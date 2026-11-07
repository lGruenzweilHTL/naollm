#!/usr/bin/env python3
"""Tune the VAD on raw robot clips (spike/record_clips.py --raw). Offline, motor-free.

Each raw clip is the whole stream from the listening beep until the reader
pressed Enter. This script replays every clip through `UtteranceDetector`, as
a voice turn would, once for each combination of these settings:

  aggressiveness  webrtcvad mode 0..3 (code: VAD_AGGRESSIVENESS)
  silence         seconds of non-speech that end the utterance (audio.vad_silence_s)
  start           speech frames in a row that start the utterance (code: SPEECH_START_FRAMES)
  pre-roll        frames kept from before the start (code: PRE_ROLL_FRAMES)

max_utterance_s and no_speech_timeout_s come from the config and are not swept.

Where the speech really is comes from Whisper word timestamps on the whole raw
clip (cached in vad_ref.json next to clips.yaml). Words before the first or
after the last word of the read sentence are dropped, so a hallucinated
"Thank you." in the silence does not move the end. To fix a bad reference,
put speech_start and speech_end (seconds) on the clip in clips.yaml.

Outcome per clip and setting (a frame is 30 ms; tolerance 0.1 s):
  ok      ended by silence after the last word, with the first word inside the cut
  early   ended before the last word was over (a pause in the sentence, or noise
          that started the cut and ended it before the speech)
  head    the cut starts after the first word: the first syllable is lost
  miss    no speech found before the no-speech timeout
  short   speech under 0.5 s: the listener drops it
  long    ended by the max length
  false   a noise-only clip (key n) that would start a turn
  open    the raw clip ends before the detector decides; not scored but
          ranked like an error, so a setting cannot win by leaving clips
          undecided. Record a longer tail
"wait" is the end of the cut minus the end of the last word: how long the
reader waits after speaking before speech to text starts. Settings rank by
errors plus open (err counts every outcome except ok and open), then by median wait, then by
fewest changes from the current setting, which is the row marked *.

With --wer K, the cuts of the best K settings and the current one go through
speech to text (the configured model) and are scored by word error rate (WER)
against the read sentence. This is the end-to-end check: a cut that loses a
word shows here even when the reference timestamps are off.

Usage:
  .venv/bin/python spike/vad_tune.py
  .venv/bin/python spike/vad_tune.py spike/clips/raw --tag noise -v
  .venv/bin/python spike/vad_tune.py --silence 0.4,0.6,0.8 --aggressiveness 2,3 --wer 5
"""

from __future__ import annotations

import argparse
import itertools
import json
import statistics
import sys
import wave
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "spike"))

from naollm.audio.listener import MIN_UTTERANCE_S  # noqa: E402
from naollm.audio.stt import SpeechToText  # noqa: E402
from naollm.audio.vad import (  # noqa: E402
    FRAME_BYTES, FRAME_MS, PRE_ROLL_FRAMES, RATE, SPEECH_START_FRAMES, VAD_AGGRESSIVENESS, End,
    UtteranceDetector, webrtc_speech_test,
)
from naollm.config import load_config  # noqa: E402
from stt_bench import edit_distance, normalise  # noqa: E402

FRAME_S = FRAME_MS / 1000
TOLERANCE_S = 0.1
ERRORS = ("early", "head", "miss", "short", "long", "false")
DUMMY_FRAME = bytes(FRAME_BYTES)


# -- data ----------------------------------------------------------------
@dataclass
class Clip:
    file: Path
    text: str
    lang: Optional[str]
    pcm: bytes
    tag: str = ""
    speech: Optional[Tuple[float, float]] = None  # reference start and end, seconds
    ref_text: str = ""
    flags: Dict[int, List[bool]] = field(default_factory=dict)  # per aggressiveness, one per frame

    @property
    def noise(self) -> bool:
        return not self.text

    @property
    def seconds(self) -> float:
        return len(self.pcm) / 2 / RATE


@dataclass(frozen=True)
class Setting:
    aggressiveness: int
    silence_s: float
    start_frames: int
    pre_roll_frames: int

    def label(self) -> str:
        return f"{self.aggressiveness}  {self.silence_s:4.2f}  {self.start_frames:5d}  {self.pre_roll_frames:7d}"

    def describe(self) -> str:
        return (f"aggressiveness {self.aggressiveness}, silence {self.silence_s:g} s, "
                f"start {self.start_frames} frames, pre-roll {self.pre_roll_frames} frames")

    def distance(self, other: "Setting") -> int:
        return sum(a != b for a, b in zip(vars(self).values(), vars(other).values()))


