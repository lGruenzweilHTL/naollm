"""Command line entry point (SPEC §18.1)."""

from __future__ import annotations

import argparse
import asyncio
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable, List, Optional

from naollm.config import Config, ConfigError, cloud_warning, load_config, startup_problems


REST_POLL_S = 5.0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="naollm", description="NAO 6 x LLM bridge")
    sub = parser.add_subparsers(dest="command", required=True)

    for name, help_text in (
        ("run", "bridge + conversation loop + operator console"),
        ("admin", "robot connection + operator console, no LLM"),
    ):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("--config", default="config.yaml", help="path to the YAML config")
        p.add_argument("--dry-run", action="store_true", help="use the fake robot")

    anim = sub.add_parser("anim", help="keyframe animation tools")
    anim_sub = anim.add_subparsers(dest="anim_command", required=True)
    v = anim_sub.add_parser("validate", help="validate animations")
    v.add_argument("name", nargs="?", help="animation name")
    v.add_argument("--all", action="store_true", help="validate every animation")
    lst = anim_sub.add_parser("list", help="list animations with review status")
    imp = anim_sub.add_parser("import", help="convert a Choregraphe Python timeline export")
    imp.add_argument("export", help="exported .py file")
    imp.add_argument("--name", required=True, help="animation name (lowercase, _)")
    imp.add_argument("--posture", default="Stand", choices=["Stand", "Sit", "Crouch"])
    for p in (v, lst, imp):
        p.add_argument("--dir", default="animations", help="animation directory")
        p.add_argument("--profile", default=None, help="robot profile with joint limits")

    ini = sub.add_parser("init", help="check and prepare a robot, with no motors")
    ini.add_argument("--robot", required=True, help="robot IP or hostname")
    ini.add_argument("--config", default="config.yaml", help="path to the YAML config")
    ini.add_argument("--skip-selftest", action="store_true", help="leave out the interactive self-test")
    ini.add_argument("--only", choices=["languages", "settings", "profile", "animations", "selftest"],
                     help="connect, then run this step only")
    ini.add_argument("--dry-run", action="store_true", help="use the fake robot and write no file")

    ev = sub.add_parser("eval", help="tool-call evaluation against the configured LLM")
    ev.add_argument("cases", nargs="?", default="tests/eval/tool_calls.yaml")
    ev.add_argument("--config", default="config.yaml")
    ev.add_argument("--profiles", default=None, help="profile overlay directory")
    ev.add_argument("--verbose", "-v", action="store_true")
    return parser


def _load(path: str) -> Config:
    config = load_config(path)
    problems = startup_problems(config)
    if problems:
        raise ConfigError("refusing to start:\n  " + "\n  ".join(problems))
    return config


def keyframe_loader(config: Config, robot_id: Callable[[], Optional[str]]):
    """`robot_id` is called when loading: the real robot only knows it after connecting."""
    from naollm.animations.library import find_profile, load_library, playable
    from naollm.animations.limits import load_limits

    def load():
        rid = robot_id()
        profile = find_profile(config.robot_profile_dir, rid) if rid else None
        entries = load_library(config.animations_dir, load_limits(profile))
        return playable(entries, config.event_mode)

    return load


