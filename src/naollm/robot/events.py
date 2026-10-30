"""Event watcher (SPEC §10.3): reacts to robot events without the LLM."""

from __future__ import annotations

from typing import TYPE_CHECKING

from naollm.robot.adapter import EventKind, RobotEvent
from naollm.state import State
from naollm.tools.messages import msg

if TYPE_CHECKING:
    from naollm.bridge import Bridge

BATTERY_CRITICAL_PCT = 10


class EventWatcher:
    def __init__(self, bridge: "Bridge") -> None:
        self.bridge = bridge
        self._battery_critical_done = False

    def attach(self) -> None:
        self.bridge.robot.events.subscribe(self.handle)

    def handle(self, event: RobotEvent) -> None:
        try:
            self._handle(event)
        except Exception as err:
            self.bridge.log.log("error", f"event {event.kind.value} handling failed: {err!r}")

    def _handle(self, event: RobotEvent) -> None:
        b = self.bridge
        kind = event.kind
        if kind in (EventKind.MIDDLE_HEAD, EventKind.REAR_HEAD):
            b.log.log("sensor", f"{kind.value} touched")
            b.estop("touch")
        elif kind == EventKind.FRONT_HEAD:
            b.log.log("sensor", "front head touched")
            if b.state.state != State.ACTING:
                b.request_listen("touch")
        elif kind == EventKind.FALL:
            b.state.force(State.FALLEN)
            b.cancel_turn()
            self._guard(b.robot.motion.stop_all_behaviors)
            self._guard(b.robot.motion.stop_move)
            b.log.log("alert", "ROBOT FALLEN: place it upright, then type 'resume'")
        elif kind == EventKind.BATTERY:
            self._battery(int(event.value))
        elif kind == EventKind.THERMAL:
            self._thermal(event.value)
        elif kind == EventKind.CONNECTION_LOST:
            b.cancel_turn()
            b.state.force(State.OFFLINE)
            b.log.log("alert", "robot connection lost: commands stopped")
        elif kind == EventKind.CONNECTION_RESTORED:
            b.log.log("alert", "robot connection restored: type 'resume' to continue")
        elif kind == EventKind.WAKE_WORD:
            if b.state.state == State.IDLE:
                b.request_listen("wake_word")

    def _battery(self, pct: int) -> None:
        b = self.bridge
        if pct >= BATTERY_CRITICAL_PCT:
            self._battery_critical_done = False
            if pct < b.config.motion.battery_motion_min_pct:
                b.log.log("alert", f"battery {pct} %: motion limited to sit/crouch")
            return
        if self._battery_critical_done:
            return
        self._battery_critical_done = True
        b.log.log("alert", f"battery {pct} %: sitting down and resting")
        b.cancel_turn()
        motion = b.robot.motion
        self._guard(motion.stop_all_behaviors)
        self._guard(motion.stop_move)
        self._guard(lambda: motion.go_to_posture("Sit"))
        self._guard(lambda: b.robot.speech.say(msg("battery_critical", b.language), False))
        self._guard(motion.rest)
        with b.state.lock:
            if b.state.state not in (State.STOPPED, State.FALLEN, State.OFFLINE):
                b.state.force(State.RESTING)

    def _thermal(self, level: str) -> None:
        b = self.bridge
        if level == "hot":
            b.log.log("alert", "joints hot: crouching and resting")
            b.cancel_turn()
            motion = b.robot.motion
            self._guard(motion.stop_all_behaviors)
            self._guard(motion.stop_move)
            self._guard(lambda: motion.go_to_posture("Crouch"))
            self._guard(motion.rest)
            b.state.force(State.RESTING)
        elif level == "warm":
            b.log.log("alert", "joints warm: dances and steps disabled")

    def _guard(self, fn) -> None:
        try:
            fn()
        except Exception as err:
            self.bridge.log.log("error", f"event reaction failed: {err!r}")
