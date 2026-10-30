"""Joint limits and posture reference angles for validation (SPEC §19.3).

The built-in values are NAO 6 reference values from the public NAO
documentation. `naollm init` (later change) writes the robot's own values from
`ALMotion.getLimits` into the robot profile, which then replaces these.
Posture angles are approximate and only used with the 10 degree tolerance.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional, Tuple

import yaml

# joint: (min_deg, max_deg, max_velocity_rad_s)
NAO6_LIMITS: Dict[str, Tuple[float, float, float]] = {
    "HeadYaw": (-119.5, 119.5, 8.27),
    "HeadPitch": (-38.5, 29.5, 7.19),
    "LShoulderPitch": (-119.5, 119.5, 7.19),
    "LShoulderRoll": (-18.0, 76.0, 7.19),
    "LElbowYaw": (-119.5, 119.5, 8.27),
    "LElbowRoll": (-88.5, -2.0, 7.19),
    "LWristYaw": (-104.5, 104.5, 24.6),
    "LHand": (0.0, 1.0, 8.33),
    "RShoulderPitch": (-119.5, 119.5, 7.19),
    "RShoulderRoll": (-76.0, 18.0, 7.19),
    "RElbowYaw": (-119.5, 119.5, 8.27),
    "RElbowRoll": (2.0, 88.5, 7.19),
    "RWristYaw": (-104.5, 104.5, 24.6),
    "RHand": (0.0, 1.0, 8.33),
    "LHipYawPitch": (-65.62, 42.44, 4.16),
    "LHipRoll": (-21.74, 45.29, 4.16),
    "LHipPitch": (-88.0, 27.73, 6.4),
    "LKneePitch": (-5.29, 121.04, 6.4),
    "LAnklePitch": (-68.15, 52.86, 6.4),
    "LAnkleRoll": (-22.79, 44.06, 4.16),
    "RHipYawPitch": (-65.62, 42.44, 4.16),
    "RHipRoll": (-45.29, 21.74, 4.16),
    "RHipPitch": (-88.0, 27.73, 6.4),
    "RKneePitch": (-5.9, 121.47, 6.4),
    "RAnklePitch": (-67.97, 53.4, 6.4),
    "RAnkleRoll": (-44.06, 22.8, 4.16),
}

LEG_JOINTS = frozenset(j for j in NAO6_LIMITS if any(k in j for k in ("Hip", "Knee", "Ankle")))
HAND_JOINTS = frozenset({"LHand", "RHand"})

# Approximate joint angles (deg) of the predefined postures.
POSTURES: Dict[str, Dict[str, float]] = {
    "Stand": {
        "HeadYaw": 0.0, "HeadPitch": 0.0,
        "LShoulderPitch": 85.0, "LShoulderRoll": 10.0, "LElbowYaw": -70.0, "LElbowRoll": -25.0, "LWristYaw": 0.0,
        "RShoulderPitch": 85.0, "RShoulderRoll": -10.0, "RElbowYaw": 70.0, "RElbowRoll": 25.0, "RWristYaw": 0.0,
        "LHipYawPitch": 0.0, "LHipRoll": 0.0, "LHipPitch": -25.0, "LKneePitch": 40.0,
        "LAnklePitch": -20.0, "LAnkleRoll": 0.0,
        "RHipYawPitch": 0.0, "RHipRoll": 0.0, "RHipPitch": -25.0, "RKneePitch": 40.0,
        "RAnklePitch": -20.0, "RAnkleRoll": 0.0,
    },
    "Crouch": {
        "HeadYaw": 0.0, "HeadPitch": 0.0,
        "LShoulderPitch": 85.0, "LShoulderRoll": 5.0, "LElbowYaw": -65.0, "LElbowRoll": -40.0, "LWristYaw": 0.0,
        "RShoulderPitch": 85.0, "RShoulderRoll": -5.0, "RElbowYaw": 65.0, "RElbowRoll": 40.0, "RWristYaw": 0.0,
        "LHipYawPitch": 0.0, "LHipRoll": 0.0, "LHipPitch": -50.0, "LKneePitch": 120.0,
        "LAnklePitch": -68.0, "LAnkleRoll": 0.0,
        "RHipYawPitch": 0.0, "RHipRoll": 0.0, "RHipPitch": -50.0, "RKneePitch": 120.0,
        "RAnklePitch": -68.0, "RAnkleRoll": 0.0,
    },
    "Sit": {
        "HeadYaw": 0.0, "HeadPitch": 0.0,
        "LShoulderPitch": 50.0, "LShoulderRoll": 10.0, "LElbowYaw": -50.0, "LElbowRoll": -60.0, "LWristYaw": 0.0,
        "RShoulderPitch": 50.0, "RShoulderRoll": -10.0, "RElbowYaw": 50.0, "RElbowRoll": 60.0, "RWristYaw": 0.0,
        "LHipYawPitch": -10.0, "LHipRoll": 5.0, "LHipPitch": -50.0, "LKneePitch": 70.0,
        "LAnklePitch": 0.0, "LAnkleRoll": 0.0,
        "RHipYawPitch": -10.0, "RHipRoll": -5.0, "RHipPitch": -50.0, "RKneePitch": 70.0,
        "RAnklePitch": 0.0, "RAnkleRoll": 0.0,
    },
}


@dataclass(frozen=True)
class JointLimits:
    limits: Dict[str, Tuple[float, float, float]]
    postures: Dict[str, Dict[str, float]]
    source: str

    def max_velocity_deg_s(self, joint: str) -> float:
        return math.degrees(self.limits[joint][2])


def builtin_limits() -> JointLimits:
    return JointLimits(dict(NAO6_LIMITS), {k: dict(v) for k, v in POSTURES.items()},
                       "built-in NAO 6 reference limits")


def load_limits(profile: Optional[str | Path] = None) -> JointLimits:
    """Robot profile format (written by `naollm init`):

        joint_limits: {HeadYaw: {min_deg: .., max_deg: .., max_velocity_rad_s: ..}, ...}
        postures: {Stand: {HeadYaw: 0.0, ...}, ...}   # optional
    """
    if profile is None:
        return builtin_limits()
    path = Path(profile).expanduser()
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    limits = dict(NAO6_LIMITS)
    for joint, v in (data.get("joint_limits") or {}).items():
        limits[joint] = (float(v["min_deg"]), float(v["max_deg"]), float(v["max_velocity_rad_s"]))
    postures = {k: dict(v) for k, v in POSTURES.items()}
    for name, angles in (data.get("postures") or {}).items():
        postures[name] = {j: float(a) for j, a in angles.items()}
    return JointLimits(limits, postures, f"robot profile {path}")
