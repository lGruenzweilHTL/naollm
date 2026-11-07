#!/usr/bin/env python3
"""Record test clips for the Whisper benchmark (spike/stt_bench.py). Motor-free.

Shows one sentence at a time. Press Enter, hear the listening beep, read the
sentence out. The clip is cut exactly as a voice turn cuts it: the same
`UtteranceDetector`, with the VAD settings from the config, on the robot's
front-mic stream. Each clip is saved as a 16 kHz mono 16-bit WAV, and
`clips.yaml` in the output folder records the sentence that was read, plus the
eval expectation when the sentence comes from tests/eval/tool_calls.yaml.

With --raw, each clip is the whole stream instead: from the listening beep
until you press Enter, with no cut. Read the sentence, stay quiet for about
2 s, then press Enter. spike/vad_tune.py replays these clips through the VAD
with other settings. Key n records a clip in which nobody speaks, to test
false starts on fan and room noise. The cut the current VAD would make is
printed and stored with each raw clip.

Sources:
  robot   the robot's front mic, as `naollm run` hears it (needs the robot)
  laptop  this laptop's default mic through `arecord`, to try things out

The robot source uses only ALAudioDevice and ALAudioPlayer (the beep). It sends
no motor commands.

The clips are voice recordings. Record only people who agreed, keep the folder
out of git (spike/clips/ is ignored), and delete it when the tuning is done.

Usage:
  .venv/bin/python spike/record_clips.py --source robot --tag near
  .venv/bin/python spike/record_clips.py --source laptop --tag laptop --limit 3
  .venv/bin/python spike/record_clips.py --source robot --raw --tag near
Keys at each sentence: Enter record, s skip, q quit (with --raw also n: record
noise only). After a clip: Enter keep, r redo, p play it back (laptop
speakers), d discard.
"""

from __future__ import annotations

import argparse
import queue
import subprocess
import sys
import threading
import time
import wave
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from naollm.audio.vad import FRAME_BYTES, FRAME_MS, RATE, End, UtteranceDetector, webrtc_speech_test  # noqa: E402
from naollm.audio.listener import MIN_UTTERANCE_S  # noqa: E402
from naollm.config import load_config  # noqa: E402

DEFAULT_PROMPTS = [ROOT / "tests/eval/tool_calls.yaml", ROOT / "spike/clip_prompts.yaml"]
# A stream that delivers nothing for this long past the no-speech timeout is dead.
WATCHDOG_MARGIN_S = 2.0
CHUNK_BYTES = 1365 * 2  # what the robot sends per callback


# -- sources -------------------------------------------------------------
class RobotSource:
    def __init__(self, url: str, listen_ip: Optional[str]) -> None:
        from naollm.robot.qi_adapter import QiRobot

        self.robot = QiRobot(listen_ip=listen_ip, log=lambda kind, msg: print(f"  [{kind}] {msg}"))
        print(f"connecting to {url}")
        self.robot.connect(url)

    def beep(self) -> None:
        self.robot.audio.play_listen_sound()
        time.sleep(0.15)  # let the beep finish, so it is not in the clip

    def start(self, on_chunk: Callable[[bytes], None]) -> None:
        self.robot.audio.start_stream(on_chunk)

    def stop(self) -> None:
        self.robot.audio.stop_stream()

    def close(self) -> None:
        self.stop()
        self.robot.disconnect()