@dataclass
class Cut:
    outcome: str
    end: Optional[End] = None
    start_s: float = 0.0
    end_s: float = 0.0
    wait_s: Optional[float] = None


def load_clips(folders: List[Path], tag: Optional[str]) -> List[Clip]:
    clips = []
    for folder in folders:
        manifest = folder / "clips.yaml"
        if not manifest.exists():
            raise SystemExit(f"{manifest} not found; record raw clips with spike/record_clips.py --raw")
        for item in yaml.safe_load(manifest.read_text(encoding="utf-8")) or []:
            if not item.get("raw") or (tag and item.get("tag") != tag):
                continue
            path = folder / item["file"]
            with wave.open(str(path), "rb") as w:
                if (w.getframerate(), w.getnchannels(), w.getsampwidth()) != (RATE, 1, 2):
                    raise SystemExit(f"{path}: need 16 kHz mono 16-bit")
                pcm = w.readframes(w.getnframes())
            clip = Clip(path, item.get("text", ""), item.get("lang"), pcm, item.get("tag", ""))
            if "speech_start" in item and "speech_end" in item:
                clip.speech = (float(item["speech_start"]), float(item["speech_end"]))
                clip.ref_text = "(set in clips.yaml)"
            clips.append(clip)
    return clips


# -- reference -----------------------------------------------------------
def trim_to_sentence(words: List[Tuple[str, float, float]], text: str) -> List[Tuple[str, float, float]]:
    """Drops words before the first and after the last word of the read sentence."""
    wanted = set(normalise(text))

    def known(word: str) -> bool:
        return any(w in wanted for w in normalise(word))

    while words and not known(words[0][0]):
        words = words[1:]
    while words and not known(words[-1][0]):
        words = words[:-1]
    return words


def add_reference(clips: List[Clip], model_name: str, cache_path: Path) -> None:
    cache: Dict[str, Any] = json.loads(cache_path.read_text()) if cache_path.exists() else {}
    todo = [c for c in clips if not c.noise and c.speech is None
            and f"{model_name}:{c.file.name}" not in cache]
    if todo:
        import numpy as np
        from faster_whisper import WhisperModel

        print(f"reference: word timestamps for {len(todo)} clips with {model_name} ...", flush=True)
        model = WhisperModel(model_name, device="cpu", compute_type="int8")
        for clip in todo:
            audio = np.frombuffer(clip.pcm, dtype=np.int16).astype(np.float32) / 32768.0
            segments, _ = model.transcribe(audio, language=clip.lang, beam_size=5, word_timestamps=True,
                                           condition_on_previous_text=False)
            words = [(w.word.strip(), float(w.start), float(w.end)) for s in segments for w in (s.words or [])]
            cache[f"{model_name}:{clip.file.name}"] = words
        cache_path.write_text(json.dumps(cache, indent=1, ensure_ascii=False))
    for clip in clips:
        if clip.noise or clip.speech is not None:
            continue
        words = trim_to_sentence([tuple(w) for w in cache[f"{model_name}:{clip.file.name}"]], clip.text)
        if words:
            clip.speech = (words[0][1], words[-1][2])
            clip.ref_text = " ".join(w[0] for w in words)


# -- replay --------------------------------------------------------------
def add_flags(clips: List[Clip], levels: List[int]) -> None:
    for clip in clips:
        frames = range(0, len(clip.pcm) - FRAME_BYTES + 1, FRAME_BYTES)
        for level in levels:
            test = webrtc_speech_test(level)
            clip.flags[level] = [test(clip.pcm[i:i + FRAME_BYTES]) for i in frames]


def replay(clip: Clip, setting: Setting, audio_cfg) -> Tuple[Optional[End], int, int, float]:
    """End, first and end frame of the cut (end exclusive), speech s. End None: still open."""
    flags = iter(clip.flags[setting.aggressiveness])
    detector = UtteranceDetector(lambda _frame: next(flags), setting.silence_s, audio_cfg.max_utterance_s,
                                 audio_cfg.no_speech_timeout_s, start_frames=setting.start_frames,
                                 pre_roll_frames=setting.pre_roll_frames)
    for n in range(len(clip.flags[setting.aggressiveness])):
        if detector.feed(DUMMY_FRAME) is not None:
            end_frame = n + 1
            return detector.end, end_frame - len(detector.audio) // FRAME_BYTES, end_frame, detector.speech_s
    return None, 0, 0, detector.speech_s


