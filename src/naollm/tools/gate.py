"""Safety gate (SPEC §10.2): every tool call, from the LLM or the operator."""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Dict, Optional

from naollm.robot.adapter import RobotStatus
from naollm.state import State
from naollm.tools.handlers import HANDLERS
from naollm.tools.messages import msg
from naollm.tools.schemas import LED, MOTION, STATUS_COLOURS, STOP, ArgError, ToolSpec, check_args

if TYPE_CHECKING:
    from naollm.bridge import Bridge

# Reasons that mean the model produced an unusable call (SPEC §7.2).
MALFORMED = frozenset({
    "unknown_tool", "bad_json", "missing_arg", "extra_arg", "bad_choice", "not_integer",
    "too_far_step", "too_short_step", "too_far_turn", "out_of_range", "reserved_colour",
})

_STATE_REASONS = {
    State.STOPPED: "stopped",
    State.FALLEN: "fallen",
    State.RESTING: "resting",
    State.OFFLINE: "offline",
}


@dataclass(frozen=True)
class GateResult:
    ok: bool
    result: Optional[str] = None
    error: Optional[str] = None
    reason: Optional[str] = None
    details: Optional[Dict[str, Any]] = None

    def as_dict(self) -> Dict[str, Any]:
        if self.ok:
            return {"ok": True, "result": self.result}
        return {"ok": False, "error": self.error}

    @property
    def malformed(self) -> bool:
        return not self.ok and self.reason in MALFORMED


