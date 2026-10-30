"""Command line entry point (SPEC §18.1)."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from typing import List, Optional

from naollm.config import Config, ConfigError, cloud_warning, load_config, startup_problems


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


def keyframe_loader(config: Config, robot_id: Optional[str] = None):
    from naollm.animations.library import find_profile, load_library, playable
    from naollm.animations.limits import load_limits

    def load():
        profile = find_profile(config.robot_profile_dir, robot_id) if robot_id else None
        entries = load_library(config.animations_dir, load_limits(profile))
        return playable(entries, config.event_mode)

    return load


async def run_app(config: Config, mode: str, dry_run: bool, config_path: str = "",
                  interactive: Optional[bool] = None) -> int:
    from naollm.admin.cli import Console
    from naollm.admin.commands import Commands
    from naollm.bridge import Bridge
    from naollm.llm.client import OpenAIChatClient
    from naollm.llm.loop import ConversationLoop
    from naollm.robot.fake_adapter import FakeRobot

    if not dry_run:
        print("The real robot adapter arrives after the phase 0 spike. Use --dry-run for now.",
              file=sys.stderr)
        return 2

    targets = [t for t in list(config.dances.values()) + list(config.gestures.values())
               if not t.startswith("anim:")]
    robot = FakeRobot(installed_behaviors=targets, printer=print)
    bridge = Bridge(config, robot, keyframe_loader=keyframe_loader(config, robot.info().robot_id))
    bridge.log.subscribe(lambda entry: print(entry.render(), flush=True))
    warning = cloud_warning(config)
    if warning:
        bridge.log.log("alert", warning)

    client = OpenAIChatClient(config.llm) if mode == "run" else None
    conversation = ConversationLoop(bridge, client) if client else None
    commands = Commands(bridge, mode, out=print, confirm=None, conversation=conversation,  # type: ignore[arg-type]
                        config_path=config_path)
    console = Console(bridge, commands, interactive=interactive)

    async def idle_monitor() -> None:
        while True:
            await asyncio.sleep(1.0)
            await asyncio.to_thread(bridge.idle_tick)

    monitor: Optional[asyncio.Task] = None
    reason = "exception"
    try:
        bridge.startup()
        monitor = asyncio.get_running_loop().create_task(idle_monitor())
        print(f"naollm ready (mode={mode}, dry-run). Type 'help'.", flush=True)
        reason = await console.run()
    except BaseException:
        bridge.estop("exception")
        raise
    finally:
        if monitor is not None:
            monitor.cancel()
        bridge.shutdown()
        if client is not None:
            await client.close()
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