def judge(clip: Clip, setting: Setting, audio_cfg) -> Cut:
    end, first, last, speech_s = replay(clip, setting, audio_cfg)
    if end is None:
        return Cut("open")
    cut = Cut("", end, first * FRAME_S, last * FRAME_S)
    if clip.noise:
        cut.outcome = "false" if end != End.NO_SPEECH and speech_s >= MIN_UTTERANCE_S else "ok"
        return cut
    if end == End.NO_SPEECH:
        cut.outcome = "miss"
    elif end == End.MAX_LENGTH:
        cut.outcome = "long"
    elif speech_s < MIN_UTTERANCE_S:
        cut.outcome = "short"
    elif clip.speech is not None and cut.end_s < clip.speech[1] - TOLERANCE_S:
        cut.outcome = "early"
    elif clip.speech is not None and cut.start_s > clip.speech[0] + TOLERANCE_S:
        cut.outcome = "head"
    else:
        cut.outcome = "ok"
        if clip.speech is not None:
            cut.wait_s = cut.end_s - clip.speech[1]
    return cut


@dataclass
class Score:
    setting: Setting
    cuts: List[Cut]
    counts: Counter
    errors: int
    wait_median: float
    wait_p90: float


def score(setting: Setting, clips: List[Clip], audio_cfg) -> Score:
    cuts = [judge(c, setting, audio_cfg) for c in clips]
    counts = Counter(c.outcome for c in cuts)
    waits = sorted(c.wait_s for c in cuts if c.wait_s is not None)
    median = statistics.median(waits) if waits else float("inf")
    p90 = waits[min(len(waits) - 1, int(0.9 * len(waits)))] if waits else float("inf")
    return Score(setting, cuts, counts, sum(counts[e] for e in ERRORS), median, p90)


# -- word error rate -----------------------------------------------------
def wer_of(s: Score, clips: List[Clip], stt: SpeechToText, cache: Dict[Tuple[str, int, int], str]
           ) -> Tuple[float, int, int]:
    """WER over the speech clips, sentences right, speech clips scored."""
    errors = words = right = scored = 0
    for clip, cut in zip(clips, s.cuts):
        if clip.noise or cut.outcome == "open":
            continue
        ref = normalise(clip.text)
        if cut.outcome == "miss":
            hyp: List[str] = []
        else:
            first, last = round(cut.start_s / FRAME_S), round(cut.end_s / FRAME_S)
            key = (clip.file.name, first, last)
            if key not in cache:
                cache[key] = stt.transcribe(clip.pcm[first * FRAME_BYTES:last * FRAME_BYTES]).text
            hyp = normalise(cache[key])
        e = edit_distance(ref, hyp)
        errors += e
        words += len(ref)
        right += e == 0
        scored += 1
    return (errors / words if words else 0.0), right, scored


# -- output --------------------------------------------------------------
def floats(text: str) -> List[float]:
    return [float(x) for x in text.split(",") if x.strip()]


def ints(text: str) -> List[int]:
    return [int(x) for x in text.split(",") if x.strip()]


def fmt_s(value: float) -> str:
    return "   -" if value == float("inf") else f"{value:4.2f}"


HEADER = ("    agg  sil  start  preroll   err | " + " ".join(f"{k:>5}" for k in ("ok",) + ERRORS)
          + " | wait med  p90 | open")


def row(s: Score, current: Setting) -> str:
    mark = "*" if s.setting == current else " "
    outcomes = " ".join(f"{s.counts[k]:5d}" for k in ("ok",) + ERRORS)
    return (f"  {mark} {s.setting.label()}  {s.errors:4d} | {outcomes} |     {fmt_s(s.wait_median)} "
            f"{fmt_s(s.wait_p90)} | {s.counts['open']:4d}")


