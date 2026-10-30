"""Action catalogue and the set of tools offered to the LLM (SPEC §6.3, §7.4, §8)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional

from naollm.config import ANIM_REF_RE, Config
from naollm.tools.schemas import MAX_TOOLS, ToolSpec, build_specs


@dataclass(frozen=True)
class Target:
    kind: str  # "behavior" | "builtin" | "keyframes"
    path: str  # behavior path, built-in animation path, or animation name


def parse_target(value: str) -> Target:
    m = ANIM_REF_RE.match(value)
    if m:
        return Target("keyframes", m.group(1))
    if value.startswith("animations/"):
        return Target("builtin", value)
    return Target("behavior", value)


@dataclass
class Catalogue:
    gestures: Dict[str, Target] = field(default_factory=dict)
    dances: Dict[str, Target] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)


def build_catalogue(
    config: Config,
    installed_behaviors: List[str],
    keyframes: Mapping[str, Any],
    keyframe_problems: Optional[Mapping[str, str]] = None,
) -> Catalogue:
    """Keep only entries whose target is available.

    `keyframes` maps animation names to playable animations (already valid and,
    in event mode, reviewed). `keyframe_problems` explains why others are missing.
    """
    installed = set(installed_behaviors)
    problems = dict(keyframe_problems or {})
    cat = Catalogue()
    for section, out in (("gestures", cat.gestures), ("dances", cat.dances)):
        for name, raw in getattr(config, section).items():
            target = parse_target(raw)
            if target.kind == "keyframes":
                if target.path in keyframes:
                    out[name] = target
                else:
                    why = problems.get(target.path, "animation file not found")
                    cat.warnings.append(f"{section}.{name}: animation '{target.path}' unavailable ({why})")
            elif target.path in installed:
                out[name] = target
            else:
                cat.warnings.append(f"{section}.{name}: '{target.path}' is not installed on the robot")
    return cat


class Registry:
    """Answers which tools exist and which are offered to the LLM right now."""

    def __init__(self, config: Config, catalogue: Catalogue) -> None:
        self.config = config
        self.catalogue = catalogue
        self.specs: Dict[str, ToolSpec] = build_specs(
            gestures=list(catalogue.gestures),
            dances=list(catalogue.dances),
            max_turn_deg=config.motion.max_turn_deg,
            max_step_cm=config.motion.max_step_cm,
        )

    def get(self, name: str) -> Optional[ToolSpec]:
        return self.specs.get(name)

    def enabled(self, name: str, walking: bool) -> bool:
        spec = self.specs.get(name)
        if spec is None:
            return False
        if name in ("turn", "step") and not walking:
            return False
        if name == "play_gesture" and not self.catalogue.gestures:
            return False
        if name == "dance" and not self.catalogue.dances:
            return False
        return True

    def enabled_specs(self, walking: bool) -> List[ToolSpec]:
        out = [s for s in self.specs.values() if self.enabled(s.name, walking)]
        return out[:MAX_TOOLS]

    def schemas(self, walking: bool) -> List[Dict[str, Any]]:
        return [s.schema() for s in self.enabled_specs(walking)]
