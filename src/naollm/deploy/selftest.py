"""The interactive self-test of `naollm init`, with no motors (SPEC §20.1 step 8).

It subscribes to robot events itself and attaches no EventWatcher, so touching the
middle or rear sensor here tests that sensor and does not run the emergency stop.
"""

from __future__ import annotations

import math
import struct
import threading
from dataclasses import dataclass
from typing import Any, Callable, List, Optional

from naollm.config import Config
from naollm.robot.adapter import EventKind, RobotEvent, WakeHit
from naollm.tools.schemas import EYE_COLOURS, STATUS_COLOURS

FAIL = "fail"
PASS = "pass"
SKIP = "skip"
MARK = {PASS: "✓", FAIL: "✗", SKIP: "-"}

TOUCH_S = 15.0
MIC_S = 3.0
WAKE_S = 20.0
MIN_MIC_DBFS = -60.0
SENTENCES = {"German": "Hallo, ich bin NAO.", "English": "Hello, I am NAO."}
TTS_LANGUAGE = {"de": "German", "en": "English"}
SENSORS = (("front", EventKind.FRONT_HEAD), ("middle", EventKind.MIDDLE_HEAD), ("rear", EventKind.REAR_HEAD))


@dataclass(frozen=True)
class Check:
    name: str
    status: str
    detail: str = ""


def rms_dbfs(pcm: bytes) -> float:
    n = len(pcm) // 2
    if not n:
        return float("-inf")
    samples = struct.unpack(f"<{n}h", pcm[:n * 2])
    rms = math.sqrt(sum(s * s for s in samples) / n)
    return 20 * math.log10(rms / 32768) if rms else float("-inf")


class SelfTest:
    def __init__(self, config: Config, robot: Any, out: Callable[[str], None],
                 ask: Callable[[str], Optional[str]], touch_s: float = TOUCH_S, mic_s: float = MIC_S,
                 wake_s: float = WAKE_S) -> None:
        self.config = config
        self.robot = robot
        self.out = out
        self.ask = ask
        self.touch_s = touch_s
        self.mic_s = mic_s
        self.wake_s = wake_s
        self.checks: List[Check] = []
        self._events: List[RobotEvent] = []
        self._arrived = threading.Condition()

    def run(self) -> List[Check]:
        self.robot.events.subscribe(self._on_event)
        for check in (self.speech, self.eyes, self.touch, self.microphone, self.wake_word, self.battery):
            try:
                check()
            except Exception as err:
                self._add(check.__name__, FAIL, f"{type(err).__name__}: {err}")
        self.robot.events.unsubscribe_all()
        return self.checks

    def _add(self, name: str, status: str, detail: str = "") -> None:
        self.checks.append(Check(name, status, detail))
        self.out(f"  {MARK[status]} {name}: {detail}")

    def _on_event(self, event: RobotEvent) -> None:
        with self._arrived:
            self._events.append(event)
            self._arrived.notify_all()

    def _confirm(self, name: str, question: str) -> None:
        answer = self.ask(f"{question} [y/n] ")
        if answer is None:
            self._add(name, SKIP, "nobody answered")
        elif answer.strip().lower() == "y":
            self._add(name, PASS, "operator confirmed")
        else:
            self._add(name, FAIL, "operator said no")

    # -- checks ----------------------------------------------------------
    def speech(self) -> None:
        languages = {TTS_LANGUAGE[c] for c in self.config.stt.languages} | {self.config.robot.language}
        for language in sorted(languages):
            name = f"speech_{language.lower()}"
            self.out(f"Listen: the robot says one sentence in {language}.")
            try:
                self.robot.speech.set_language(language)
                self.robot.speech.say(SENTENCES[language], False)
            except Exception as err:
                self._add(name, FAIL, str(err))
                continue
            self._confirm(name, f"Did you hear the {language} sentence?")

    def eyes(self) -> None:
        self.out("Watch the eyes: they turn blue, then white.")
        self.robot.speech.set_eyes(STATUS_COLOURS["blue"])
        self._confirm("eyes", "Did the eyes turn blue?")
        self.robot.speech.set_eyes(EYE_COLOURS["white"])

    def touch(self) -> None:
        for sensor, kind in SENSORS:
            self.out(f"Touch the {sensor} head sensor within {self.touch_s:g} s.")
            seen = self._wait(lambda: any(e.kind == kind for e in self._events), self.touch_s)
            self._add(f"touch_{sensor}", PASS if seen else FAIL,
                      f"{sensor} sensor reported" if seen else f"no event from the {sensor} sensor")

    def microphone(self) -> None:
        self.out(f"Speak to the robot for {self.mic_s:g} s.")
        chunks: List[bytes] = []
        self.robot.audio.start_stream(chunks.append)
        try:
            threading.Event().wait(self.mic_s)
        finally:
            self.robot.audio.stop_stream()
        pcm = b"".join(chunks)
        chunks.clear()
        if not pcm:
            self._add("microphone", FAIL, "no audio arrived; check robot.listen_ip and the firewall")
            return
        level = rms_dbfs(pcm)
        ok = level > MIN_MIC_DBFS
        self._add("microphone", PASS if ok else FAIL,
                  f"level {level:.1f} dBFS" + ("" if ok else f"; expected above {MIN_MIC_DBFS:g} dBFS"))

    def wake_word(self) -> None:
        cfg = self.config.audio
        self.robot.audio.configure_wake_words(cfg.wake_words, cfg.wake_threshold, cfg.wake_language)
        phrases = " or ".join(f"'{w}'" for w in cfg.wake_words)
        self.out(f"Say {phrases} a few times within {self.wake_s:g} s.")
        self.robot.audio.set_wake_enabled(True)
        try:
            self._wait(lambda: any(e.kind == EventKind.WAKE_WORD for e in self._events), self.wake_s)
        finally:
            self.robot.audio.set_wake_enabled(False)
        hits = [e.value for e in self._events if e.kind == EventKind.WAKE_CANDIDATE and isinstance(e.value, WakeHit)]
        heard = [f"{h.phrase} {h.confidence:.2f}" for h in hits]
        if any(h.confidence >= cfg.wake_threshold for h in hits):
            self._add("wake_word", PASS, f"heard: {', '.join(heard)}")
        else:
            self._add("wake_word", FAIL, f"nothing at or above {cfg.wake_threshold:g}; heard: {heard or 'nothing'}. "
                                         "Tune the threshold with 'ears test'")

    def battery(self) -> None:
        if not self.config.robot.battery_reliable:
            self._add("battery", SKIP, "robot.battery_reliable is false; the reading means nothing")
            return
        self._add("battery", PASS, f"{self.robot.events.status().battery_pct} %")

    def _wait(self, condition: Callable[[], bool], seconds: float) -> bool:
        with self._arrived:
            return self._arrived.wait_for(condition, timeout=seconds)
