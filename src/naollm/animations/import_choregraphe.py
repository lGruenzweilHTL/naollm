"""Convert a Choregraphe "export to Python" timeline to an animation file (SPEC §19.2).

The export is parsed with `ast`; it is never executed. Only literal values in
`names.append(...)`, `times.append(...)`, `keys.append(...)` and plain list
assignments to `names`, `times`, `keys` are used.
"""

from __future__ import annotations

import ast
import math
from pathlib import Path
from typing import Any, Dict, List, Tuple

from naollm.animations.limits import HAND_JOINTS, LEG_JOINTS
from naollm.animations.model import Animation

_LISTS = ("names", "times", "keys")


class ChoregrapheImportError(Exception):
    pass


def _literal(node: ast.AST) -> Any:
    try:
        return ast.literal_eval(node)
    except ValueError:
        raise ChoregrapheImportError(f"line {getattr(node, 'lineno', '?')}: not a literal value") from None


def parse_export(source: str) -> Tuple[List[str], List[List[float]], List[List[Any]]]:
    try:
        tree = ast.parse(source)
    except SyntaxError as err:
        raise ChoregrapheImportError(f"not valid Python: {err}") from None
    found: Dict[str, List[Any]] = {k: [] for k in _LISTS}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and node.func.attr == "append" and isinstance(node.func.value, ast.Name) \
                and node.func.value.id in _LISTS and len(node.args) == 1:
            found[node.func.value.id].append(_literal(node.args[0]))
        elif isinstance(node, ast.Assign) and len(node.targets) == 1 \
                and isinstance(node.targets[0], ast.Name) and node.targets[0].id in _LISTS \
                and isinstance(node.value, (ast.List, ast.Tuple)):
            found[node.targets[0].id].extend(_literal(node.value))
    names, times, keys = found["names"], found["times"], found["keys"]
    if not names:
        raise ChoregrapheImportError("no 'names' found: is this a Choregraphe timeline export?")
    if not (len(names) == len(times) == len(keys)):
        raise ChoregrapheImportError(f"names/times/keys lengths differ: {len(names)}/{len(times)}/{len(keys)}")
    return names, times, keys


def _angle(key: Any) -> float:
    # Bezier form: [angle, [3, dt, da], [3, dt, da]]; simple form: angle
    if isinstance(key, (list, tuple)):
        key = key[0]
    return float(key)


def convert(source: str, name: str, posture: str = "Stand", description: str = "") -> Animation:
    names, times, keys = parse_export(source)
    tracks: Dict[str, Dict[str, List[float]]] = {}
    for joint, ts, ks in zip(names, times, keys):
        if joint in tracks:
            raise ChoregrapheImportError(f"duplicate track for {joint}")
        angles = [_angle(k) for k in ks]
        if joint not in HAND_JOINTS:
            angles = [round(math.degrees(a), 2) for a in angles]
        tracks[joint] = {"times": [round(float(t), 3) for t in ts], "angles_deg": angles}
    duration = max((t["times"][-1] for t in tracks.values() if t["times"]), default=0.0)
    return Animation.model_validate({
        "name": name,
        "description": description or "Imported from Choregraphe timeline",
        "author": "human",
        "reviewed_by": "",
        "posture": posture,
        "duration_s": duration,
        "uses_legs": any(j in LEG_JOINTS for j in tracks),
        "interpolation": "bezier",
        "tracks": tracks,
    })


def import_file(export_path: str | Path, name: str, out_dir: str | Path,
                posture: str = "Stand") -> Path:
    source = Path(export_path).read_text(encoding="utf-8")
    anim = convert(source, name, posture)
    out = Path(out_dir) / f"{name}.anim.yaml"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(anim.to_yaml(), encoding="utf-8")
    return out
