"""Keyframe playback through the adapter (SPEC §19.4).

The gate has already taken the motion lock when this runs.
"""

from __future__ import annotations

from naollm.animations.model import Animation
from naollm.robot.adapter import Motion, Outcome


def play(motion: Motion, anim: Animation) -> Outcome:
    upper_body = not anim.uses_legs
    if upper_body:
        motion.set_balancer(True)
    try:
        return motion.play_keyframes(anim.to_keyframes())
    finally:
        if upper_body:
            motion.set_balancer(False)
