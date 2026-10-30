"""Tool definitions, JSON schemas for the LLM, and argument checking (SPEC §7.4, §8)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

# Categories decide which gate rules apply.
MOTION = "motion"   # takes the motion lock
LED = "led"         # eyes; refused in blocking states, no lock
READ = "read"       # always allowed
STOP = "stop"       # always allowed, never locked

MAX_TOOLS = 10

LOOK_DIRECTIONS = ["left", "right", "up", "down", "at_person"]
POSTURES = ["Stand", "Sit", "Crouch"]
STEP_DIRECTIONS = ["forward", "back", "left", "right"]
EYE_COLOURS = {
    "white": "#FFFFFF",
    "purple": "#8000FF",
    "orange": "#FF8000",
    "pink": "#FF40A0",
    "cyan": "#00FFFF",
}
# Status colours, never selectable by the LLM.
STATUS_COLOURS = {
    "blue": "#0000FF",     # listening
    "yellow": "#FFC000",   # thinking
    "red": "#FF0000",      # emergency stop / fallen
    "green": "#00FF00",    # face enrolment
}


@dataclass(frozen=True)
class Param:
    name: str
    kind: str  # "enum" | "int"
    choices: Tuple[str, ...] = ()
    minimum: Optional[int] = None
    maximum: Optional[int] = None

    def schema(self) -> Dict[str, Any]:
        if self.kind == "enum":
            return {"type": "string", "enum": list(self.choices)}
        s: Dict[str, Any] = {"type": "integer"}
        if self.minimum is not None:
            s["minimum"] = self.minimum
        if self.maximum is not None:
            s["maximum"] = self.maximum
        return s


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    category: str
    params: Tuple[Param, ...] = field(default_factory=tuple)

    def schema(self) -> Dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": {p.name: p.schema() for p in self.params},
                    "required": [p.name for p in self.params],
                    "additionalProperties": False,
                },
            },
        }

    def param(self, name: str) -> Optional[Param]:
        for p in self.params:
            if p.name == name:
                return p
        return None


@dataclass(frozen=True)
class ArgError:
    reason: str
    params: Dict[str, Any]


def check_args(spec: ToolSpec, args: Any) -> Tuple[Optional[Dict[str, Any]], Optional[ArgError]]:
    """Validate `args` against the tool's schema. Rejects, never clamps."""
    if args is None:
        args = {}
    if not isinstance(args, dict):
        return None, ArgError("bad_json", {})
    for key in args:
        if spec.param(key) is None:
            return None, ArgError("extra_arg", {"param": key})
    clean: Dict[str, Any] = {}
    for p in spec.params:
        if p.name not in args:
            return None, ArgError("missing_arg", {"param": p.name})
        value = args[p.name]
        if p.kind == "enum":
            if not isinstance(value, str) or value not in p.choices:
                return None, ArgError("bad_choice", {"param": p.name, "value": value,
                                                     "choices": list(p.choices)})
            clean[p.name] = value
        else:
            if isinstance(value, bool):
                return None, ArgError("not_integer", {"param": p.name})
            if isinstance(value, float) and value.is_integer():
                value = int(value)
            if not isinstance(value, int):
                return None, ArgError("not_integer", {"param": p.name})
            if (p.minimum is not None and value < p.minimum) or (p.maximum is not None and value > p.maximum):
                return None, ArgError(_range_reason(spec.name, p, value),
                                      {"param": p.name, "value": value, "min": p.minimum, "max": p.maximum})
            clean[p.name] = value
    return clean, None


def _range_reason(tool: str, p: Param, value: int) -> str:
    if tool == "step" and p.name == "cm":
        return "too_far_step" if value > (p.maximum or 0) else "too_short_step"
    if tool == "turn":
        return "too_far_turn"
    return "out_of_range"


def build_specs(
    gestures: Sequence[str],
    dances: Sequence[str],
    max_turn_deg: int,
    max_step_cm: int,
) -> Dict[str, ToolSpec]:
    """All tools in a fixed order; enums come from the catalogue."""
    specs = [
        ToolSpec("play_gesture", "Play one of your gestures.", MOTION,
                 (Param("name", "enum", tuple(gestures)),)),
        ToolSpec("dance", "Perform one of your dances.", MOTION, (Param("name", "enum", tuple(dances)),)),
        ToolSpec("set_posture", "Stand up, sit down or crouch.", MOTION,
                 (Param("posture", "enum", tuple(POSTURES)),)),
        ToolSpec("look", "Turn your head in a direction for a moment.", MOTION,
                 (Param("direction", "enum", tuple(LOOK_DIRECTIONS)),)),
        ToolSpec("turn", "Turn your body on the spot; positive is left.", MOTION,
                 (Param("degrees", "int", minimum=-max_turn_deg, maximum=max_turn_deg),)),
        ToolSpec("step", "Take a few small steps.", MOTION,
                 (Param("direction", "enum", tuple(STEP_DIRECTIONS)),
                  Param("cm", "int", minimum=5, maximum=max_step_cm))),
        ToolSpec("set_eyes", "Change the colour of your eyes.", LED,
                 (Param("color", "enum", tuple(EYE_COLOURS)),)),
        ToolSpec("get_status", "Check your battery, posture and temperature.", READ),
        ToolSpec("who_is_here", "Count people in front of you and name known club members.", READ),
        ToolSpec("stop", "Stop moving right now.", STOP),
    ]
    return {s.name: s for s in specs}
