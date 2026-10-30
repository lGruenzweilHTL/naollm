"""Operator commands (SPEC §18.4). Pure logic, no terminal handling."""

from __future__ import annotations

import asyncio
import shlex
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Dict, List, Optional

from naollm.config import cloud_warning
from naollm.state import State
from naollm.tools.schemas import POSTURES, ToolSpec

if TYPE_CHECKING:
    from naollm.bridge import Bridge
    from naollm.llm.loop import ConversationLoop

HELP: Dict[str, str] = {
    "help": "help [command]  list commands or show details",
    "status": "status  full status dump",
    "tools": "tools  list LLM tools, parameters and whether they are enabled",
    "tool": "tool <name> [args...]  run a tool through the gate; args positional or key=value",
    "say": 'say "<text>"  make NAO speak (animated)',
    "behaviors": "behaviors  installed behaviors and the config entries they map to",
    "posture": "posture <Stand|Sit|Crouch>  shortcut for 'tool set_posture'",
    "wake": "wake  stiffness on and Stand",
    "rest": "rest  Crouch and stiffness off",
    "walk": "walk on|off  walking toggle for turn/step (LLM and admin)",
    "llm": "llm on|off  pause/resume the conversation loop",
    "reset": "reset  new visitor: clear history and displacement budget",
    "estop": "estop  emergency stop (also Ctrl+E, head touch)",
    "resume": "resume  leave STOPPED/FALLEN/RESTING after confirmation",
    "listen": "listen  start listening (also space on an empty line)",
    "chat": "chat [--de|--en] <text>  send typed visitor text to the LLM (run mode)",
    "quit": "quit  safe shutdown and exit",
}

Out = Callable[[str], None]
Confirm = Callable[[str], Awaitable[str]]


class CommandError(Exception):
    pass


@dataclass(frozen=True)
class Parsed:
    name: str
    args: List[str]


def parse_line(line: str) -> Optional[Parsed]:
    try:
        tokens = shlex.split(line)
    except ValueError as err:
        raise CommandError(f"cannot parse line: {err}") from None
    if not tokens:
        return None
    return Parsed(tokens[0].lower(), tokens[1:])


def parse_tool_args(spec: ToolSpec, tokens: List[str]) -> Dict[str, Any]:
    """Positional in schema order, or key=value. Ints are converted; the gate checks ranges."""
    args: Dict[str, Any] = {}
    position = 0
    for token in tokens:
        if "=" in token:
            key, _, raw = token.partition("=")
            param = spec.param(key)
            if param is None:
                raise CommandError(f"{spec.name} has no parameter '{key}'")
        else:
            while position < len(spec.params) and spec.params[position].name in args:
                position += 1
            if position >= len(spec.params):
                raise CommandError(f"too many arguments for {spec.name}")
            param = spec.params[position]
            raw = token
            position += 1
        if param.kind == "int":
            try:
                args[param.name] = int(raw)
            except ValueError:
                raise CommandError(f"{param.name} must be a whole number, got '{raw}'") from None
        else:
            args[param.name] = raw
    return args


def completions(bridge: "Bridge", words: List[str], current: str) -> List[str]:
    """Candidates for the word being typed. `words` are the complete words before it."""
    if not words:
        return [c for c in HELP if c.startswith(current)]
    cmd = words[0]
    if cmd == "help" and len(words) == 1:
        options = list(HELP)
    elif cmd == "tool":
        if len(words) == 1:
            options = list(bridge.registry.specs)
        else:
            spec = bridge.registry.get(words[1])
            options = []
            if spec is not None:
                given = {w.split("=", 1)[0] for w in words[2:] if "=" in w}
                positional = len([w for w in words[2:] if "=" not in w])
                remaining = [p for p in spec.params if p.name not in given][positional:]
                if remaining and remaining[0].kind == "enum":
                    options = list(remaining[0].choices)
                options += [f"{p.name}=" for p in remaining]
    elif cmd == "posture" and len(words) == 1:
        options = list(POSTURES)
    elif cmd in ("walk", "llm") and len(words) == 1:
        options = ["on", "off"]
    elif cmd == "chat" and len(words) == 1:
        options = ["--de", "--en"]
    else:
        options = []
    return [o for o in options if o.startswith(current)]


