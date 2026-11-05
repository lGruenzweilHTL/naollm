"""Operator commands (SPEC §18.4). Pure logic, no terminal handling."""

from __future__ import annotations

import asyncio
import shlex
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Coroutine, Dict, List, Optional

from naollm.config import cloud_warning
from naollm.state import State
from naollm.tools.messages import msg
from naollm.tools.schemas import POSTURES, ToolSpec

if TYPE_CHECKING:
    from naollm.admin.faces import Faces
    from naollm.audio.listener import Listener
    from naollm.bridge import Bridge
    from naollm.llm.loop import ConversationLoop
    from naollm.llm.residency import ModelResidency

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
    "model": "model load|unload|status  keep the LLM loaded in Ollama, release it, or show where it is",
    "reset": "reset  new visitor: clear history and displacement budget",
    "estop": "estop  emergency stop (also Ctrl+E, head touch)",
    "resume": "resume  leave STOPPED/FALLEN/RESTING after confirmation",
    "listen": "listen [--wav <file>]  start listening (also space on an empty line); --wav only in a dry run",
    "ears": "ears on|off|test  wake word on or off (head tap and key keep working), or show confidences for 30 s",
    "reconnect": "reconnect  connect again after a network drop; then type 'resume'",
    "faces": "faces list | enroll <name> | delete <name> | delete --all  consent-backed face records",
    "notice": "notice  robot speaks the privacy notice (not during a turn)",
    "anim": "anim play <name>  play a library animation (refused with motors off)",
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
    elif cmd == "model" and len(words) == 1:
        options = ["load", "unload", "status"]
    elif cmd == "ears" and len(words) == 1:
        options = ["on", "off", "test"]
    elif cmd == "listen" and len(words) == 1:
        options = ["--wav"]
    elif cmd == "faces" and len(words) == 1:
        options = ["list", "enroll", "delete"]
    elif cmd == "faces" and words[1:] == ["delete"]:
        options = ["--all"]
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
        listener: Optional["Listener"] = None,
        faces: Optional["Faces"] = None,
        model: Optional["ModelResidency"] = None,
    ) -> None:
        self.bridge = bridge
        self.mode = mode
        self.out = out
        self.confirm = confirm
        self.conversation = conversation
        self.config_path = config_path
        self.listener = listener
        self.faces = faces
        self.model = model
        # Set by the interactive console: runs a job in the background, so the prompt stays available.
        self.spawn: Optional[Callable[[Coroutine[Any, Any, None]], Any]] = None

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
        except Exception as err:  # for example a robot call on a lost session
            self.out(f"error: {err}")
            self.bridge.log.log("error", f"command failed: {err!r}", source="admin")
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
            battery = f"battery {s.battery_pct} %" if b.config.robot.battery_reliable else "battery hidden"
            robot_part = (f"{'connected' if s.connected else 'DISCONNECTED'} | posture {s.posture} | "
                          f"{battery} | temp {s.thermal}")
        except Exception:
            robot_part = "robot status unavailable"
        llm = "—" if self.conversation is None else ("ON" if b.llm_enabled else "OFF")
        faces = len(b.robot.perception.recognized_names()) if b.config.privacy.face_recognition_enabled else 0
        motors = "" if b.motors_enabled else "motors OFF | "
        return (f"{b.state.state.value} | {motors}{robot_part} | walking {'ON' if b.walking else 'OFF'} | "
                f"LLM {llm} | faces {faces}")

    async def cmd_status(self, args: List[str]) -> None:
        b = self.bridge
        self.out(self.status_line())
        if not b.motors_enabled:
            self.out("  motors are off: the bridge commands no motor")
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
        if not self.bridge.motors_enabled:
            self.out("  motors are off: motion tools are hidden and refused")
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
    def _refuse_with_motors_off(self, command: str) -> bool:
        if self.bridge.motors_enabled:
            return False
        self.out(f"{command}: refused, motors are off")
        self.bridge.log.log("admin", f"{command} refused: motors off", source="admin")
        return True

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
        if self._refuse_with_motors_off("posture"):
            return
        await self.run_tool("set_posture", {"posture": args[0]})

    async def cmd_say(self, args: List[str]) -> None:
        if not args:
            raise CommandError('usage: say "<text>"')
        text = " ".join(args)
        self.bridge.log.log("admin", "say", source="admin", text=text)
        await asyncio.to_thread(self.bridge.robot.speech.say, text)

    async def cmd_wake(self, args: List[str]) -> None:
        b = self.bridge
        if self._refuse_with_motors_off("wake"):
            return
        await asyncio.to_thread(b.robot.motion.wake_up)
        b.state.transition_from(State.RESTING, State.IDLE)
        b.log.log("admin", "wake", source="admin")
        await self.run_tool("set_posture", {"posture": "Stand"})

    async def cmd_rest(self, args: List[str]) -> None:
        b = self.bridge
        if self._refuse_with_motors_off("rest"):
            return
        if b.robot.events.status().posture != "Crouch" and b.state.state not in (State.STOPPED, State.FALLEN):
            await self.run_tool("set_posture", {"posture": "Crouch"})
        await asyncio.to_thread(b.robot.motion.rest)
        if b.state.state != State.FALLEN:
            b.state.force(State.RESTING)
        b.log.log("admin", "rest", source="admin")

    async def cmd_walk(self, args: List[str]) -> None:
        if args not in (["on"], ["off"]):
            raise CommandError("usage: walk on|off")
        if args[0] == "on" and self._refuse_with_motors_off("walk on"):
            return
        self.bridge.set_walking(args[0] == "on")
        self.out(f"walking {args[0].upper()}")

    async def cmd_anim(self, args: List[str]) -> None:
        if len(args) != 2 or args[0] != "play":
            raise CommandError("usage: anim play <name>")
        if self._refuse_with_motors_off("anim play"):
            return
        self.out("anim play: not available; reference the animation in gestures or dances "
                 "and run it with 'tool play_gesture' or 'tool dance'")

    async def cmd_llm(self, args: List[str]) -> None:
        if args not in (["on"], ["off"]):
            raise CommandError("usage: llm on|off")
        if self.conversation is None:
            self.out("no LLM in admin mode")
            return
        self.bridge.llm_enabled = args[0] == "on"
        self.bridge.log.log("admin", f"LLM {args[0].upper()}", source="admin")
        self.out(f"LLM {args[0].upper()}")

    async def cmd_model(self, args: List[str]) -> None:
        from naollm.llm.residency import ModelResidencyError, in_daemon_thread

        if args not in (["load"], ["unload"], ["status"]):
            raise CommandError("usage: model load|unload|status")
        if self.model is None:
            self.out("model control is not available")
            return
        name = self.model.cfg.model
        try:
            if args[0] == "load":
                self.out(f"loading {name} ...")
                await in_daemon_thread(self.model.load)
                self.bridge.log.log("admin", f"model {name} loaded and kept loaded", source="admin")
                self.out(f"{name} loaded; it stays loaded until 'model unload' or exit")
            elif args[0] == "unload":
                if self.bridge.state.state in (State.LISTENING, State.THINKING, State.ACTING) or (
                        self.conversation is not None and self.conversation.busy):
                    self.out(f"model unload: refused during a turn (state {self.bridge.state.state.value})")
                    return
                await asyncio.to_thread(self.model.unload)
                self.bridge.log.log("admin", f"model {name} unloaded", source="admin")
                self.out(f"{name} unloaded; the next turn loads it again with Ollama's default keep-alive")
            else:
                status = await asyncio.to_thread(self.model.status)
                where = f"loaded, expires {status.expires_at or 'never'}" if status.loaded else "not loaded"
                self.out(f"{name}: {where}; held by this bridge: {'yes' if self.model.held else 'no'}")
        except ModelResidencyError as err:
            self.out(f"model {args[0]}: failed, {err}")

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
        problem = b.resume_problem()
        if b.resume():
            self.out("resumed: IDLE")
        else:
            self.out(f"resume refused: {problem or 'state changed'}")

    # -- conversation --------------------------------------------------
    async def cmd_listen(self, args: List[str]) -> None:
        b = self.bridge
        if args and (len(args) != 2 or args[0] != "--wav"):
            raise CommandError("usage: listen [--wav <file>]")
        if args:
            load = getattr(b.robot.audio, "load_wav", None)
            if load is None:
                raise CommandError("--wav only works in a dry run")
            if b.state.state != State.IDLE:
                raise CommandError(f"cannot listen in state {b.state.state.value}")
            try:
                load(args[1])
            except ValueError as err:
                raise CommandError(str(err)) from None
        if self.listener is None:
            b.request_listen("operator")
            return
        task = self.listener.start("operator")
        if task is None:
            return
        try:
            await task
        except asyncio.CancelledError:
            if not task.cancelled():
                raise

    async def cmd_ears(self, args: List[str]) -> None:
        if args not in (["on"], ["off"], ["test"]):
            raise CommandError("usage: ears on|off|test")
        if self.listener is None:
            self.out("the wake word is not available")
            return
        if args[0] == "test":
            await self.listener.ears_test(self.out)
            return
        problem = self.listener.set_ears(args[0] == "on")
        self.out(problem or f"ears {args[0].upper()}")

    # -- faces and privacy -----------------------------------------------
    async def cmd_faces(self, args: List[str]) -> None:
        usage = "usage: faces list | faces enroll <name> | faces delete <name> | faces delete --all"
        if self.faces is None:
            raise CommandError("the consent registry is not available")
        if args == ["list"]:
            await self.faces.list()
        elif len(args) == 2 and args[0] == "enroll":
            await self.faces.enroll(args[1])
        elif args == ["delete", "--all"]:
            await self.faces.delete_all()
        elif len(args) == 2 and args[0] == "delete":
            await self.faces.delete(args[1])
        else:
            raise CommandError(usage)

    async def cmd_notice(self, args: List[str]) -> None:
        b = self.bridge
        if b.state.state != State.IDLE or b.exclusive or (self.conversation is not None and self.conversation.busy):
            self.out(f"notice: refused during a turn (state {b.state.state.value})")
            return
        text = msg("notice", b.language, wake=b.config.audio.wake_words[0] if b.config.audio.wake_words else "NAO")
        if b.motors_enabled:
            text += " " + msg("notice_tracking", b.language)
        b.log.log("admin", "privacy notice spoken", source="admin")
        await asyncio.to_thread(b.robot.speech.say, text, False)

    async def cmd_reconnect(self, args: List[str]) -> None:
        b = self.bridge
        try:
            await asyncio.to_thread(b.reconnect)
        except Exception as err:
            self.out(f"reconnect failed: {err}")
            b.log.log("alert", f"reconnect failed: {err}", source="admin")
            return
        self.out(f"reconnected; state is still {b.state.state.value}. Type 'resume' to continue")

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
        if self.bridge.exclusive:
            self.out(f"{self.bridge.exclusive} is running")
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