class LaptopSource:
    def __init__(self, device: Optional[str]) -> None:
        self.device = device
        self._proc: Optional[subprocess.Popen] = None
        self._thread: Optional[threading.Thread] = None

    def beep(self) -> None:
        sys.stdout.write("\a")
        sys.stdout.flush()

    def start(self, on_chunk: Callable[[bytes], None]) -> None:
        cmd = ["arecord", "-q", "-t", "raw", "-f", "S16_LE", "-r", str(RATE), "-c", "1"]
        if self.device:
            cmd += ["-D", self.device]
        self._proc = subprocess.Popen(cmd, stdout=subprocess.PIPE)
        proc = self._proc

        def pump() -> None:
            assert proc.stdout is not None
            while True:
                chunk = proc.stdout.read(CHUNK_BYTES)
                if not chunk:
                    return
                on_chunk(chunk)

        self._thread = threading.Thread(target=pump, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._proc is not None:
            self._proc.terminate()
            self._proc.wait()
            self._proc = None

    def close(self) -> None:
        self.stop()


# -- recording -----------------------------------------------------------
def capture(source, cfg_audio) -> Optional[UtteranceDetector]:
    """One utterance, cut like a voice turn. None when the stream stopped delivering."""
    detector = UtteranceDetector(webrtc_speech_test(), cfg_audio.vad_silence_s,
                                 cfg_audio.max_utterance_s, cfg_audio.no_speech_timeout_s)
    chunks: "queue.Queue[bytes]" = queue.Queue()
    source.beep()
    source.start(chunks.put)
    limit = cfg_audio.no_speech_timeout_s + cfg_audio.max_utterance_s + WATCHDOG_MARGIN_S
    started = time.monotonic()
    got_any = False
    try:
        while time.monotonic() - started < limit:
            try:
                chunk = chunks.get(timeout=0.25)
            except queue.Empty:
                if not got_any and time.monotonic() - started > cfg_audio.no_speech_timeout_s + WATCHDOG_MARGIN_S:
                    return None
                continue
            got_any = True
            if detector.feed(chunk) is not None:
                return detector
        return None
    finally:
        source.stop()


def capture_raw(source, prompt: str) -> Optional[bytes]:
    """The whole stream from the beep until Enter. None when the source delivered nothing."""
    chunks: List[bytes] = []
    source.beep()
    source.start(chunks.append)
    try:
        ask(prompt)
    finally:
        source.stop()
    return b"".join(chunks) or None


def current_cut(pcm: bytes, cfg_audio) -> Tuple[Optional[End], Optional[float], float]:
    """What the configured VAD does with a raw clip: end, its time in s (None: still open), speech s."""
    detector = UtteranceDetector(webrtc_speech_test(), cfg_audio.vad_silence_s,
                                 cfg_audio.max_utterance_s, cfg_audio.no_speech_timeout_s)
    for i in range(0, len(pcm) - FRAME_BYTES + 1, FRAME_BYTES):
        if detector.feed(pcm[i:i + FRAME_BYTES]) is not None:
            return detector.end, (i // FRAME_BYTES + 1) * FRAME_MS / 1000, detector.speech_s
    return None, None, detector.speech_s


def save_wav(path: Path, pcm: bytes) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(pcm)


def play(path: Path) -> None:
    subprocess.run(["aplay", "-q", str(path)], check=False)


def load_prompts(paths: List[Path], languages: List[str]) -> List[Dict[str, Any]]:
    """Entries with prompt, and optional profile, lang and expect. Only the allowed languages."""
    out = []
    for path in paths:
        for item in yaml.safe_load(path.read_text(encoding="utf-8")) or []:
            lang = item.get("lang", "de")
            if lang not in languages:
                continue
            entry = {"text": item["prompt"], "lang": lang}
            if "expect" in item:
                entry["profile"] = item.get("profile", "event_default")
                entry["expect"] = item["expect"]
            out.append(entry)
    return out


def ask(question: str) -> str:
    try:
        return input(question).strip().lower()
    except EOFError:
        return "q"


def record_raw(source, cfg_audio, args, manifest: List[Dict[str, Any]], manifest_path: Path,
               entry: Dict[str, Any], stem: str) -> str:
    """Records raw clips for one entry until one is kept. Returns kept, skip or q."""
    noise = entry.get("noise", False)
    prompt = (f"  stay quiet (noise only); press Enter after about {cfg_audio.no_speech_timeout_s + 1:g} s: " if noise
              else "  read the sentence, stay quiet about 2 s, then press Enter: ")
    while True:
        print("  recording ...")
        pcm = capture_raw(source, prompt)
        if pcm is None:
            print("  no audio from the source")
            key = ask("  r retry, s skip, q quit: ")
            if key == "r":
                continue
            return key if key == "q" else "skip"
        name = f"{stem}{'-noise' if noise else ''}.wav"
        path = args.out / name
        save_wav(path, pcm)
        seconds = len(pcm) / 2 / RATE
        end, end_s, speech_s = current_cut(pcm, cfg_audio)
        if end is None:
            print(f"  clip {seconds:.1f} s; current VAD has not ended yet (stream stopped first)")
        else:
            dropped = end != End.NO_SPEECH and speech_s < MIN_UTTERANCE_S
            print(f"  clip {seconds:.1f} s; current VAD ends by {end.value} at {end_s:.2f} s, speech {speech_s:.1f} s"
                  + (" (too short: a voice turn drops it)" if dropped else ""))
        key = ask("  Enter keep, r redo, p play, d discard: ")
        while key == "p":
            play(path)
            key = ask("  Enter keep, r redo, p play, d discard: ")
        if key in ("", "k"):
            clip = {"file": name, **entry, "raw": True, "source": args.source, "tag": args.tag,
                    "speaker": args.speaker, "clip_s": round(seconds, 2),
                    "vad_end": end.value if end else "open",
                    "vad_end_s": round(end_s, 2) if end_s is not None else None}
            manifest.append(clip)
            manifest_path.write_text(yaml.safe_dump(manifest, sort_keys=False, allow_unicode=True))
            return "kept"
        path.unlink(missing_ok=True)
        if key == "q":
            return "q"
        if key == "d":
            return "skip"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default=str(ROOT / "config.yaml"), help="robot URL, languages and VAD settings")
    ap.add_argument("--source", choices=["robot", "laptop"], default="robot")
    ap.add_argument("--robot", help="robot URL; default robot.url from the config")
    ap.add_argument("--device", help="laptop source: ALSA device for arecord, e.g. plughw:0,7")
    ap.add_argument("--prompts", action="append", type=Path,
                    help="YAML list of {prompt, lang?, profile?, expect?}; repeatable. "
                         "Default: tests/eval/tool_calls.yaml and spike/clip_prompts.yaml")
    ap.add_argument("--raw", action="store_true",
                    help="save the whole stream from the beep until Enter, not the cut utterance "
                         "(for spike/vad_tune.py)")
    ap.add_argument("--out", type=Path,
                    help="folder for the WAVs and clips.yaml; a later run adds to it. "
                         "Default spike/clips, or spike/clips/raw with --raw")
    ap.add_argument("--tag", default="", help="condition label stored with each clip: near, far, noise, ...")
    ap.add_argument("--speaker", default="", help="speaker label stored with each clip (no real names)")
    ap.add_argument("--limit", type=int, help="record at most this many sentences")
    args = ap.parse_args()

    cfg = load_config(args.config)
    prompts = load_prompts(args.prompts or DEFAULT_PROMPTS, list(cfg.stt.languages))
    if args.limit:
        prompts = prompts[:args.limit]
    if not prompts:
        print(f"no sentences in the allowed languages {cfg.stt.languages}", file=sys.stderr)
        return 2
    if args.out is None:
        args.out = ROOT / ("spike/clips/raw" if args.raw else "spike/clips")
    args.out.mkdir(parents=True, exist_ok=True)
    manifest_path = args.out / "clips.yaml"
    manifest: List[Dict[str, Any]] = yaml.safe_load(manifest_path.read_text()) if manifest_path.exists() else []
    manifest = manifest or []

    a = cfg.audio
    print(f"VAD: silence {a.vad_silence_s:g} s, max {a.max_utterance_s:g} s, no-speech {a.no_speech_timeout_s:g} s")
    source = (RobotSource(args.robot or cfg.robot.url, cfg.robot.listen_ip) if args.source == "robot"
              else LaptopSource(args.device))
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    kept = 0
    try:
        for i, entry in enumerate(prompts, 1):
            print(f"\n[{i}/{len(prompts)}]  \"{entry['text']}\"")
            key = ask("  Enter record, n noise only, s skip, q quit: " if args.raw
                      else "  Enter record, s skip, q quit: ")
            if key == "q":
                break
            if key == "s":
                continue
            if args.raw:
                noise = key == "n"
                key = record_raw(source, a, args, manifest, manifest_path,
                                 {"text": "", "noise": True} if noise else entry, f"{stamp}-{i:02d}")
                kept += key == "kept"
                if key == "q":
                    break
                continue
            while True:
                print("  listening ...")
                detector = capture(source, a)
                if detector is None:
                    print("  no audio from the source")
                    key = ask("  r retry, s skip, q quit: ")
                elif detector.end == End.NO_SPEECH:
                    print("  nobody spoke")
                    key = ask("  r retry, s skip, q quit: ")
                else:
                    name = f"{stamp}-{i:02d}.wav"
                    path = args.out / name
                    save_wav(path, detector.audio)
                    seconds = len(detector.audio) / 2 / RATE
                    print(f"  ended by {detector.end.value}: speech {detector.speech_s:.1f} s, clip {seconds:.1f} s")
                    key = ask("  Enter keep, r redo, p play, d discard: ")
                    while key == "p":
                        play(path)
                        key = ask("  Enter keep, r redo, p play, d discard: ")
                    if key in ("", "k"):
                        clip = {"file": name, **entry, "source": args.source, "tag": args.tag,
                                "speaker": args.speaker, "end": detector.end.value,
                                "speech_s": round(detector.speech_s, 2), "clip_s": round(seconds, 2)}
                        manifest.append(clip)
                        manifest_path.write_text(yaml.safe_dump(manifest, sort_keys=False, allow_unicode=True))
                        kept += 1
                        break
                    path.unlink(missing_ok=True)
                    if key == "d":
                        break
                    if key == "q":
                        raise KeyboardInterrupt
                    key = "r"
                if key != "r":
                    break
            if key == "q":
                break
    except KeyboardInterrupt:
        print()
    finally:
        source.close()
    print(f"\nkept {kept} clips; {len(manifest)} in {manifest_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
