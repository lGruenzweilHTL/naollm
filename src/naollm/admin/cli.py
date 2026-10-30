"""Operator console (SPEC §18): prompt, hotkeys, Ctrl+C and signal handling."""

from __future__ import annotations

import asyncio
import signal
import sys
import time
from typing import TYPE_CHECKING, Callable, Optional, Set

from prompt_toolkit import PromptSession
from prompt_toolkit.application import get_app
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.filters import Condition
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.patch_stdout import patch_stdout

from naollm.admin.commands import Commands, completions

if TYPE_CHECKING:
    from naollm.bridge import Bridge

CTRL_C_WINDOW_S = 3.0
INLINE = frozenset({"resume", "quit", "help", "status", "tools", "behaviors"})


class CtrlC:
    """First press: emergency stop. Second press within 3 s: quit."""

    def __init__(self, estop: Callable[[str], None], out: Callable[[str], None],
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.estop = estop
        self.out = out
        self.clock = clock
        self._last: Optional[float] = None

    def press(self) -> bool:
        now = self.clock()
        if self._last is not None and now - self._last <= CTRL_C_WINDOW_S:
            self.out("Ctrl+C again: shutting down")
            return True
        self._last = now
        self.estop("sigint")
        self.out("Press Ctrl+C again within 3 s to quit")
        return False


class CommandCompleter(Completer):
    def __init__(self, bridge: "Bridge") -> None:
        self.bridge = bridge

    def get_completions(self, document, complete_event):
        text = document.text_before_cursor
        words = text.split()
        current = "" if text.endswith(" ") or not words else words[-1]
        before = words if text.endswith(" ") else words[:-1]
        for option in completions(self.bridge, before, current):
            yield Completion(option, start_position=-len(current))


class Console:
    def __init__(self, bridge: "Bridge", commands: Commands, interactive: Optional[bool] = None) -> None:
        self.bridge = bridge
        self.commands = commands
        self.interactive = sys.stdin.isatty() if interactive is None else interactive
        self.ctrl_c = CtrlC(bridge.estop, self.print)
        self.exit_reason: Optional[str] = None
        self._tasks: Set[asyncio.Task] = set()
        self._session: Optional[PromptSession] = None
        self._main: Optional[asyncio.Task] = None
        self._lines: Optional[asyncio.StreamReader] = None

    # -- output --------------------------------------------------------
    def print(self, text: str) -> None:
        print(text, flush=True)

    def toolbar(self) -> str:
        return " " + self.commands.status_line()

    # -- prompt_toolkit session ----------------------------------------
    def key_bindings(self) -> KeyBindings:
        kb = KeyBindings()

        @kb.add("c-e", eager=True)
        def _estop(event) -> None:
            self.bridge.estop("hotkey")

        @kb.add(" ", filter=Condition(lambda: not get_app().current_buffer.text))
        def _listen(event) -> None:
            self.bridge.request_listen("key")

        return kb

    def make_session(self, **kwargs) -> PromptSession:
        return PromptSession(
            key_bindings=self.key_bindings(),
            completer=CommandCompleter(self.bridge),
            bottom_toolbar=self.toolbar,
            refresh_interval=1.0,
            **kwargs,
        )

    async def _confirm(self, question: str) -> str:
        if self.interactive and self._session is not None:
            try:
                return await self._session.prompt_async(question)
            except (KeyboardInterrupt, EOFError):
                return ""
        self.print(question.rstrip())
        line = await self._read_plain_line()
        return line or ""

    # -- main loop -----------------------------------------------------
    def install_signal_handlers(self) -> None:
        loop = asyncio.get_running_loop()
        try:
            loop.add_signal_handler(signal.SIGINT, self._on_sigint)
            for sig in (signal.SIGTERM, signal.SIGHUP):
                loop.add_signal_handler(sig, self._on_terminate, sig.name.lower())
        except (NotImplementedError, AttributeError):  # Windows
            signal.signal(signal.SIGINT, lambda *_: loop.call_soon_threadsafe(self._on_sigint))

    def _on_sigint(self) -> None:
        if self.ctrl_c.press():
            self._request_exit("ctrl_c")

    def _on_terminate(self, source: str) -> None:
        self.bridge.estop(source)
        self._request_exit(source)

    def _request_exit(self, reason: str) -> None:
        if self.exit_reason is None:
            self.exit_reason = reason
        if self._main is not None and not self._main.done():
            self._main.cancel()

    async def run(self) -> str:
        """Returns the exit reason: quit, eof, ctrl_c, sigterm, sighup."""
        self.commands.confirm = self._confirm
        self._main = asyncio.current_task()
        self.install_signal_handlers()
        try:
            if self.interactive:
                await self._run_interactive()
            else:
                await self._run_plain()
        except asyncio.CancelledError:
            if self.exit_reason is None:
                raise
        finally:
            for task in list(self._tasks):
                task.cancel()
        return self.exit_reason or "quit"

    async def _run_interactive(self) -> None:
        self._session = self.make_session()
        with patch_stdout(raw=True):
            while self.exit_reason is None:
                try:
                    line = await self._session.prompt_async("naollm> ")
                except KeyboardInterrupt:
                    if self.ctrl_c.press():
                        self.exit_reason = "ctrl_c"
                    continue
                except EOFError:
                    self.exit_reason = "eof"
                    break
                if not await self.dispatch(line):
                    self.exit_reason = "quit"

    async def _read_plain_line(self) -> Optional[str]:
        if self._lines is None:
            loop = asyncio.get_running_loop()
            reader = asyncio.StreamReader()
            await loop.connect_read_pipe(lambda: asyncio.StreamReaderProtocol(reader), sys.stdin)
            self._lines = reader
        raw = await self._lines.readline()
        if not raw:
            return None
        return raw.decode("utf-8", errors="replace").rstrip("\n")

    async def _run_plain(self) -> None:
        # Scripted input (pipe): commands run one after another.
        while self.exit_reason is None:
            line = await self._read_plain_line()
            if line is None:
                self.exit_reason = "eof"
                break
            if line.strip():
                self.print(f"naollm> {line}")
            if not await self.commands.execute(line):
                self.exit_reason = "quit"

    async def dispatch(self, line: str) -> bool:
        """Inline for short commands; everything else runs in the background so
        the prompt (and Ctrl+E) stays available while the robot moves."""
        name = line.strip().split(" ", 1)[0].lower()
        if not name:
            return True
        if name in INLINE:
            return await self.commands.execute(line)
        task = asyncio.get_running_loop().create_task(self.commands.execute(line))
        self._tasks.add(task)
        task.add_done_callback(self._task_done)
        return True

    def _task_done(self, task: asyncio.Task) -> None:
        self._tasks.discard(task)
        if not task.cancelled() and task.exception() is not None:
            self.print(f"error: {task.exception()!r}")

    async def wait_idle(self) -> None:
        """Wait for background commands (used by scripted sessions and tests)."""
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)
