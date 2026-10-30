"""The animation library on disk: load, validate and filter for the tool enums."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from naollm.animations.limits import JointLimits, load_limits
from naollm.animations.model import Animation, AnimationError, load_animation
from naollm.animations.validate import Violation, validate

SUFFIX = ".anim.yaml"


@dataclass
class Entry:
    name: str
    path: Path
    animation: Optional[Animation] = None
    error: Optional[str] = None
    violations: List[Violation] = field(default_factory=list)

    @property
    def valid(self) -> bool:
        return self.animation is not None and not self.error and not self.violations

    @property
    def reviewed(self) -> bool:
        return self.animation is not None and self.animation.reviewed


def animation_files(directory: str | Path) -> List[Path]:
    d = Path(directory)
    return sorted(d.glob(f"*{SUFFIX}")) if d.is_dir() else []


def load_entry(path: Path, limits: JointLimits) -> Entry:
    name = path.name[: -len(SUFFIX)]
    entry = Entry(name, path)
    try:
        entry.animation = load_animation(path)
    except AnimationError as err:
        entry.error = str(err)
        return entry
    entry.violations = validate(entry.animation, limits)
    return entry


def load_library(directory: str | Path, limits: Optional[JointLimits] = None) -> List[Entry]:
    limits = limits or load_limits(None)
    return [load_entry(p, limits) for p in animation_files(directory)]


def playable(entries: List[Entry], event_mode: bool) -> Tuple[Dict[str, Animation], Dict[str, str]]:
    """Animations offered to the LLM, and why the others are not."""
    ok: Dict[str, Animation] = {}
    problems: Dict[str, str] = {}
    for e in entries:
        if e.error:
            problems[e.name] = "cannot load"
        elif e.violations:
            problems[e.name] = f"{len(e.violations)} validation problem(s)"
        elif event_mode and not e.reviewed:
            problems[e.name] = "not reviewed (event mode)"
        else:
            assert e.animation is not None
            ok[e.name] = e.animation
    return ok, problems


def find_profile(profile_dir: str | Path, robot_id: str) -> Optional[Path]:
    path = Path(profile_dir).expanduser() / f"{robot_id}.yaml"
    return path if path.is_file() else None
