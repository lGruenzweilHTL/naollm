"""Animation validator (SPEC §19.3). Reports violations; never raises."""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from naollm.animations.limits import HAND_JOINTS, LEG_JOINTS, JointLimits
from naollm.animations.model import Animation

ANGLE_MARGIN_DEG = 5.0
POSTURE_TOLERANCE_DEG = 10.0
MAX_DURATION_S = 30.0
DEFAULT_SPEED_FRACTION = 0.6


@dataclass(frozen=True)
class Violation:
    rule: str
    joint: Optional[str]
    time: Optional[float]
    message: str

    def __str__(self) -> str:
        where = []
        if self.joint:
            where.append(self.joint)
        if self.time is not None:
            where.append(f"t={self.time:g}s")
        prefix = f"[{self.rule}] " + (" ".join(where) + ": " if where else "")
        return prefix + self.message


def validate(anim: Animation, limits: JointLimits,
             speed_fraction: float = DEFAULT_SPEED_FRACTION) -> List[Violation]:
    out: List[Violation] = []
    posture = limits.postures.get(anim.posture, {})

    for joint, track in anim.tracks.items():
        # 1. known joint (duplicates are impossible after YAML mapping load,
        #    the importer checks them itself)
        if joint not in limits.limits:
            out.append(Violation("joint", joint, None, "unknown NAO joint name"))
            continue
        if len(track.times) != len(track.angles_deg):
            out.append(Violation("shape", joint, None,
                                 f"{len(track.times)} times but {len(track.angles_deg)} angles"))
            continue
        if not track.times:
            out.append(Violation("shape", joint, None, "track has no keyframes"))
            continue
        # 6./7. legs
        if joint in LEG_JOINTS and not anim.uses_legs:
            out.append(Violation("legs", joint, None, "leg/hip joint used but uses_legs is false"))
        lo, hi, _ = limits.limits[joint]
        hand = joint in HAND_JOINTS
        margin = 0.0 if hand else ANGLE_MARGIN_DEG
        prev_t, prev_a = 0.0, posture.get(joint)
        for t, a in zip(track.times, track.angles_deg):
            # 4. timing
            if t <= prev_t:
                out.append(Violation("time", joint, t, "times must be positive and strictly increasing"))
            if t > MAX_DURATION_S:
                out.append(Violation("time", joint, t, f"keyframe after {MAX_DURATION_S:g} s"))
            # 2. limits
            if a < lo + margin or a > hi - margin:
                unit = "" if hand else "°"
                out.append(Violation("limit", joint, t,
                                     f"{a:g}{unit} outside {lo + margin:g}..{hi - margin:g}{unit}"))
            # 3. speed
            if prev_a is not None and t > prev_t and not hand:
                speed = abs(a - prev_a) / (t - prev_t)
                allowed = limits.max_velocity_deg_s(joint) * speed_fraction
                if speed > allowed:
                    out.append(Violation("speed", joint, t,
                                         f"{speed:.0f}°/s exceeds {allowed:.0f}°/s "
                                         f"({speed_fraction:.0%} of max)"))
            prev_t, prev_a = t, a
        # 5. start/end near the declared posture
        ref = posture.get(joint)
        if ref is not None and not hand:
            first_t, first_a = track.times[0], track.angles_deg[0]
            last_t, last_a = track.times[-1], track.angles_deg[-1]
            if abs(first_a - ref) > POSTURE_TOLERANCE_DEG:
                out.append(Violation("posture", joint, first_t,
                                     f"first keyframe {first_a:g}° is more than "
                                     f"{POSTURE_TOLERANCE_DEG:g}° from {anim.posture} ({ref:g}°)"))
            if abs(last_a - ref) > POSTURE_TOLERANCE_DEG:
                out.append(Violation("posture", joint, last_t,
                                     f"last keyframe {last_a:g}° is more than "
                                     f"{POSTURE_TOLERANCE_DEG:g}° from {anim.posture} ({ref:g}°)"))

    last = max((tr.times[-1] for tr in anim.tracks.values() if tr.times), default=0.0)
    if last > MAX_DURATION_S:
        out.append(Violation("time", None, last, f"animation longer than {MAX_DURATION_S:g} s"))
    if last > anim.duration_s + 1e-6:
        out.append(Violation("time", None, last, f"keyframes run past duration_s ({anim.duration_s:g} s)"))
    if anim.uses_legs and not anim.reviewed:
        out.append(Violation("review", None, None, "uses_legs is true but reviewed_by is empty"))
    return out
