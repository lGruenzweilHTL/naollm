"""Bridge runtime: wires robot, state, gate, event watcher and timers together."""

from __future__ import annotations

import threading
import time
from typing import Any, Callable, Dict, Mapping, Optional, Tuple

from naollm.admin.estop import EStop
from naollm.config import Config
from naollm.eventlog import EventLog
from naollm.robot.adapter import RobotAdapter
from naollm.robot.events import EventWatcher
from naollm.state import State, StateMachine
from naollm.tools.gate import Gate
from naollm.tools.messages import lang_code, msg
from naollm.tools.registry import Catalogue, Registry, build_catalogue
from naollm.tools.schemas import EYE_COLOURS, STATUS_COLOURS

STATE_EYES = {
    State.IDLE: EYE_COLOURS["white"],
    State.LISTENING: STATUS_COLOURS["blue"],
    State.THINKING: STATUS_COLOURS["yellow"],
    State.STOPPED: STATUS_COLOURS["red"],
    State.FALLEN: STATUS_COLOURS["red"],
}

KeyframeLoader = Callable[[], Tuple[Mapping[str, Any], Mapping[str, str]]]


class Bridge:
    def __init__(
        self,
        config: Config,
        robot: RobotAdapter,
        log: Optional[EventLog] = None,
        keyframe_loader: Optional[KeyframeLoader] = None,
    ) -> None:
        self.config = config
        self.robot = robot
        self.log = log or EventLog(event_mode=config.event_mode)
        self.state = StateMachine()
        self.walking = config.motion.walking_enabled
        self.llm_enabled = True
        self.displacement_cm = 0
        self.language = lang_code(config.robot.language)
        self.keyframe_loader = keyframe_loader
        self.keyframes: Dict[str, Any] = {}
        self.registry = Registry(config, Catalogue())
        self.gate = Gate(self)
        self.estop_routine = EStop(self)
        self.watcher = EventWatcher(self)
        self.look_hold_s = 3.0
        self.eyes_hold_s = 5.0
        self.last_activity = time.monotonic()
        self.idle_crouched = False
        self.listen_handler: Optional[Callable[[str], None]] = None
        self._turn_canceller: Optional[Callable[[], None]] = None
        self._timers: Dict[str, threading.Timer] = {}
        self._timer_lock = threading.Lock()
        self._shut_down = False
        self._state_colour: Optional[str] = None
        self.state.add_listener(self._on_state_change)

    # -- catalogue -----------------------------------------------------
    def refresh_catalogue(self) -> Catalogue:
        keyframes: Mapping[str, Any] = {}
        problems: Mapping[str, str] = {}
        if self.keyframe_loader is not None:
            keyframes, problems = self.keyframe_loader()
        self.keyframes = dict(keyframes)
        catalogue = build_catalogue(
            self.config, self.robot.motion.installed_behaviors(), self.keyframes, problems
        )
        self.registry = Registry(self.config, catalogue)
        for warning in catalogue.warnings:
            self.log.log("warning", warning)
        return catalogue

    # -- lifecycle (SPEC §6.3, §6.4) -------------------------------------
    def startup(self, greet: bool = True) -> None:
        self.robot.connect(self.config.robot.url)
        info = self.robot.info()
        self.log.log("robot", f"connected to {info.name} (NAOqi {info.naoqi_version})")
        self.robot.motion.wake_up()
        self.robot.motion.go_to_posture("Stand")
        self.robot.perception.set_tracking(True)
        self.refresh_catalogue()
        installed = self.robot.speech.installed_languages()
        for needed in ("German", "English"):
            if needed not in installed:
                self.log.log("warning", f"TTS language {needed} is not installed on the robot")
        self.watcher.attach()
        self.state.transition(State.IDLE)
        self.robot.speech.set_eyes(EYE_COLOURS["white"])
        self._state_colour = EYE_COLOURS["white"]
        if greet:
            self.robot.speech.say(msg("ready", self.language))
        self.touch()

    def shutdown(self) -> None:
        if self._shut_down:
            return
        self._shut_down = True
        motion = self.robot.motion
        steps = [
            ("cancel_timers", self.cancel_all_timers),
            ("cancel_turn", self.cancel_turn),
            ("stop_behaviors", motion.stop_all_behaviors),
            ("stop_move", motion.stop_move),
            ("crouch", lambda: motion.go_to_posture("Crouch")),
            ("rest", motion.rest),
            ("unsubscribe", self.robot.events.unsubscribe_all),
            ("disconnect", self.robot.disconnect),
        ]
        for name, step in steps:
            try:
                step()
            except Exception as err:
                self.log.log("error", f"shutdown step {name} failed: {err!r}")
        self.state.force(State.OFFLINE)
        self.log.log("robot", "shutdown complete")

    # -- control -------------------------------------------------------
    def estop(self, source: str) -> None:
        self.estop_routine(source)

    def resume(self) -> bool:
        """Operator resume from STOPPED/FALLEN/RESTING. Refused while still fallen."""
        if self.robot.events.status().fallen:
            self.log.log("admin", "resume refused: robot still reports fallen", source="admin")
            return False
        if not self.state.resume():
            return False
        self.log.log("admin", "resumed", source="admin")
        self.touch()
        return True

    def set_walking(self, enabled: bool) -> None:
        self.walking = enabled
        if enabled:
            self.displacement_cm = 0
        self.log.log("admin", f"walking {'ON' if enabled else 'OFF'}", source="admin")

    def register_turn(self, canceller: Optional[Callable[[], None]]) -> None:
        self._turn_canceller = canceller

    def cancel_turn(self) -> None:
        canceller = self._turn_canceller
        if canceller is not None:
            canceller()

    def request_listen(self, source: str) -> None:
        if self.listen_handler is not None:
            self.listen_handler(source)
        else:
            self.log.log("listen", "listening not available yet (audio input arrives with phase 3)",
                         source=source)

    def touch(self) -> None:
        self.last_activity = time.monotonic()

    # -- idle crouch (SPEC §10.3) ----------------------------------------
    def idle_tick(self, now: Optional[float] = None) -> None:
        now = time.monotonic() if now is None else now
        if self.state.state != State.IDLE or self.idle_crouched:
            return
        if now - self.last_activity < self.config.motion.idle_crouch_s:
            return
        if self.robot.events.status().posture != "Stand":
            return
        result = self.gate.check_and_run("set_posture", {"posture": "Crouch"}, source="idle")
        if result.ok:
            self.idle_crouched = True

    def before_turn(self) -> None:
        if self.idle_crouched:
            self.idle_crouched = False
            self.gate.check_and_run("set_posture", {"posture": "Stand"}, source="idle")
        self.touch()

    # -- eyes and timers -----------------------------------------------
    def _on_state_change(self, old: State, new: State) -> None:
        colour = STATE_EYES.get(new)
        if colour is not None and colour != self._state_colour:
            self._state_colour = colour
            try:
                self.robot.speech.set_eyes(colour)
            except Exception as err:
                self.log.log("error", f"eyes failed: {err!r}")
        self.log.log("state", f"{old.value} -> {new.value}")

    def set_status_eyes_red(self) -> None:
        self._state_colour = STATUS_COLOURS["red"]
        self.robot.speech.set_eyes(STATUS_COLOURS["red"])

    def restore_eyes(self) -> None:
        colour = STATE_EYES.get(self.state.state, EYE_COLOURS["white"])
        self._state_colour = colour
        self.robot.speech.set_eyes(colour)

    def schedule(self, key: str, delay_s: float, fn: Callable[[], None]) -> None:
        def run() -> None:
            with self._timer_lock:
                if self._timers.get(key) is not timer:
                    return
                del self._timers[key]
            try:
                fn()
            except Exception as err:
                self.log.log("error", f"timer {key} failed: {err!r}")

        timer = threading.Timer(delay_s, run)
        timer.daemon = True
        with self._timer_lock:
            old = self._timers.pop(key, None)
            if old is not None:
                old.cancel()
            self._timers[key] = timer
        timer.start()

    def cancel_timer(self, key: str) -> None:
        with self._timer_lock:
            timer = self._timers.pop(key, None)
        if timer is not None:
            timer.cancel()

    def cancel_all_timers(self) -> None:
        with self._timer_lock:
            timers = list(self._timers.values())
            self._timers.clear()
        for t in timers:
            t.cancel()