class Commands:
    def __init__(
        self,
        bridge: "Bridge",
        mode: str,
        out: Out,
        confirm: Confirm,
        conversation: Optional["ConversationLoop"] = None,
        config_path: str = "",
    ) -> None:
        self.bridge = bridge
        self.mode = mode
        self.out = out
        self.confirm = confirm
        self.conversation = conversation
        self.config_path = config_path

    async def execute(self, line: str) -> bool:
        """Run one command line. Returns False when the console should quit."""
        try:
            parsed = parse_line(line)
            if parsed is None:
                return True
            handler = getattr(self, f"cmd_{parsed.name}", None)
            if handler is None:
                self.out(f"unknown command '{parsed.name}'. Type 'help' for the list.")
                return True
            result = await handler(parsed.args)
            return result is not False
        except CommandError as err:
            self.out(f"error: {err}")
            return True

    # -- information ---------------------------------------------------
    async def cmd_help(self, args: List[str]) -> None:
        if args:
            self.out(HELP.get(args[0], f"unknown command '{args[0]}'"))
            return
        for text in HELP.values():
            self.out("  " + text)
        self.out("  hotkeys: Ctrl+E emergency stop, space on empty line = listen, "
                 "Ctrl+C stop (twice within 3 s to quit)")

    def status_line(self) -> str:
        b = self.bridge
        try:
            s = b.robot.events.status()
            robot_part = (f"{'connected' if s.connected else 'DISCONNECTED'} | posture {s.posture} | "
                          f"battery {s.battery_pct} % | temp {s.thermal}")
        except Exception:
            robot_part = "robot status unavailable"
        llm = "—" if self.conversation is None else ("ON" if b.llm_enabled else "OFF")
        faces = len(b.robot.perception.recognized_names()) if b.config.privacy.face_recognition_enabled else 0
        return (f"{b.state.state.value} | {robot_part} | walking {'ON' if b.walking else 'OFF'} | "
                f"LLM {llm} | faces {faces}")

    async def cmd_status(self, args: List[str]) -> None:
        b = self.bridge
        self.out(self.status_line())
        self.out(f"  model {b.config.llm.model} at {b.config.llm.base_url}")
        self.out(f"  installed behaviors: {len(b.robot.motion.installed_behaviors())}")
        self.out(f"  displacement used: {b.displacement_cm} cm of "
                 f"{int(b.config.motion.max_total_displacement_m * 100)} cm")
        self.out(f"  event mode: {b.config.event_mode}; config: {self.config_path or '-'}")
        warning = cloud_warning(b.config)
        if warning:
            self.out(f"  WARNING: {warning}")

    async def cmd_tools(self, args: List[str]) -> None:
        reg = self.bridge.registry
        for spec in reg.specs.values():
            params = ", ".join(
                f"{p.name}: {'|'.join(p.choices) if p.kind == 'enum' else f'{p.minimum}..{p.maximum}'}"
                for p in spec.params) or "-"
            enabled = "LLM" if reg.enabled(spec.name, self.bridge.walking) else "hidden"
            self.out(f"  {spec.name:<13} [{enabled}] {params}")

    async def cmd_behaviors(self, args: List[str]) -> None:
        installed = self.bridge.robot.motion.installed_behaviors()
        mapped = {}
        for section in ("gestures", "dances"):
            for name, target in getattr(self.bridge.config, section).items():
                mapped[target] = f"{section}.{name}"
        for path in sorted(installed):
            self.out(f"  {path}" + (f"  <- {mapped[path]}" if path in mapped else ""))
        missing = [f"{v} ({k})" for k, v in mapped.items() if not k.startswith("anim:") and k not in installed]
        if missing:
            self.out("  missing: " + ", ".join(missing))

    # -- tools and motion ----------------------------------------------
    async def run_tool(self, name: str, args: Dict[str, Any]) -> None:
        result = await asyncio.to_thread(self.bridge.gate.check_and_run, name, args, "admin")
        if result.ok:
            self.out(f"{name}: ok, {result.result}")
        else:
            self.out(f"{name}: refused, {result.error}")
            if result.details and "choices" in result.details:
                self.out(f"  valid choices: {', '.join(result.details['choices'])}")

    async def cmd_tool(self, args: List[str]) -> None:
        if not args:
            raise CommandError("usage: tool <name> [args...]; see 'tools'")
        spec = self.bridge.registry.get(args[0])
        if spec is None:
            raise CommandError(f"unknown tool '{args[0]}'. Tools: {', '.join(self.bridge.registry.specs)}")
        await self.run_tool(spec.name, parse_tool_args(spec, args[1:]))

    async def cmd_posture(self, args: List[str]) -> None:
        if len(args) != 1:
            raise CommandError("usage: posture <Stand|Sit|Crouch>")
        await self.run_tool("set_posture", {"posture": args[0]})

    async def cmd_say(self, args: List[str]) -> None:
        if not args:
            raise CommandError('usage: say "<text>"')
        text = " ".join(args)
        self.bridge.log.log("admin", "say", source="admin", text=text)
        await asyncio.to_thread(self.bridge.robot.speech.say, text)

    async def cmd_wake(self, args: List[str]) -> None:
        b = self.bridge
        await asyncio.to_thread(b.robot.motion.wake_up)
        b.state.transition_from(State.RESTING, State.IDLE)
        b.log.log("admin", "wake", source="admin")
        await self.run_tool("set_posture", {"posture": "Stand"})

    async def cmd_rest(self, args: List[str]) -> None:
        b = self.bridge
        if b.robot.events.status().posture != "Crouch" and b.state.state not in (State.STOPPED, State.FALLEN):
            await self.run_tool("set_posture", {"posture": "Crouch"})
        await asyncio.to_thread(b.robot.motion.rest)
        if b.state.state != State.FALLEN:
            b.state.force(State.RESTING)
        b.log.log("admin", "rest", source="admin")

    async def cmd_walk(self, args: List[str]) -> None:
        if args not in (["on"], ["off"]):
            raise CommandError("usage: walk on|off")
        self.bridge.set_walking(args[0] == "on")
        self.out(f"walking {args[0].upper()}")

    async def cmd_llm(self, args: List[str]) -> None:
        if args not in (["on"], ["off"]):
            raise CommandError("usage: llm on|off")
        if self.conversation is None:
            self.out("no LLM in admin mode")
            return
        self.bridge.llm_enabled = args[0] == "on"
        self.bridge.log.log("admin", f"LLM {args[0].upper()}", source="admin")
        self.out(f"LLM {args[0].upper()}")

    async def cmd_reset(self, args: List[str]) -> None:
        if self.conversation is not None:
            self.conversation.reset()
        self.bridge.displacement_cm = 0
        self.bridge.log.log("admin", "new visitor: history and displacement reset", source="admin")
        self.out("history cleared")

    # -- safety --------------------------------------------------------
    async def cmd_estop(self, args: List[str]) -> None:
        await asyncio.to_thread(self.bridge.estop, "command")

    async def cmd_resume(self, args: List[str]) -> None:
        b = self.bridge
        if b.state.state not in (State.STOPPED, State.FALLEN, State.RESTING, State.OFFLINE):
            self.out(f"nothing to resume (state {b.state.state.value})")
            return
        answer = await self.confirm(f"Resume from {b.state.state.value}? [y/N] ")
        if answer.strip().lower() != "y":
            self.out(f"still {b.state.state.value}")
            return
        if b.resume():
            self.out("resumed: IDLE")
        else:
            self.out("resume refused: robot still reports fallen")

    # -- conversation --------------------------------------------------
    async def cmd_listen(self, args: List[str]) -> None:
        self.bridge.request_listen("operator")

    async def cmd_chat(self, args: List[str]) -> None:
        if self.conversation is None:
            self.out("the conversation loop is not available in admin mode; use 'naollm run'")
            return
        lang: Optional[str] = None
        if args and args[0] in ("--de", "--en"):
            lang, args = args[0][2:], args[1:]
        if not args:
            raise CommandError("usage: chat [--de|--en] <text>")
        if not self.bridge.llm_enabled:
            self.out("LLM is off; type 'llm on'")
            return
        if self.conversation.busy:
            self.out("a turn is still running")
            return
        task = self.conversation.start_turn(" ".join(args), lang)
        try:
            await task
        except asyncio.CancelledError:
            if not task.cancelled():
                raise

    async def cmd_quit(self, args: List[str]) -> bool:
        return False