async def run_app(config: Config, mode: str, dry_run: bool, config_path: str = "",
                  interactive: Optional[bool] = None, robot: Any = None,
                  consent_path: Optional[Path] = None) -> int:
    from naollm.admin.cli import Console
    from naollm.admin.commands import Commands
    from naollm.admin.consent import DEFAULT_PATH, ConsentRegistry
    from naollm.admin.faces import Faces
    from naollm.audio.listener import Listener
    from naollm.audio.stt import SpeechToText
    from naollm.bridge import Bridge, RobotNotAtRest
    from naollm.llm.client import OpenAIChatClient
    from naollm.llm.loop import ConversationLoop
    from naollm.llm.residency import ModelResidency, ModelResidencyError, in_daemon_thread
    from naollm.robot.adapter import RobotOffline
    from naollm.robot.fake_adapter import FakeRobot
    from naollm.robot.qi_adapter import QiRobot, QiUnavailable, RobotConnectionError

    if dry_run:
        targets = [t for t in list(config.dances.values()) + list(config.gestures.values())
                   if not t.startswith("anim:")]
        robot = FakeRobot(installed_behaviors=targets, printer=print)
    else:
        if config.motion.motors_enabled:
            print("Refusing to start: real motion is not implemented yet. "
                  "Set motion.motors_enabled: false, or use --dry-run.", file=sys.stderr)
            return 2
        if robot is None:
            robot = QiRobot(listen_ip=config.robot.listen_ip)
    bridge = Bridge(config, robot, keyframe_loader=keyframe_loader(config, lambda: robot.info().robot_id))
    bridge.log.subscribe(lambda entry: print(entry.render(), flush=True))
    if not dry_run:
        robot.log = lambda kind, message: bridge.log.log(kind, message)
    warning = cloud_warning(config)
    if warning:
        bridge.log.log("alert", warning)

    residency = ModelResidency(config.llm)
    client = OpenAIChatClient(config.llm, residency) if mode == "run" else None
    conversation = ConversationLoop(bridge, client) if client else None
    stt = SpeechToText(config.stt) if conversation is not None else None
    listener = Listener(bridge, conversation, stt)
    # A dry run never touches the real consent file: its faces do not exist.
    dry_dir = tempfile.TemporaryDirectory(prefix="naollm-dry-run-") if dry_run else None
    registry = ConsentRegistry(Path(dry_dir.name) / "consent.json" if dry_dir else consent_path or DEFAULT_PATH)
    commands = Commands(bridge, mode, out=print, confirm=None, conversation=conversation,  # type: ignore[arg-type]
                        config_path=config_path, listener=listener, model=residency)
    faces = Faces(bridge, registry, commands, listener)
    commands.faces = faces
    console = Console(bridge, commands, interactive=interactive)
    console.on_start = faces.startup_check

    async def idle_monitor() -> None:
        while True:
            await asyncio.sleep(1.0)
            await asyncio.to_thread(bridge.idle_tick)

    async def rest_monitor() -> None:
        while True:
            await asyncio.sleep(REST_POLL_S)
            await asyncio.to_thread(bridge.rest_tick)

    async def warm_model() -> None:
        bridge.log.log("llm", f"loading model {config.llm.model} (kept loaded until exit)")
        try:
            await in_daemon_thread(residency.load)
        except ModelResidencyError as err:
            bridge.log.log("alert", f"model warm-up failed: {err}. The first turn will be slower")
            return
        bridge.log.log("llm", f"model {config.llm.model} loaded")

    async def release_model() -> None:
        if not residency.held:
            return
        try:
            await asyncio.to_thread(residency.unload)
            print(f"model {config.llm.model} unloaded", flush=True)
        except ModelResidencyError as err:
            print(f"model unload failed: {err}", file=sys.stderr, flush=True)

    monitors: List[asyncio.Task] = []
    reason = "exception"
    try:
        try:
            bridge.startup(greet=False)
        except (RobotNotAtRest, RobotConnectionError, QiUnavailable, RobotOffline) as err:
            print(f"Refusing to start: {err}", file=sys.stderr)
            bridge.mark_not_started()
            return 2
        # A dry run loads the speech model at the first `listen`, so prompt tuning needs no download.
        warm = asyncio.get_running_loop().create_task(warm_model()) if mode == "run" else None
        await listener.prepare(load_model=not dry_run)
        if warm is not None:
            await warm
        bridge.greet()
        loop = asyncio.get_running_loop()
        monitors = [loop.create_task(idle_monitor()), loop.create_task(rest_monitor())]
        print(f"naollm ready (mode={mode}{', dry-run' if dry_run else ''}). Type 'help'.", flush=True)
        reason = await console.run()
    except BaseException:
        bridge.estop("exception")
        raise
    finally:
        for monitor in monitors:
            monitor.cancel()
        bridge.shutdown()
        await release_model()
        if client is not None:
            await client.close()
        if dry_dir is not None:
            dry_dir.cleanup()
    return 0 if reason in ("quit", "eof", "ctrl_c", "sigterm", "sighup") else 1


def cmd_anim(args: argparse.Namespace) -> int:
    from naollm.animations.import_choregraphe import ChoregrapheImportError, import_file
    from naollm.animations.library import load_entry, load_library
    from naollm.animations.limits import load_limits

    limits = load_limits(args.profile)
    if args.anim_command == "import":
        try:
            out = import_file(args.export, args.name, args.dir, args.posture)
        except (ChoregrapheImportError, OSError) as err:
            print(f"import failed: {err}", file=sys.stderr)
            return 1
        print(f"wrote {out}")
        entry = load_entry(out, limits)
        _print_entry(entry)
        print(f"limits: {limits.source}")
        return 0 if entry.valid else 1

    entries = load_library(args.dir, limits)
    if args.anim_command == "list":
        for e in entries:
            review = f"reviewed by {e.animation.reviewed_by}" if e.reviewed else "NOT reviewed"
            state = "valid" if e.valid else "INVALID"
            print(f"  {e.name:<20} {state:<8} {review}")
        return 0

    if not args.all and not args.name:
        print("give an animation name or --all", file=sys.stderr)
        return 2
    if args.name:
        entries = [e for e in entries if e.name == args.name]
        if not entries:
            print(f"no animation '{args.name}' in {args.dir}", file=sys.stderr)
            return 1
    print(f"limits: {limits.source}")
    bad = 0
    for e in entries:
        if not e.valid:
            bad += 1
            _print_entry(e)
    print(f"{len(entries) - bad} valid, {bad} invalid")
    return 1 if bad else 0


def _print_entry(e) -> None:
    if e.error:
        print(f"{e.name}: {e.error}")
    elif e.violations:
        print(f"{e.name}:")
        for v in e.violations:
            print(f"  {v}")
    else:
        print(f"{e.name}: valid")


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command in ("run", "admin"):
            config = _load(args.config)
            return asyncio.run(run_app(config, args.command, args.dry_run, args.config))
        if args.command == "anim":
            return cmd_anim(args)
        if args.command == "init":
            from naollm.deploy.init import run_init_cli
            return run_init_cli(args, _load(args.config))
        if args.command == "eval":
            from naollm.eval import run_eval_cli
            return run_eval_cli(args)
    except ConfigError as err:
        print(str(err), file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130
    return 1


if __name__ == "__main__":
    sys.exit(main())
