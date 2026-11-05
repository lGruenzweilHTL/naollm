"""In-memory fake robot for tests and `--dry-run` (SPEC §14.1, §14.2)."""

from __future__ import annotations

import threading
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from naollm.robot.adapter import (
    HEAD_SPEED,
    POSTURE_SPEED,
    EventKind,
    Keyframes,
    Outcome,
    RobotEvent,
    RobotInfo,
    RobotStatus,
    ThermalLevel,
    WakeHit,
)

# Simulated durations in seconds, multiplied by `time_scale`.
BEHAVIOR_S = 6.0
ANIMATION_S = 2.0
POSTURE_S = 1.5
WALK_M_PER_S = 0.1
TURN_RAD_PER_S = 0.6
SAY_S_PER_CHAR = 0.06
STREAM_RATE = 16000
STREAM_CHUNK_SAMPLES = 1365  # the size the real robot sends

# Reference NAO 6 limits (min_rad, max_rad, max_velocity_rad_s) for the fake.
_FAKE_LIMITS: Dict[str, Tuple[float, float, float]] = {
    "HeadYaw": (-2.0857, 2.0857, 8.27),
    "HeadPitch": (-0.6720, 0.5149, 7.19),
}


@dataclass(frozen=True)
class Action:
    name: str
    args: Dict[str, Any] = field(default_factory=dict)

    def __str__(self) -> str:
        inner = ", ".join(f"{k}={v!r}" for k, v in self.args.items())
        return f"{self.name}({inner})"