class Gate:
    def __init__(self, bridge: "Bridge") -> None:
        self.bridge = bridge
        self.motion_lock = threading.Lock()

    # -- public ----------------------------------------------------------
    def check_and_run(self, name: str, args: Any = None, source: str = "llm",
                      lang: Optional[str] = None) -> GateResult:
        """Never raises."""
        lang = lang or self.bridge.language
        started = time.monotonic()
        try:
            result = self._check_and_run(name, args, lang)
        except Exception as err:  # last line of defence
            self.bridge.log.log("error", f"gate failure in {name}: {err!r}", source=source)
            result = self._fail("robot_error", lang)
        self._log(name, args, source, result, time.monotonic() - started)
        return result

    # -- internals -------------------------------------------------------
    def _fail(self, reason: str, lang: str, **params: Any) -> GateResult:
        text_params = {k: v for k, v in params.items() if k != "choices"}
        return GateResult(False, error=msg(reason, lang, **text_params), reason=reason, details=params or None)

    def _check_and_run(self, name: str, raw_args: Any, lang: str) -> GateResult:
        bridge = self.bridge
        # 1. tool exists
        spec = bridge.registry.get(name)
        if spec is None:
            return self._fail("unknown_tool", lang, name=name)

        # 2. arguments
        args, err = self._parse_args(spec, raw_args)
        motors_off = not bridge.motors_enabled and spec.category in (MOTION, STOP)
        # With motors off the action enums can be empty, because behaviors are
        # not installed. An unknown action must still report motors_off.
        if err is not None and not (motors_off and err.reason == "bad_choice"):
            return self._fail(err.reason, lang, **err.params)
        if motors_off:
            return self._fail("motors_off", lang)
        if spec.name == "who_is_here" and not bridge.robot.perception.available:
            return self._fail("perception_off", lang)
        assert args is not None

        if spec.category not in (MOTION, LED):
            return self._execute(spec, args, lang)

        status = bridge.robot.events.status()
        with bridge.state.lock:
            # 3. state
            current = bridge.state.state
            if not status.connected:
                return self._fail("offline", lang)
            if status.fallen:
                return self._fail("fallen", lang)
            if current in _STATE_REASONS:
                return self._fail(_STATE_REASONS[current], lang)
            if spec.category == LED:
                return self._execute(spec, args, lang)
            if current == State.ACTING:
                return self._fail("already_moving", lang)
            # 4. preconditions, 5. thermal
            reason = self._preconditions(spec, args, status) or self._thermal(spec, args, status)
            if reason is not None:
                return self._fail(reason[0], lang, **reason[1])
            # 6. lock
            if not self.motion_lock.acquire(blocking=False):
                return self._fail("already_moving", lang)
            previous = current
            bridge.state.transition(State.ACTING)
        try:
            return self._execute(spec, args, lang)
        finally:
            with bridge.state.lock:
                if not bridge.state.transition_from(State.ACTING, previous):
                    bridge.state.transition_from(State.ACTING, State.IDLE)
            self.motion_lock.release()
            bridge.touch()

    def _parse_args(self, spec: ToolSpec, raw: Any):
        if isinstance(raw, str):
            if not raw.strip():
                raw = {}
            else:
                try:
                    raw = json.loads(raw)
                except ValueError:
                    return None, ArgError("bad_json", {})
        if spec.name == "set_eyes" and isinstance(raw, dict) and raw.get("color") in STATUS_COLOURS:
            return None, ArgError("reserved_colour", {"value": raw["color"]})
        return check_args(spec, raw)

    def _preconditions(self, spec: ToolSpec, args: Dict[str, Any], status: RobotStatus):
        cfg = self.bridge.config.motion
        name = spec.name
        low_battery = status.battery_pct < cfg.battery_motion_min_pct
        if low_battery and not (name == "set_posture" and args["posture"] in ("Sit", "Crouch")):
            return "battery_low", {}
        if name in ("turn", "step") and not self.bridge.walking:
            return "walking_off", {}
        if name == "dance" and status.battery_pct < cfg.battery_dance_min_pct:
            return "battery_dance", {}
        required = self._required_posture(spec, args)
        if required is not None and status.posture != required:
            if required == "Stand":
                return "need_stand", {}
            return "need_posture", {"posture": required}
        if name == "step":
            budget_cm = int(round(cfg.max_total_displacement_m * 100))
            if self.bridge.displacement_cm + args["cm"] > budget_cm:
                return "distance_budget", {"max": cfg.max_total_displacement_m}
        return None

    def _required_posture(self, spec: ToolSpec, args: Dict[str, Any]) -> Optional[str]:
        if spec.name in ("dance", "turn", "step"):
            if spec.name == "dance":
                target = self.bridge.registry.catalogue.dances[args["name"]]
                if target.kind == "keyframes":
                    return self.bridge.keyframes[target.path].posture
            return "Stand"
        if spec.name == "play_gesture":
            target = self.bridge.registry.catalogue.gestures[args["name"]]
            if target.kind == "keyframes":
                return self.bridge.keyframes[target.path].posture
            return "Stand"
        return None

    def _thermal(self, spec: ToolSpec, args: Dict[str, Any], status: RobotStatus):
        if status.thermal == "hot":
            if not (spec.name == "set_posture" and args["posture"] == "Crouch"):
                return "too_hot", {}
        elif status.thermal == "warm" and spec.name in ("dance", "step"):
            return "too_warm", {}
        return None

    def _execute(self, spec: ToolSpec, args: Dict[str, Any], lang: str) -> GateResult:
        outcome = HANDLERS[spec.name](self.bridge, args, lang)
        return GateResult(True, result=outcome)

    def _log(self, name: str, args: Any, source: str, result: GateResult, seconds: float) -> None:
        fields: Dict[str, Any] = {"args": json.dumps(args, ensure_ascii=False) if not isinstance(args, str) else args,
                                  "ms": int(seconds * 1000)}
        if result.ok:
            # Names of club members are personal data: keep them out in event mode.
            text = result.result if name == "who_is_here" else None
            message = f"{name} ok" if text else f"{name} ok: {result.result}"
            self.bridge.log.log("tool", message, source=source, text=text, **fields)
        else:
            self.bridge.log.log("reject", f"{name}: {result.reason}", source=source, **fields)
