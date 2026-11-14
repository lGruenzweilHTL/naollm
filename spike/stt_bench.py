#!/usr/bin/env python3
"""Compare Whisper models on recorded clips (spike/record_clips.py), then score the LLM on them.

For each model: load time, word error rate (WER) against the sentence that was
read, sentences right, and latency per clip (median, p90, max) with the
real-time factor (RTF: latency / clip length; below 1 is faster than speech).
Transcription goes through `SpeechToText`, as in a voice turn: CPU, int8,
beam 1, the language rules from the config.

With --llm, each clip that has an eval expectation goes to the LLM once per
distinct transcript, judged as in `naollm eval`. The "reference" row sends the
sentence as written, so it shows what the LLM does with perfect STT. A model
whose transcript matches the reference shares that result, so a difference to
the reference row comes from the transcript, not from LLM randomness. "turn"
is STT latency plus time to the LLM's first answer token: the wait before the
robot can start to speak.

WER normalises case, punctuation, British spelling and number words, so
"Twelve times seven?" and "12 times 7" count as equal.

A model named `gemma:<ollama model>`, such as `gemma:gemma4:e4b`, sends each
clip as audio input to the chat endpoint instead of Whisper (SPEC §11.2
experiment). It uses the OpenAI-style
`input_audio` content part on `llm.base_url` from --config, asks for the
transcript only, and is scored with the same WER and latency. Its "load" is the
time of the first, unscored request.

Raw clips (record_clips.py --raw) are cut here first, as a voice turn cuts
them: `UtteranceDetector` with the VAD settings from the config, or the ones
given with --aggressiveness, --silence, --start and --pre-roll (try the best
row from spike/vad_tune.py before you change the code). So one raw recording
serves both the VAD sweep and this benchmark. A raw clip the VAD drops is
listed with the reason and left out of the table: no speech found, an utterance shorter than the voice turn
accepts, or a noise-only clip in which the VAD started an utterance (a false
start). Noise-only clips are never transcribed.

Usage:
  .venv/bin/python spike/stt_bench.py spike/clips
  .venv/bin/python spike/stt_bench.py spike/clips --models small,gemma:gemma4:e4b --llm
  .venv/bin/python spike/stt_bench.py spike/clips --models small,distil-small.en --llm -v
  .venv/bin/python spike/stt_bench.py spike/clips --tag far --llm --run "gemma4:e2b, reasoning off"
  .venv/bin/python spike/stt_bench.py spike/clips/raw --models base,small,large-v3-turbo
  .venv/bin/python spike/stt_bench.py spike/clips/raw --aggressiveness 3 --silence 0.6
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import gc
import io
import json
import urllib.error
import urllib.request
import re
import statistics
import sys
import time
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from naollm.audio.listener import MIN_UTTERANCE_S  # noqa: E402
from naollm.audio.stt import SpeechToText  # noqa: E402
from naollm.audio.vad import (  # noqa: E402
    PRE_ROLL_FRAMES, SPEECH_START_FRAMES, VAD_AGGRESSIVENESS, End, UtteranceDetector, webrtc_speech_test,
)
from naollm.config import load_config  # noqa: E402

# Multilingual only: the bridge allows German and English.
DEFAULT_MODELS = "base,small,medium,large-v3-turbo"
DEFAULT_RUN = "gemma4:e4b, reasoning off"
RATE = 16000

NUMBERS = {w: str(i) for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen "
    "fifteen sixteen seventeen eighteen nineteen twenty".split())}
NUMBERS.update({"thirty": "30", "forty": "40", "fifty": "50", "sixty": "60", "seventy": "70",
                "eighty": "80", "ninety": "90", "hundred": "100"})
# German number words, so "zwölf" and "12" count as equal.
NUMBERS.update({w: str(i) for i, w in enumerate(
    "null eins zwei drei vier fünf sechs sieben acht neun zehn elf zwölf dreizehn vierzehn "
    "fünfzehn sechzehn siebzehn achtzehn neunzehn zwanzig".split())})
NUMBERS.update({"ein": "1", "eine": "1", "dreißig": "30", "vierzig": "40", "fünfzig": "50", "sechzig": "60",
                "siebzig": "70", "achtzig": "80", "neunzig": "90", "hundert": "100"})
SPELLING = {"favourite": "favorite", "colour": "color", "colours": "colors", "metre": "meter",
            "metres": "meters", "centimetres": "centimeters", "okay": "ok"}


# -- text ----------------------------------------------------------------
def normalise(text: str) -> List[str]:
    text = text.lower().replace("-", " ").replace("’", "'")
    words = re.sub(r"[^\w\s']", " ", text).split()
    return [NUMBERS.get(w, SPELLING.get(w, w)) for w in (w.strip("'") for w in words) if w]


def edit_distance(ref: List[str], hyp: List[str]) -> int:
    row = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, 1):
        prev, row[0] = row[0], i
        for j, h in enumerate(hyp, 1):
            prev, row[j] = row[j], min(row[j] + 1, row[j - 1] + 1, prev + (r != h))
    return row[-1]


# -- data ----------------------------------------------------------------
@dataclass
class Clip:
    file: Path
    text: str
    lang: str
    seconds: float
    pcm: bytes
    tag: str = ""
    profile: Optional[str] = None
    expect: Optional[Dict[str, Any]] = None


@dataclass
class Result:
    clip: Clip
    text: str
    latency: float
    errors: int
    words: int


@dataclass
class ModelRun:
    name: str
    load_s: float = 0.0
    results: List[Result] = field(default_factory=list)
    skipped: str = ""


@dataclass
class VadSetting:
    aggressiveness: int
    silence_s: float
    start_frames: int
    pre_roll_frames: int

    def describe(self) -> str:
        return (f"VAD aggressiveness {self.aggressiveness}, silence {self.silence_s:g} s, "
                f"start {self.start_frames}, pre-roll {self.pre_roll_frames}")


def cut_raw(pcm: bytes, vad: VadSetting, audio_cfg) -> Tuple[Optional[bytes], str]:
    """The utterance a voice turn would take from a raw stream, or None and why it would drop it.

    A raw clip stops where the reader pressed Enter. When the VAD has not ended by
    then, the utterance so far is taken, as more silence would have ended it.
    """
    detector = UtteranceDetector(webrtc_speech_test(vad.aggressiveness), vad.silence_s, audio_cfg.max_utterance_s,
                                 audio_cfg.no_speech_timeout_s, vad.start_frames, vad.pre_roll_frames)
    detector.feed(pcm)
    if detector.end == End.NO_SPEECH or not detector.audio:
        return None, "no speech found"
    if detector.speech_s < MIN_UTTERANCE_S:
        return None, f"utterance {detector.speech_s:.2f} s, shorter than {MIN_UTTERANCE_S} s"
    return detector.audio, ""


def read_wav(path: Path) -> bytes:
    with wave.open(str(path), "rb") as w:
        if (w.getframerate(), w.getnchannels(), w.getsampwidth()) != (RATE, 1, 2):
            raise SystemExit(f"{path}: need 16 kHz mono 16-bit")
        return w.readframes(w.getnframes())


def load_clips(folders: List[Path], tag: Optional[str], vad: VadSetting, audio_cfg
               ) -> Tuple[List[Clip], List[Tuple[str, str]]]:
    """Cut clips as recorded, raw clips cut by the VAD, and the dropped raw clips with the reason."""
    clips: List[Clip] = []
    dropped: List[Tuple[str, str]] = []
    for folder in folders:
        manifest = folder / "clips.yaml"
        if not manifest.exists():
            raise SystemExit(f"{manifest} not found; record clips with spike/record_clips.py")
        for item in yaml.safe_load(manifest.read_text(encoding="utf-8")) or []:
            if tag and item.get("tag") != tag:
                continue
            path = folder / item["file"]
            pcm = read_wav(path)
            if item.get("raw"):
                cut, why = cut_raw(pcm, vad, audio_cfg)
                if not item.get("text"):  # noise only: any utterance is a false start
                    if cut is not None:
                        dropped.append((path.name, "noise only, but the VAD started an utterance"))
                    continue
                if cut is None:
                    dropped.append((path.name, why))
                    continue
                pcm = cut
            clips.append(Clip(path, item["text"], item.get("lang", "en"), len(pcm) / 2 / RATE, pcm,
                              item.get("tag", ""), item.get("profile"), item.get("expect")))
    return clips, dropped


# -- STT -----------------------------------------------------------------
class BenchEngine:
    """`WhisperEngine` with a choice of beam size and thread count."""

    def __init__(self, name: str, beam: int, threads: int) -> None:
        from faster_whisper import WhisperModel

        self._model = WhisperModel(name, device="cpu", compute_type="int8", cpu_threads=threads)
        self.beam = beam

    def transcribe(self, pcm: bytes, language: Optional[str]):
        import numpy as np

        audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
        return self._model.transcribe(audio, language=language, beam_size=self.beam,
                                      condition_on_previous_text=False)


def bench_model(name: str, clips: List[Clip], stt_cfg, beam: int, threads: int, verbose: bool) -> ModelRun:
    from faster_whisper import download_model

    run = ModelRun(name)
    if name.endswith(".en") and list(stt_cfg.languages) != ["en"]:
        run.skipped = f"English-only, but stt.languages is {stt_cfg.languages}"
        return run
    print(f"\n=== {name}", flush=True)
    t = time.monotonic()
    download_model(name)
    if time.monotonic() - t > 2:
        print(f"  downloaded in {time.monotonic() - t:.0f} s")
    t = time.monotonic()
    engine = BenchEngine(name, beam, threads)
    run.load_s = time.monotonic() - t
    stt = SpeechToText(stt_cfg.model_copy(update={"model": name}), engine)
    stt.transcribe(clips[0].pcm)  # warm-up; not counted
    for clip in clips:
        t = time.monotonic()
        text = stt.transcribe(clip.pcm).text
        latency = time.monotonic() - t
        ref = normalise(clip.text)
        errors = edit_distance(ref, normalise(text))
        run.results.append(Result(clip, text, latency, errors, len(ref)))
        if verbose or errors:
            mark = "  ok " if not errors else f"  {errors:>2}x"
            print(f"{mark} {latency:5.2f}s  {clip.file.name}  {text!r}" + ("" if not errors else f"  (said {clip.text!r})"))
    del stt, engine
    gc.collect()
    return run


# -- Gemma audio input ------------------------------------------------------
GEMMA_PREFIX = "gemma:"
GEMMA_PROMPT = {
    "en": "Transcribe this audio exactly, in English. Reply with the transcript only.",
    "de": "Schreib genau auf, was in dieser Aufnahme gesagt wird, auf Deutsch. Antworte nur mit dem Text.",
}
GEMMA_TIMEOUT_S = 180.0


def wav_bytes(pcm: bytes) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(pcm)
    return buffer.getvalue()


def gemma_transcribe(base_url: str, model: str, pcm: bytes, lang: str) -> str:
    """One chat request with the clip as `input_audio`. Raises RuntimeError with the endpoint's answer."""
    body = {
        "model": model,
        "temperature": 0,
        "reasoning_effort": "none",
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": GEMMA_PROMPT.get(lang, GEMMA_PROMPT["en"])},
            {"type": "input_audio", "input_audio": {"data": base64.b64encode(wav_bytes(pcm)).decode(),
                                                    "format": "wav"}},
        ]}],
    }
    request = urllib.request.Request(base_url.rstrip("/") + "/chat/completions", json.dumps(body).encode(),
                                     {"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=GEMMA_TIMEOUT_S) as response:
            data = json.loads(response.read())
    except urllib.error.HTTPError as err:
        raise RuntimeError(f"HTTP {err.code}: {err.read().decode(errors='replace')[:300]}") from None
    return (data["choices"][0]["message"].get("content") or "").strip()


def bench_gemma(name: str, clips: List[Clip], base_url: str, verbose: bool) -> ModelRun:
    model = name[len(GEMMA_PREFIX):]
    run = ModelRun(name)
    print(f"\n=== {name} (audio input at {base_url})", flush=True)
    t = time.monotonic()
    try:
        gemma_transcribe(base_url, model, clips[0].pcm, clips[0].lang)  # loads the model; not counted
    except (RuntimeError, OSError, KeyError) as err:
        run.skipped = f"no audio input: {err}"
        return run
    run.load_s = time.monotonic() - t
    for clip in clips:
        t = time.monotonic()
        try:
            text = gemma_transcribe(base_url, model, clip.pcm, clip.lang)
        except (RuntimeError, OSError, KeyError) as err:
            text = ""
            print(f"  error {clip.file.name}: {err}")
        latency = time.monotonic() - t
        ref = normalise(clip.text)
        errors = edit_distance(ref, normalise(text))
        run.results.append(Result(clip, text, latency, errors, len(ref)))
        if verbose or errors:
            mark = "  ok " if not errors else f"  {errors:>2}x"
            print(f"{mark} {latency:5.2f}s  {clip.file.name}  {text!r}" + ("" if not errors else f"  (said {clip.text!r})"))
    return run


# -- LLM -----------------------------------------------------------------
def score_llm(runs: List[ModelRun], clips: List[Clip], args) -> Tuple[Dict[Tuple, Any], str]:
    """Outcomes keyed by (clip file, transcript). Each distinct (case, transcript) goes once."""
    from naollm.eval import (EVAL_TIMEOUT_S, Case, Progress, _warm_up, evaluate, load_eval_config)
    from naollm.config import parse_config
    from naollm.llm.client import OpenAIChatClient

    matches = [(label, cfg) for label, cfg in load_eval_config(args.eval_config) if args.run in label]
    if not matches:
        raise SystemExit(f"no run in {args.eval_config} matches {args.run!r}")
    label, base = matches[0]
    llm = parse_config(base).llm
    wanted: Dict[Tuple, Case] = {}
    for clip in clips:
        if clip.expect is None:
            continue
        texts = [clip.text] + [r.text for run in runs for r in run.results if r.clip is clip]
        for text in texts:
            key = (clip.profile, clip.lang, text, json.dumps(clip.expect, sort_keys=True))
            wanted.setdefault(key, Case(clip.profile, text, dict(clip.expect), clip.lang))
    cases = list(wanted.values())
    if not cases:
        raise SystemExit("--llm needs clips with an eval expectation; none of these clips has one")
    # The first request per tool set fills Ollama's prompt cache and takes seconds longer.
    # The bridge pays that once at start-up, so one unscored copy per profile goes first.
    primes = {c.profile: Case(c.profile, c.prompt, c.expect, c.lang) for c in reversed(cases)}
    cases = list(primes.values()) + cases
    print(f"\n=== LLM: {label}, {len(cases) - len(primes)} distinct transcripts "
          f"(+{len(primes)} unscored to fill the prompt cache)", flush=True)
    progress = Progress(len(cases), verbose=args.verbose)
    held: List[Any] = []

    def factory(cfg):
        return OpenAIChatClient(cfg.llm.model_copy(update={"timeout_s": EVAL_TIMEOUT_S}), held[0] if held else None)

    async def main():
        residency = await _warm_up(llm, progress)
        if residency is not None:
            held.append(residency)
        return await evaluate(cases, base, args.profiles, factory, progress)

    try:
        report = asyncio.run(main())
    finally:
        if held and held[0].held:
            held[0].unload()
    by_case = {id(o.case): o for o in report.all()}
    return {key: by_case[id(case)] for key, case in wanted.items() if id(case) in by_case}, label


def outcome_for(outcomes, clip: Clip, text: str):
    return outcomes.get((clip.profile, clip.lang, text, json.dumps(clip.expect, sort_keys=True)))


# -- report --------------------------------------------------------------
def p90(values: List[float]) -> float:
    ordered = sorted(values)
    return ordered[max(0, -(-len(ordered) * 9 // 10) - 1)]


def table(header: List[str], rows: List[List[str]]) -> None:
    widths = [max(len(r[i]) for r in [header] + rows) for i in range(len(header))]
    for row in [header] + rows:
        print("  " + "  ".join(c.ljust(w) if i == 0 else c.rjust(w)
                               for i, (c, w) in enumerate(zip(row, widths))).rstrip())


def report(runs: List[ModelRun], clips: List[Clip], outcomes: Optional[Dict], llm_label: str) -> None:
    audio = sum(c.seconds for c in clips)
    print(f"\nSTT on {len(clips)} clips ({audio:.0f} s of audio, median clip "
          f"{statistics.median(c.seconds for c in clips):.1f} s)")
    header = ["model", "load", "WER", "right", "median", "p90", "max", "RTF"]
    if outcomes is not None:
        header += ["tool calls", "turn median", "turn p90"]
    rows = []
    if outcomes is not None:
        ref = [outcome_for(outcomes, c, c.text) for c in clips if c.expect is not None]
        ref = [o for o in ref if o is not None]
        firsts = [o.timing.first_answer for o in ref if o.timing.first_answer is not None]
        rows.append(["reference (as written)", "-", "-", "-", "-", "-", "-", "-",
                     f"{sum(o.passed for o in ref)}/{len(ref)}",
                     f"{statistics.median(firsts):.1f}s" if firsts else "-",
                     f"{p90(firsts):.1f}s" if firsts else "-"])
    for run in runs:
        if run.skipped:
            rows.append([run.name, f"skipped: {run.skipped}"] + [""] * (len(header) - 2))
            continue
        lat = [r.latency for r in run.results]
        words = sum(r.words for r in run.results)
        row = [run.name, f"{run.load_s:.1f}s", f"{sum(r.errors for r in run.results) / max(words, 1):.1%}",
               f"{sum(not r.errors for r in run.results)}/{len(run.results)}",
               f"{statistics.median(lat):.2f}s", f"{p90(lat):.2f}s", f"{max(lat):.2f}s",
               f"{statistics.median(r.latency / r.clip.seconds for r in run.results):.2f}"]
        if outcomes is not None:
            scored = [(r, outcome_for(outcomes, r.clip, r.text)) for r in run.results if r.clip.expect is not None]
            scored = [(r, o) for r, o in scored if o is not None]
            turns = [r.latency + o.timing.first_answer for r, o in scored if o.timing.first_answer is not None]
            row += [f"{sum(o.passed for _, o in scored)}/{len(scored)}",
                    f"{statistics.median(turns):.1f}s" if turns else "-",
                    f"{p90(turns):.1f}s" if turns else "-"]
        rows.append(row)
    table(header, rows)
    if outcomes is not None:
        print(f"LLM: {llm_label}. turn = STT latency + LLM time to first answer token.")
        failed = []
        for run in runs:
            for r in run.results:
                o = outcome_for(outcomes, r.clip, r.text) if r.clip.expect is not None else None
                ref_o = outcome_for(outcomes, r.clip, r.clip.text) if o is not None else None
                if o is not None and ref_o is not None and o.passed != ref_o.passed:
                    failed.append(f"  {run.name}: {r.text!r} -> {o.detail}  "
                                  f"(as written: {'pass' if ref_o.passed else 'fail'})")
        if failed:
            print("transcripts that changed the tool-call result:")
            print("\n".join(failed))


def write_json(path: Path, runs: List[ModelRun], outcomes: Optional[Dict]) -> None:
    out = []
    for run in runs:
        items = []
        for r in run.results:
            o = outcome_for(outcomes, r.clip, r.text) if outcomes and r.clip.expect is not None else None
            items.append({"file": r.clip.file.name, "said": r.clip.text, "heard": r.text,
                          "latency_s": round(r.latency, 3), "errors": r.errors, "words": r.words,
                          "tool_call_passed": None if o is None else o.passed,
                          "first_answer_s": None if o is None else o.timing.first_answer})
        out.append({"model": run.name, "load_s": round(run.load_s, 2), "skipped": run.skipped, "clips": items})
    path.write_text(json.dumps(out, indent=2, ensure_ascii=False))
    print(f"wrote {path}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("clips", nargs="*", type=Path, default=[ROOT / "spike/clips"],
                    help="folders with clips.yaml (default spike/clips)")
    ap.add_argument("--models", default=DEFAULT_MODELS, help=f"comma list (default {DEFAULT_MODELS})")
    ap.add_argument("--config", default=str(ROOT / "config.yaml"), help="stt.languages and fallback_language")
    ap.add_argument("--tag", help="only clips with this tag")
    ap.add_argument("--beam", type=int, default=1, help="beam size; the bridge uses 1")
    ap.add_argument("--threads", type=int, default=0, help="CPU threads for Whisper; 0 is the library default")
    ap.add_argument("--llm", action="store_true", help="also score tool calls on the transcripts")
    ap.add_argument("--eval-config", default=str(ROOT / "tests/eval/eval_config.yaml"))
    ap.add_argument("--run", default=DEFAULT_RUN, help=f"eval matrix run label to use (default {DEFAULT_RUN!r})")
    ap.add_argument("--profiles", type=Path, default=ROOT / "tests/eval/profiles")
    ap.add_argument("--json", type=Path, help="write every transcript and timing here")
    ap.add_argument("-v", "--verbose", action="store_true", help="print every transcript, not only wrong ones")
    ap.add_argument("--aggressiveness", type=int, choices=[0, 1, 2, 3],
                    help=f"raw clips: webrtcvad mode; default VAD_AGGRESSIVENESS ({VAD_AGGRESSIVENESS})")
    ap.add_argument("--silence", type=float, help="raw clips: seconds of silence that end; default audio.vad_silence_s")
    ap.add_argument("--start", type=int, help=f"raw clips: speech frames that start; default {SPEECH_START_FRAMES}")
    ap.add_argument("--pre-roll", type=int, help=f"raw clips: frames kept before the start; default {PRE_ROLL_FRAMES}")
    args = ap.parse_args()

    config = load_config(args.config)
    stt_cfg = config.stt
    vad = VadSetting(
        VAD_AGGRESSIVENESS if args.aggressiveness is None else args.aggressiveness,
        config.audio.vad_silence_s if args.silence is None else args.silence,
        SPEECH_START_FRAMES if args.start is None else args.start,
        PRE_ROLL_FRAMES if args.pre_roll is None else args.pre_roll)
    clips, dropped = load_clips(args.clips, args.tag, vad, config.audio)
    if dropped:
        print(f"raw clips cut with {vad.describe()}: {len(dropped)} dropped, not in the table below")
        for name, why in dropped:
            print(f"  {name}: {why}")
    if not clips:
        print("no clips", file=sys.stderr)
        return 2
    print(f"{len(clips)} clips, languages {stt_cfg.languages}, beam {args.beam}")
    runs = [bench_gemma(m.strip(), clips, config.llm.base_url, args.verbose) if m.strip().startswith(GEMMA_PREFIX)
            else bench_model(m.strip(), clips, stt_cfg, args.beam, args.threads, args.verbose)
            for m in args.models.split(",") if m.strip()]
    outcomes, label = (score_llm(runs, clips, args) if args.llm else (None, ""))
    report(runs, clips, outcomes, label)
    if args.json:
        write_json(args.json, runs, outcomes)
    return 0


if __name__ == "__main__":
    sys.exit(main())