class FakeRobot:
    """Implements every sub-interface of `RobotAdapter` in one object."""

    def __init__(
        self,
        installed_behaviors: Optional[List[str]] = None,
        time_scale: float = 1.0,
        printer: Optional[Callable[[str], None]] = None,
    ) -> None:
        self.motion = self
        self.speech = self
        self.audio = self
        self.events = self
        self.perception = self

        self.time_scale = time_scale
        self.printer = printer
        self.actions: List[Action] = []
        self._lock = threading.RLock()
        self._handlers: List[Callable[[RobotEvent], None]] = []
        self._active: List[threading.Event] = []
        self._moving = 0

        # Simulated state, settable by tests and the dry-run operator.
        self.connected = False
        self.posture = "Crouch"
        self.stiff = False
        self.battery_pct = 100
        self.thermal: ThermalLevel = "ok"
        self.fallen = False
        self.behaviors: List[str] = list(installed_behaviors or [])
        self.languages: List[str] = ["German", "English"]
        self.language = "German"
        self.eyes = "#FFFFFF"
        self.tracking = False
        self.balancer = False
        self.people = 0
        self.faces: List[str] = []         # learned on the robot
        self.seen_faces: List[str] = []    # recognized right now
        self.learn_results: List[Any] = [] # one per learn_face: True, False or an exception
        self.recognition = False
        self.limits: Dict[str, Tuple[float, float, float]] = dict(_FAKE_LIMITS)
        self.fail_on: Dict[str, Exception] = {}
        self.autonomous_life_state = "disabled"
        self.asr_languages: List[str] = ["German", "English"]
        self.wake_words: List[str] = []
        self.wake_threshold = 0.5
        self.wake_language: Optional[str] = None
        self.wake_enabled = False
        self.stream_active = False
        self.stream_audio = b""
        self._stream_stop: Optional[threading.Event] = None
        self.available = True

    # -- recording -----------------------------------------------------
    def _record(self, _action: str, **args: Any) -> None:
        action = Action(_action, args)
        with self._lock:
            self.actions.append(action)
        if self.printer:
            self.printer(f"[robot] {action}")
        failure = self.fail_on.get(_action)
        if failure is not None:
            raise failure

    def action_names(self) -> List[str]:
        with self._lock:
            return [a.name for a in self.actions]

    def clear_actions(self) -> None:
        with self._lock:
            self.actions.clear()

    # -- long actions --------------------------------------------------
    def _run_long(self, seconds: float) -> Outcome:
        done = threading.Event()
        with self._lock:
            self._active.append(done)
            self._moving += 1
        try:
            interrupted = done.wait(max(0.0, seconds * self.time_scale))
        finally:
            with self._lock:
                self._active.remove(done)
                self._moving -= 1
        return "interrupted" if interrupted else "finished"

    def _interrupt_all(self) -> None:
        with self._lock:
            for ev in self._active:
                ev.set()

    # -- connection ----------------------------------------------------
    def connect(self, url: str) -> None:
        self._record("connect", url=url)
        self.connected = True

    def disconnect(self) -> None:
        self._record("disconnect")
        self.connected = False

    def info(self) -> RobotInfo:
        return RobotInfo(naoqi_version="2.8.7-fake", robot_id="fake-nao", name="FakeNAO")

    def rest_problem(self, check_life: bool = True) -> Optional[str]:
        if self.stiff:
            return "the robot is awake or stiff"
        if check_life and self.autonomous_life_state != "disabled":
            return f"Autonomous Life is {self.autonomous_life_state}; run 'naollm init'"
        return None

    def autonomous_life(self) -> str:
        return self.autonomous_life_state

    def disable_autonomous_life(self) -> None:
        self._record("disable_autonomous_life")
        self.autonomous_life_state = "disabled"

    # -- Motion --------------------------------------------------------
    def run_behavior(self, path: str) -> Outcome:
        self._record("run_behavior", path=path)
        if path not in self.behaviors:
            raise RuntimeError(f"behavior not installed: {path}")
        return self._run_long(BEHAVIOR_S)

    def stop_all_behaviors(self) -> None:
        self._record("stop_all_behaviors")
        self._interrupt_all()

    def installed_behaviors(self) -> List[str]:
        return list(self.behaviors)

    def play_animation(self, path: str) -> Outcome:
        self._record("play_animation", path=path)
        return self._run_long(ANIMATION_S)

    def play_keyframes(self, frames: Keyframes) -> Outcome:
        self._record("play_keyframes", joints=list(frames.names), duration_s=frames.duration_s)
        return self._run_long(frames.duration_s)

    def set_balancer(self, enabled: bool) -> None:
        self._record("set_balancer", enabled=enabled)
        self.balancer = enabled

    def go_to_posture(self, name: str, speed: float = POSTURE_SPEED) -> bool:
        self._record("go_to_posture", name=name)
        if self.fallen:
            return False
        outcome = self._run_long(POSTURE_S)
        if outcome == "finished" or name == "Crouch":
            self.posture = name
        return outcome == "finished"

    def move_to(self, x_m: float, y_m: float, theta_rad: float) -> Outcome:
        self._record("move_to", x_m=round(x_m, 3), y_m=round(y_m, 3), theta_rad=round(theta_rad, 3))
        seconds = max((x_m**2 + y_m**2) ** 0.5 / WALK_M_PER_S, abs(theta_rad) / TURN_RAD_PER_S)
        return self._run_long(seconds)

    def stop_move(self) -> None:
        self._record("stop_move")
        self._interrupt_all()

    def set_head(self, yaw_rad: float, pitch_rad: float, speed: float = HEAD_SPEED) -> None:
        self._record("set_head", yaw_rad=round(yaw_rad, 3), pitch_rad=round(pitch_rad, 3))

    def joint_limits(self) -> Dict[str, Tuple[float, float, float]]:
        return dict(self.limits)

    def rest(self) -> None:
        self._record("rest")
        self.stiff = False
        if not self.fallen:
            self.posture = "Crouch"

    def wake_up(self) -> None:
        self._record("wake_up")
        self.stiff = True

    # -- Speech --------------------------------------------------------
    def say(self, text: str, animated: bool = True) -> None:
        self._record("say", text=text, animated=animated)
        self._run_long(len(text) * SAY_S_PER_CHAR)

    def stop_speech(self) -> None:
        self._record("stop_speech")

    def set_language(self, language: str) -> None:
        self._record("set_language", language=language)
        if language not in self.languages:
            raise RuntimeError(f"language not installed: {language}")
        self.language = language

    def installed_languages(self) -> List[str]:
        return list(self.languages)

    def set_eyes(self, rgb_hex: str, duration_s: float = 0.2) -> None:
        self._record("set_eyes", rgb=rgb_hex)
        self.eyes = rgb_hex

    # -- Audio ---------------------------------------------------------
    def load_wav(self, path: str | Path) -> None:
        """Play this 16 kHz mono 16-bit file into the next stream. Dry run only."""
        try:
            with wave.open(str(path), "rb") as w:
                if (w.getframerate(), w.getnchannels(), w.getsampwidth()) != (STREAM_RATE, 1, 2):
                    raise ValueError(f"{path}: need 16 kHz mono 16-bit, got {w.getframerate()} Hz, "
                                     f"{w.getnchannels()} channels, {w.getsampwidth() * 8} bit")
                self.stream_audio = w.readframes(w.getnframes())
        except (OSError, EOFError, wave.Error) as err:
            raise ValueError(f"cannot read {path}: {err}") from None

    def start_stream(self, on_chunk: Callable[[bytes], None]) -> None:
        self._record("start_stream")
        self.stop_stream_quietly()
        stop = threading.Event()
        audio, self.stream_audio = self.stream_audio, b""
        self._stream_stop = stop
        self.stream_active = True
        threading.Thread(target=self._feed_stream, args=(audio, on_chunk, stop), daemon=True).start()

    def _feed_stream(self, audio: bytes, on_chunk: Callable[[bytes], None], stop: threading.Event) -> None:
        """Sends the audio in the robot's chunk size, then silence until the stream stops."""
        size = STREAM_CHUNK_SAMPLES * 2
        delay = STREAM_CHUNK_SAMPLES / STREAM_RATE * self.time_scale
        offset = 0
        while not stop.wait(delay):
            chunk = audio[offset:offset + size]
            offset += size
            on_chunk(chunk + b"\0" * (size - len(chunk)))

    def stop_stream_quietly(self) -> None:
        if self._stream_stop is not None:
            self._stream_stop.set()
            self._stream_stop = None
        self.stream_active = False

    def stop_stream(self) -> None:
        self._record("stop_stream")
        self.stop_stream_quietly()

    def configure_wake_words(self, words: List[str], threshold: float, language: str) -> None:
        self._record("configure_wake_words", words=list(words), threshold=threshold, language=language)
        if language not in self.asr_languages:
            raise RuntimeError(f"ASR language not installed: {language}")
        self.wake_words = list(words)
        self.wake_threshold = threshold
        self.wake_language = language

    def set_wake_enabled(self, enabled: bool) -> None:
        self._record("set_wake_enabled", enabled=enabled)
        self.wake_enabled = enabled

    def wake_languages(self) -> List[str]:
        return list(self.asr_languages)

    def play_listen_sound(self) -> None:
        self._record("play_listen_sound")

    def hear_wake_phrase(self, phrase: str, confidence: float) -> None:
        """Simulate the on-robot engine hearing a phrase. Nothing arrives while it is paused."""
        if not self.wake_enabled:
            return
        hit = WakeHit(phrase, confidence)
        self.inject(EventKind.WAKE_CANDIDATE, hit)
        if confidence >= self.wake_threshold:
            self.inject(EventKind.WAKE_WORD, hit)

    # -- Events --------------------------------------------------------
    def subscribe(self, handler: Callable[[RobotEvent], None]) -> None:
        with self._lock:
            self._handlers.append(handler)

    def unsubscribe_all(self) -> None:
        with self._lock:
            self._handlers.clear()

    def status(self) -> RobotStatus:
        with self._lock:
            moving = self._moving > 0
        return RobotStatus(
            battery_pct=self.battery_pct,
            posture=self.posture,
            fallen=self.fallen,
            thermal=self.thermal,
            moving=moving,
            connected=self.connected,
        )

    def inject(self, kind: EventKind, value: Any = None) -> None:
        """Simulate a robot event; handlers run in the calling thread."""
        if kind == EventKind.BATTERY:
            self.battery_pct = int(value)
        elif kind == EventKind.THERMAL:
            self.thermal = value
        elif kind == EventKind.FALL:
            self.fallen = True
            self.posture = "Unknown"
        elif kind == EventKind.CONNECTION_LOST:
            self.connected = False
        elif kind == EventKind.CONNECTION_RESTORED:
            self.connected = True
        with self._lock:
            handlers = list(self._handlers)
        event = RobotEvent(kind, value)
        for handler in handlers:
            handler(event)

    # -- Perception ----------------------------------------------------
    def set_tracking(self, enabled: bool) -> None:
        self._record("set_tracking", enabled=enabled)
        self.tracking = enabled

    def start(self, recognition: bool) -> None:
        self._record("perception_start", recognition=recognition)
        self.recognition = recognition

    def people_count(self) -> int:
        return self.people

    def recognized_names(self) -> List[str]:
        return list(self.seen_faces) if self.recognition else []

    def learned_names(self) -> List[str]:
        self._record("learned_names")
        return list(self.faces)

    def learn_face(self, name: str) -> bool:
        self._record("learn_face", name=name)
        result = self.learn_results.pop(0) if self.learn_results else True
        if isinstance(result, Exception):
            raise result
        if result:
            self.faces.append(name)
        return result

    def forget_face(self, name: str) -> bool:
        self._record("forget_face", name=name)
        if name in self.faces:
            self.faces.remove(name)
            return True
        return False

    def forget_all_faces(self) -> bool:
        self._record("forget_all_faces")
        self.faces.clear()
        return True
