"""Bridge runtime: wires robot, state, gate, event watcher and timers together."""

from __future__ import annotations

import threading
import time
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple

from naollm.admin.estop import EStop
from naollm.config import Config
from naollm.eventlog import EventLog
from naollm.robot.adapter import EventKind, RobotAdapter, RobotEvent
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

TTS_LANGUAGE = {"de": "German", "en": "English"}


class RobotNotAtRest(Exception):
    """The robot is awake or stiff, or Autonomous Life is on, with motors off."""


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
        self.motors_enabled = config.motion.motors_enabled
        self.walking = config.motion.walking_enabled and self.motors_enabled
        self.llm_enabled = True
        self.displacement_cm = 0
        self.language = lang_code(config.robot.language)
        self.keyframe_loader = keyframe_loader
        self.keyframes: Dict[str, Any] = {}
        self.registry = self._make_registry(Catalogue())
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
        self._installed_tts: Optional[List[str]] = None
        self._awake_reported = False
        self.exclusive: Optional[str] = None  # an operator flow that no turn may interrupt
        self.shutdown_hooks: List[Callable[[], None]] = []
        self.reconnect_hooks: List[Callable[[], None]] = []
        self.state.add_listener(self._on_state_change)

    # -- catalogue -----------------------------------------------------
    def _make_registry(self, catalogue: Catalogue) -> Registry:
        return Registry(self.config, catalogue, motors_enabled=self.motors_enabled,
                        perception_available=self.robot.perception.available)

    def refresh_catalogue(self) -> Catalogue:
        keyframes: Mapping[str, Any] = {}
        problems: Mapping[str, str] = {}
        if self.keyframe_loader is not None:
            keyframes, problems = self.keyframe_loader()
        self.keyframes = dict(keyframes)
        catalogue = build_catalogue(
            self.config, self.robot.motion.installed_behaviors(), self.keyframes, problems
        )
        self.registry = self._make_registry(catalogue)
        if not self.motors_enabled:
            self.log.log("robot", f"motors off: motion catalogue not used ({len(catalogue.warnings)} entries unavailable)")
            return catalogue
        for warning in catalogue.warnings:
            self.log.log("warning", warning)
        return catalogue

    # -- lifecycle (SPEC §6.3, §6.4) -------------------------------------
    def startup(self, greet: bool = True) -> None:
        self.robot.connect(self.config.robot.url)
        self._require_at_rest()
        info = self.robot.info()
        self.log.log("robot", f"connected to {info.name} (NAOqi {info.naoqi_version})")
        self._start_perception()
        if self.motors_enabled:
            self.robot.motion.wake_up()
            self.robot.motion.go_to_posture("Stand")
            self.robot.perception.set_tracking(True)
        self.refresh_catalogue()
        self._installed_tts = self.robot.speech.installed_languages()
        for needed in self._needed_tts_languages():
            if needed not in self._installed_tts:
                self.log.log("warning", f"TTS language {needed} is not installed on the robot")
        if self.config.robot.language in self._installed_tts:
            try:
                self.robot.speech.set_language(self.config.robot.language)
            except Exception as err:
                self.log.log("warning", f"TTS language switch failed: {err!r}")
        self.watcher.attach()
        self.state.transition(State.IDLE)
        try:
            self.robot.speech.set_eyes(EYE_COLOURS["white"])
        except Exception as err:
            self.log.log("error", f"eyes failed: {err!r}")
        self._state_colour = EYE_COLOURS["white"]
        if greet:
            self.greet()
        self.touch()

    def greet(self) -> None:
        try:
            self.robot.speech.say(msg("ready", self.language))
        except Exception as err:
            self.log.log("error", f"ready line failed: {err!r}")

    def mark_not_started(self) -> None:
        """Startup refused: there is no session left to shut down."""
        self._shut_down = True

    def _start_perception(self) -> None:
        """A failure leaves who_is_here refused; everything else keeps working."""
        try:
            self.robot.perception.start(self.config.privacy.face_recognition_enabled)
        except Exception as err:
            self.log.log("alert", f"PERCEPTION UNAVAILABLE: who_is_here and faces are off ({err})")

    def _needed_tts_languages(self) -> List[str]:
        needed = [TTS_LANGUAGE[code] for code in self.config.stt.languages]
        if self.config.robot.language not in needed:
            needed.append(self.config.robot.language)
        return needed

    def turn_language(self, lang: str) -> str:
        """The language of a turn, or robot.language when no voice is installed for it."""
        if self._installed_tts is None or TTS_LANGUAGE[lang] in self._installed_tts:
            return lang
        fallback = lang_code(self.config.robot.language)
        self.log.log("warning", f"no {TTS_LANGUAGE[lang]} voice installed: "
                                f"turn runs in {self.config.robot.language}")
        return fallback

    def _require_at_rest(self) -> None:
        """With motors off, stop here unless the robot is at rest. Commands nothing."""
        if self.motors_enabled:
            return
        problem = self.robot.rest_problem()
        if problem is None:
            return
        try:
            self.robot.disconnect()
        except Exception as err:
            self.log.log("error", f"disconnect failed: {err!r}")
        raise RobotNotAtRest(problem)

    def shutdown(self) -> None:
        if self._shut_down:
            return
        self._shut_down = True
        motion = self.robot.motion
        moving = [
            ("stop_behaviors", motion.stop_all_behaviors),
            ("stop_move", motion.stop_move),
            ("crouch", lambda: motion.go_to_posture("Crouch")),
            ("rest", motion.rest),
        ] if self.motors_enabled else []
        steps = [
            ("cancel_timers", self.cancel_all_timers),
            ("cancel_turn", self.cancel_turn),
            *[(f"hook_{i}", hook) for i, hook in enumerate(self.shutdown_hooks)],
            *moving,
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
        """Operator resume from a blocking state. Refused while a reason for it remains."""
        problem = self.resume_problem()
        if problem is not None:
            self.log.log("admin", f"resume refused: {problem}", source="admin")
            return False
        if not self.state.resume():
            return False
        self._awake_reported = False
        self.log.log("admin", "resumed", source="admin")
        self.touch()
        return True

    def resume_problem(self) -> Optional[str]:
        status = self.robot.events.status()
        if not status.connected:
            return "robot is not connected; type 'reconnect'"
        if status.fallen:
            return "robot still reports fallen"
        if not self.motors_enabled:
            return self.robot.rest_problem(check_life=False)
        return None

    def reconnect(self) -> None:
        """Close the session and connect again. The state stays blocked until `resume`."""
        with self.state.lock:
            if self.state.state not in (State.OFFLINE, State.STOPPED, State.FALLEN, State.RESTING):
                self.state.force(State.OFFLINE)
        self.cancel_turn()
        try:
            self.robot.disconnect()
        except Exception as err:
            self.log.log("warning", f"old session did not close cleanly: {err!r}")
        self.robot.connect(self.config.robot.url)
        self._require_at_rest()
        self._start_perception()
        self.registry = self._make_registry(self.registry.catalogue)
        for hook in self.reconnect_hooks:
            try:
                hook()
            except Exception as err:
                self.log.log("error", f"reconnect step failed: {err!r}")
        self.watcher.handle(RobotEvent(EventKind.CONNECTION_RESTORED))

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

    # -- wake-up watch (motors off) --------------------------------------
    def rest_tick(self) -> None:
        """Called every few seconds. A wake-up is reported only: `rest` would be a motor command."""
        if self.motors_enabled or self.state.state == State.OFFLINE:
            return
        try:
            problem = self.robot.rest_problem(check_life=False)
        except Exception as err:
            self.log.log("warning", f"rest check failed: {err!r}")
            return
        if problem is None:
            self._awake_reported = False
            return
        if self._awake_reported:
            return
        self._awake_reported = True
        self.log.log("alert", f"ROBOT AWAKE: {problem}. No motor command was sent. Put it at rest, then type 'resume'")
        self.estop("robot_awake")

    # -- idle crouch (SPEC §10.3) ----------------------------------------
    def idle_tick(self, now: Optional[float] = None) -> None:
        if not self.motors_enabled:
            return
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
        if self.idle_crouched and self.motors_enabled:
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
