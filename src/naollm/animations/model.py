"""Keyframe animation file format (SPEC §19.1)."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Dict, List, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from naollm.robot.adapter import Keyframes

# Hands are open/close fractions 0..1, not angles. They are stored unchanged.
HAND_JOINTS = frozenset({"LHand", "RHand"})


class AnimationError(Exception):
    pass


class _UniqueKeyLoader(yaml.SafeLoader):
    """Rejects duplicate keys, e.g. the same joint track twice."""

    def construct_mapping(self, node, deep=False):
        seen = set()
        for key_node, _ in node.value:
            key = self.construct_object(key_node, deep=deep)
            if key in seen:
                raise AnimationError(f"duplicate key '{key}' (line {key_node.start_mark.line + 1})")
            seen.add(key)
        return super().construct_mapping(node, deep=deep)


class Track(BaseModel):
    model_config = ConfigDict(extra="forbid")
    times: List[float]
    angles_deg: List[float]


class Animation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    description: str = ""
    version: int = 1
    author: str = "human"
    reviewed_by: str = ""
    posture: Literal["Stand", "Sit", "Crouch"] = "Stand"
    duration_s: float = Field(gt=0)
    uses_legs: bool = False
    interpolation: Literal["bezier", "linear"] = "bezier"
    tracks: Dict[str, Track]

    @property
    def reviewed(self) -> bool:
        return bool(self.reviewed_by.strip())

    def angles_rad(self, joint: str) -> List[float]:
        values = self.tracks[joint].angles_deg
        if joint in HAND_JOINTS:
            return list(values)
        return [math.radians(v) for v in values]

    def to_keyframes(self) -> Keyframes:
        names = list(self.tracks)
        return Keyframes(
            names=names,
            times=[list(self.tracks[j].times) for j in names],
            angles=[self.angles_rad(j) for j in names],
            bezier=self.interpolation == "bezier",
            duration_s=self.duration_s,
        )

    def to_yaml(self) -> str:
        data = self.model_dump()
        data["tracks"] = {j: {"times": t["times"], "angles_deg": t["angles_deg"]} for j, t in data["tracks"].items()}
        return yaml.safe_dump(data, sort_keys=False, allow_unicode=True, default_flow_style=None)


def load_animation(path: str | Path) -> Animation:
    p = Path(path)
    try:
        data = yaml.load(p.read_text(encoding="utf-8"), Loader=_UniqueKeyLoader)
    except AnimationError as err:
        raise AnimationError(f"{p}: {err}") from None
    except (OSError, yaml.YAMLError) as err:
        raise AnimationError(f"{p}: cannot read: {err}") from None
    try:
        anim = Animation.model_validate(data)
    except ValidationError as err:
        detail = "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in err.errors())
        raise AnimationError(f"{p}: {detail}") from None
    expected = p.name[: -len(".anim.yaml")] if p.name.endswith(".anim.yaml") else p.stem
    if anim.name != expected:
        raise AnimationError(f"{p}: name '{anim.name}' does not match file name '{expected}'")
    return anim
