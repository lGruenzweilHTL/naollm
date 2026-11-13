"""`naollm anim preview`: play one animation slowly on the robot (SPEC §19.5).

Preview is how an animation gets reviewed, so it accepts one without
`reviewed_by`, but never one that fails validation. It plays through the bridge
and the safety gate like any other motion, so the head-touch emergency stop works.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Callable, Optional

from naollm.animations.library import load_entry
from naollm.animations.limits import load_limits
from naollm.animations.model import Animation, Track
from naollm.config import Config

DEFAULT_SPEED = 0.5

Ask = Callable[[str], Optional[str]]
Out = Callable[[str], None]


def slowed(anim: Animation, factor: float) -> Animation:
    """Every keyframe time divided by the factor, so 0.5 plays at half speed."""
    tracks = {joint: Track(times=[t / factor for t in track.times], angles_deg=list(track.angles_deg))
              for joint, track in anim.tracks.items()}
    return anim.model_copy(update={"tracks": tracks, "duration_s": anim.duration_s / factor})


def speed_problem(factor: float) -> Optional[str]:
    if not 0 < factor <= 1:
        return f"--speed must be greater than 0 and at most 1, got {factor:g}"
    return None


def preview(config: Config, name: str, factor: float, robot: Any, out: Out, ask: Ask,
            profile: Optional[str] = None) -> int:
    from naollm.bridge import Bridge, StartupRefused

    problem = speed_problem(factor)
    if problem:
        out(problem)
        return 2
    if not config.motion.motors_enabled:
        out("anim preview: refused, motors are off (motion.motors_enabled is false)")
        return 2
    path = Path(config.animations_dir) / f"{name}.anim.yaml"
    if not path.is_file():
        out(f"no animation '{name}' in {config.animations_dir}")
        return 1
    entry = load_entry(path, load_limits(profile))
    if not entry.valid or entry.animation is None:
        out(f"anim preview: refused, {name} does not pass validation; run 'naollm anim validate {name}'")
        return 1
    if not entry.reviewed:
        out(f"{name} is not reviewed yet: preview it, then set reviewed_by in {path}")
    anim = slowed(entry.animation, factor)
    out(f"{name}: {entry.animation.duration_s:g} s at speed {factor:g} plays for {anim.duration_s:g} s, "
        f"starting from {anim.posture}")
    answer = ask("The robot stands up and plays the animation. Hands near the robot, area clear? [y/N] ")
    if (answer or "").strip().lower() != "y":
        out("anim preview: not confirmed, nothing played")
        return 1

    # Only this animation is in the catalogue, under its own name.
    cfg = config.model_copy(update={"gestures": {name: f"anim:{name}"}, "dances": {}})
    bridge = Bridge(cfg, robot, keyframe_loader=lambda: ({name: anim}, {}))
    bridge.log.subscribe(lambda e: out(e.render()))
    try:
        try:
            bridge.startup(greet=False)
        except StartupRefused as err:
            bridge.mark_not_started()
            out(f"anim preview: refused, {err}")
            return 2
        if anim.posture != "Stand":
            moved = bridge.gate.check_and_run("set_posture", {"posture": anim.posture}, source="admin")
            if not moved.ok:
                out(f"anim preview: {anim.posture} not reached: {moved.error}")
                return 1
        result = bridge.gate.check_and_run("play_gesture", {"name": name}, source="admin")
        if not result.ok:
            out(f"anim preview: refused, {result.error} ({result.reason})")
            return 1
        out(f"anim preview: {name} {result.result}")
        return 0 if result.result == "finished" else 1
    except KeyboardInterrupt:
        bridge.estop("sigint")
        return 130
    finally:
        bridge.shutdown()


def _ask(question: str) -> Optional[str]:
    try:
        return input(question)
    except EOFError:
        return None


def run_preview_cli(args, config: Config) -> int:
    from naollm.main import motion_problem
    from naollm.robot.fake_adapter import FakeRobot
    from naollm.robot.qi_adapter import QiRobot, QiUnavailable, RobotConnectionError

    def out(line: str) -> None:
        print(line, flush=True)

    if args.dry_run:
        robot: Any = FakeRobot(printer=print)
    else:
        problem = motion_problem(config)
        if problem:
            print(f"anim preview: refused, {problem}", file=sys.stderr)
            return 2
        robot = QiRobot(listen_ip=config.robot.listen_ip, motors_enabled=config.motion.motors_enabled)
    try:
        return preview(config, args.name, args.speed, robot, out, _ask, profile=args.profile)
    except (QiUnavailable, RobotConnectionError) as err:
        print(f"anim preview: {err}", file=sys.stderr)
        return 2