def details(s: Score, clips: List[Clip]) -> None:
    print(f"\n  clips for {s.setting.describe()}")
    for clip, cut in zip(clips, s.cuts):
        ref = f"speech {clip.speech[0]:.2f}-{clip.speech[1]:.2f}" if clip.speech else (
            "noise" if clip.noise else "no reference")
        if cut.end is None:
            where = "stream ended first"
        elif cut.end == End.NO_SPEECH:
            where = f"no speech by {cut.end_s:.2f}"
        else:
            where = f"cut {cut.start_s:.2f}-{cut.end_s:.2f} by {cut.end.value}"
        wait = f" wait {cut.wait_s:.2f}" if cut.wait_s is not None else ""
        print(f"    {cut.outcome:5}  {clip.file.name}  {ref}  {where}{wait}  \"{clip.text or '-'}\"")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("folders", nargs="*", type=Path, default=[ROOT / "spike/clips/raw"],
                    help="folders with raw clips and clips.yaml (default spike/clips/raw)")
    ap.add_argument("--config", default=str(ROOT / "config.yaml"), help="max length, no-speech timeout, STT")
    ap.add_argument("--tag", help="only clips with this tag")
    ap.add_argument("--aggressiveness", default="0,1,2,3")
    ap.add_argument("--silence", default="0.4,0.5,0.6,0.7,0.8,1.0,1.2", help="seconds")
    ap.add_argument("--start", default="2,3,4,6", help="speech frames in a row (30 ms each)")
    ap.add_argument("--pre-roll", default="5,10,15", help="frames (30 ms each)")
    ap.add_argument("--ref-model", help="Whisper model for the word timestamps; default stt.model")
    ap.add_argument("--top", type=int, default=15, help="settings to show")
    ap.add_argument("--wer", type=int, default=0, metavar="K",
                    help="speech to text on the cuts of the best K settings and the current one")
    ap.add_argument("-v", "--verbose", action="store_true", help="per-clip outcomes for the best and current")
    args = ap.parse_args()

    cfg = load_config(args.config)
    a = cfg.audio
    clips = load_clips(args.folders, args.tag)
    if not clips:
        print("no raw clips; record them with spike/record_clips.py --raw", file=sys.stderr)
        return 2
    current = Setting(VAD_AGGRESSIVENESS, a.vad_silence_s, SPEECH_START_FRAMES, PRE_ROLL_FRAMES)
    levels = sorted(set(ints(args.aggressiveness)) | {current.aggressiveness})
    grid = {Setting(*combo) for combo in itertools.product(
        levels, sorted(set(floats(args.silence)) | {current.silence_s}),
        sorted(set(ints(args.start)) | {current.start_frames}),
        sorted(set(ints(args.pre_roll)) | {current.pre_roll_frames}))}

    ref_model = args.ref_model or cfg.stt.model
    for folder in args.folders:
        add_reference([c for c in clips if c.file.parent == folder], ref_model, folder / "vad_ref.json")
    speech = [c for c in clips if not c.noise]
    print(f"{len(speech)} speech clips, {len(clips) - len(speech)} noise clips; "
          f"max {a.max_utterance_s:g} s, no-speech {a.no_speech_timeout_s:g} s; reference {ref_model}")
    for clip in speech:
        if clip.speech is None:
            print(f"  no reference for {clip.file.name}: Whisper found none of the sentence; "
                  "only miss, short and long are scored", file=sys.stderr)
        elif args.verbose:
            print(f"  {clip.file.name}  {clip.speech[0]:.2f}-{clip.speech[1]:.2f}  \"{clip.ref_text}\"")
    tail_needed = max(s.silence_s for s in grid) + TOLERANCE_S
    for clip in speech:
        if clip.speech is not None and clip.seconds - clip.speech[1] < tail_needed:
            print(f"  short tail on {clip.file.name}: {clip.seconds - clip.speech[1]:.2f} s after the last word, "
                  f"long silence settings show open", file=sys.stderr)

    add_flags(clips, levels)
    scores = sorted((score(s, clips, a) for s in grid),
                    key=lambda s: (s.errors + s.counts["open"], s.wait_median, s.wait_p90, s.setting.distance(current)))
    best = scores[:args.top]
    now = next(s for s in scores if s.setting == current)
    print(f"\n{len(grid)} settings, best {len(best)}; wait in s\n{HEADER}")
    for s in best:
        print(row(s, current))
    if now not in best:
        print(f"  ...\n{row(now, current)}  (rank {scores.index(now) + 1})")

    if args.verbose:
        details(scores[0], clips)
        if now is not scores[0]:
            details(now, clips)

    if args.wer:
        stt = SpeechToText(cfg.stt)
        print(f"\nspeech to text ({cfg.stt.model}) on the cuts", flush=True)
        stt.ensure_loaded()
        cache: Dict[Tuple[str, int, int], str] = {}
        chosen = scores[:args.wer] + ([now] if now not in scores[:args.wer] else [])
        print("    agg  sil  start  preroll    WER  right")
        for s in chosen:
            wer, right, scored = wer_of(s, clips, stt, cache)
            mark = "*" if s.setting == current else " "
            print(f"  {mark} {s.setting.label()}  {wer:5.1%}  {right}/{scored}", flush=True)

    print("\nVAD_AGGRESSIVENESS, SPEECH_START_FRAMES and PRE_ROLL_FRAMES are in src/naollm/audio/vad.py; "
          "the silence is audio.vad_silence_s in the config.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
