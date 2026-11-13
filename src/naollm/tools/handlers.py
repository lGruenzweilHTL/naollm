"""Tool implementations (SPEC §8.2). Called only by the gate."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any, Callable, Dict

from naollm.animations import player
from naollm.robot.adapter import Outcome
from naollm.tools.messages import msg
from naollm.tools.registry import Target
from naollm.tools.schemas import EYE_COLOURS

if TYPE_CHECKING:
    from naollm.bridge import Bridge

Handler = Callable[["Bridge", Dict[str, Any], str], str]

# Fixed head angles in degrees (yaw positive = left, pitch positive = down).
LOOK_ANGLES = {
    "left": (40.0, 0.0),
    "right": (-40.0, 0.0),
    "up": (0.0, -20.0),
    "down": (0.0, 15.0),
}

STEP_VECTORS = {
    "forward": (1.0, 0.0),
    "back": (-1.0, 0.0),
    "left": (0.0, 1.0),
    "right": (0.0, -1.0),
}


# Age game (SPEC §8.6): below this the robot says it cannot see well enough.
AGE_MIN_CONFIDENCE = 0.5
AGE_STEP = 5
AGE_MIN, AGE_MAX = 5, 80


class RobotFailure(Exception):
    """The robot could not do what was asked."""


class ToolRefused(Exception):
    """A tool declines with a gate reason the robot can say, such as `age_several`."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def run_target(bridge: "Bridge", target: Target) -> Outcome:
    motion = bridge.robot.motion
    if target.kind == "behavior":
        return motion.run_behavior(target.path)
    if target.kind == "builtin":
        return motion.play_animation(target.path)
    return player.play(motion, bridge.keyframes[target.path])


def play_gesture(bridge: "Bridge", args: Dict[str, Any], lang: str) -> str:
    return run_target(bridge, bridge.registry.catalogue.gestures[args["name"]])


def dance(bridge: "Bridge", args: Dict[str, Any], lang: str) -> str:
    try:
        return run_target(bridge, bridge.registry.catalogue.dances[args["name"]])
    finally:
        # Heat builds while the dance runs, so the cooldown counts from its end, also an interrupted one.
        bridge.last_dance_end = bridge.clock()


def set_posture(bridge: "Bridge", args: Dict[str, Any], lang: str) -> str:
    if not bridge.robot.motion.go_to_posture(args["posture"]):
        raise RobotFailure(f"posture {args['posture']} not reached")
    return "finished"


def look(bridge: "Bridge", args: Dict[str, Any], lang: str) -> str:
    perception = bridge.robot.perception
    direction = args["direction"]
    if direction == "at_person":
        bridge.cancel_timer("look")
        perception.set_tracking(True)
        bridge.tracking_wanted = True
        return "looking at you"
    yaw, pitch = LOOK_ANGLES[direction]
    perception.set_tracking(False)
    bridge.robot.motion.set_head(math.radians(yaw), math.radians(pitch))
    # Tracking returns after the hold, unless an emergency stop or a fall came in between.
    bridge.schedule("look", bridge.look_hold_s, bridge.resume_tracking)
    return f"looking {direction}"


def turn(bridge: "Bridge", args: Dict[str, Any], lang: str) -> str:
    return bridge.robot.motion.move_to(0.0, 0.0, math.radians(args["degrees"]))


def step(bridge: "Bridge", args: Dict[str, Any], lang: str) -> str:
    dx, dy = STEP_VECTORS[args["direction"]]
    metres = args["cm"] / 100.0
    # Counted before moving: an interrupted step still uses its budget.
    bridge.displacement_cm += args["cm"]
    return bridge.robot.motion.move_to(dx * metres, dy * metres, 0.0)


def set_eyes(bridge: "Bridge", args: Dict[str, Any], lang: str) -> str:
    bridge.robot.speech.set_eyes(EYE_COLOURS[args["color"]])
    bridge.schedule("eyes", bridge.eyes_hold_s, bridge.restore_eyes)
    return f"eyes {args['color']}"


def get_status(bridge: "Bridge", args: Dict[str, Any], lang: str) -> str:
    s = bridge.robot.events.status()
    battery = bridge.config.robot.battery_reliable
    if lang == "en":
        return (f"battery {s.battery_pct} %, " if battery else "") + f"posture {s.posture}, temperature {s.thermal}"
    return (f"Akku {s.battery_pct} %, " if battery else "") + f"Haltung {s.posture}, Temperatur {s.thermal}"


def who_is_here(bridge: "Bridge", args: Dict[str, Any], lang: str) -> str:
    perception = bridge.robot.perception
    count = perception.people_count()
    names = perception.recognized_names() if bridge.config.privacy.face_recognition_enabled else []
    if lang == "en":
        text = f"{count} people"
        return text + (f"; known club members: {', '.join(names)}" if names else "")
    text = f"{count} Personen"
    return text + (f"; bekannte Club-Mitglieder: {', '.join(names)}" if names else "")


def age_line(age: float, lang: str) -> str:
    """A 5-year range, rounded down; no number outside 5..80."""
    if age < AGE_MIN:
        return msg("age_very_young", lang)
    if age > AGE_MAX:
        return msg("age_very_old", lang)
    low = int(age // AGE_STEP) * AGE_STEP
    return msg("age_range", lang, low=low, high=low + AGE_STEP)


def guess_age(bridge: "Bridge", args: Dict[str, Any], lang: str) -> str:
    """Speaks the range itself. The estimate stays in this function: no return value, log or history holds it."""
    perception = bridge.robot.perception
    count = perception.people_count()
    if count == 0:
        raise ToolRefused("age_nobody")
    if count > 1:
        raise ToolRefused("age_several")
    estimate = perception.estimate_age()
    if estimate is None or estimate.confidence < AGE_MIN_CONFIDENCE:
        raise ToolRefused("age_unsure")
    line = age_line(estimate.age, lang)
    del estimate
    bridge.robot.speech.say(line, False)
    return msg("age_told", lang)


def stop(bridge: "Bridge", args: Dict[str, Any], lang: str) -> str:
    """Soft stop: halt, keep posture, do not enter STOPPED."""
    bridge.robot.motion.stop_all_behaviors()
    bridge.robot.motion.stop_move()
    return "stopped"


HANDLERS: Dict[str, Handler] = {
    "play_gesture": play_gesture,
    "dance": dance,
    "set_posture": set_posture,
    "look": look,
    "turn": turn,
    "step": step,
    "set_eyes": set_eyes,
    "get_status": get_status,
    "who_is_here": who_is_here,
    "stop": stop,
    "guess_age": guess_age,
}
