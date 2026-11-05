"""Conversation turn loop (SPEC §7.2)."""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

from naollm.llm.client import ChatClient, LLMUnavailable, ToolCall
from naollm.llm.prompts import system_prompt
from naollm.llm.sentences import SentenceSplitter
from naollm.bridge import TTS_LANGUAGE
from naollm.state import State
from naollm.tools.messages import msg

if TYPE_CHECKING:
    from naollm.bridge import Bridge

class Speaker:
    """Speaks queued sentences one after another on a worker task."""

    def __init__(self, bridge: "Bridge") -> None:
        self.bridge = bridge
        self._queue: "asyncio.Queue[str]" = asyncio.Queue()
        self._worker: Optional[asyncio.Task] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def start(self) -> None:
        loop = asyncio.get_running_loop()
        if self._loop is not loop:  # first use, or a new event loop
            self._loop = loop
            self._queue = asyncio.Queue()
            self._worker = None
        if self._worker is None or self._worker.done():
            self._worker = loop.create_task(self._run())

    async def close(self) -> None:
        if self._worker is not None:
            self._worker.cancel()
            try:
                await self._worker
            except asyncio.CancelledError:
                pass
            self._worker = None

    def say(self, sentence: str) -> None:
        self.start()
        self._queue.put_nowait(sentence)

    async def drain(self) -> None:
        self.start()
        await self._queue.join()

    def clear(self) -> None:
        while True:
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            self._queue.task_done()

    async def _run(self) -> None:
        while True:
            sentence = await self._queue.get()
            try:
                await asyncio.to_thread(self.bridge.robot.speech.say, sentence)
            except Exception as err:
                self.bridge.log.log("error", f"speech failed: {err!r}")
            finally:
                self._queue.task_done()


@dataclass
class TurnResult:
    status: str  # "answered" | "fallback" | "apology" | "refused"
    text: str = ""
    rounds: int = 0
    tool_results: List[Tuple[str, Dict[str, Any]]] = field(default_factory=list)


class ConversationLoop:
    def __init__(self, bridge: "Bridge", client: ChatClient) -> None:
        self.bridge = bridge
        self.client = client
        self.speaker = Speaker(bridge)
        self.history: List[Tuple[str, str]] = []
        self.last_turn_end: Optional[float] = None
        self.requests: List[Dict[str, Any]] = []  # last turn's requests, for tests and debugging
        self._task: Optional[asyncio.Task] = None

    # -- history -------------------------------------------------------
    def reset(self) -> None:
        self.history.clear()
        self.last_turn_end = None

    def _expire_history(self, now: float) -> None:
        cfg = self.bridge.config.conversation
        if self.last_turn_end is not None and now - self.last_turn_end > cfg.idle_reset_s:
            self.reset()

    def _messages(self, user_text: str, lang: str) -> List[Dict[str, Any]]:
        cfg = self.bridge.config.conversation
        messages: List[Dict[str, Any]] = [
            {"role": "system", "content": system_prompt(lang, cfg.school_name, self.bridge.motors_enabled)}
        ]
        turns = self.history[-cfg.history_turns:] if cfg.history_turns else []
        for user, answer in turns:
            messages.append({"role": "user", "content": user})
            messages.append({"role": "assistant", "content": answer})
        messages.append({"role": "user", "content": user_text})
        return messages

    # -- turns ---------------------------------------------------------
    @property
    def busy(self) -> bool:
        return self._task is not None and not self._task.done()

    def start_turn(self, text: str, lang: Optional[str] = None) -> asyncio.Task:
        """Run a turn as a task that the emergency stop can cancel from any thread."""
        loop = asyncio.get_running_loop()
        return self.track(loop.create_task(self.run_turn(text, lang)), loop)

    def track(self, task: asyncio.Task, loop: asyncio.AbstractEventLoop) -> asyncio.Task:
        """Make `task` the current turn: it counts as busy and the emergency stop cancels it."""
        self._task = task

        def cancel() -> None:
            loop.call_soon_threadsafe(task.cancel)

        self.bridge.register_turn(cancel)
        task.add_done_callback(lambda _t: self.bridge.register_turn(None) if self._task is task else None)
        return task

    async def run_turn(self, text: str, lang: Optional[str] = None,
                       now: Optional[float] = None, prepared: bool = False) -> TurnResult:
        """`prepared`: the listener has already entered THINKING for this turn."""
        bridge = self.bridge
        lang = bridge.turn_language(lang or bridge.language)
        started = time.monotonic()
        self._expire_history(time.monotonic() if now is None else now)
        allowed = (State.IDLE, State.LISTENING, State.THINKING) if prepared else (State.IDLE, State.LISTENING)
        if bridge.state.state not in allowed or bridge.exclusive:
            bridge.log.log("turn", f"turn refused in state {bridge.state.state.value}")
            return TurnResult("refused")
        await asyncio.to_thread(bridge.before_turn)
        bridge.state.transition(State.THINKING)
        bridge.log.log("turn", "turn started", text=text, lang=lang)
        try:
            await asyncio.to_thread(bridge.robot.speech.set_language, TTS_LANGUAGE[lang])
        except Exception as err:
            bridge.log.log("warning", f"TTS language switch failed: {err!r}")
        self.speaker.start()
        try:
            result = await self._rounds(text, lang)
        except asyncio.CancelledError:
            self.speaker.clear()
            bridge.log.log("turn", "turn cancelled")
            raise
        finally:
            bridge.state.transition_from(State.THINKING, State.IDLE)
            self.last_turn_end = time.monotonic() if now is None else now
            bridge.touch()
        if result.status in ("answered", "fallback"):
            self.history.append((text, result.text))
        bridge.log.log("turn", f"turn {result.status}", text=result.text or None,
                       rounds=result.rounds, ms=int((time.monotonic() - started) * 1000))
        return result

    async def _rounds(self, text: str, lang: str) -> TurnResult:
        bridge = self.bridge
        max_rounds = bridge.config.llm.max_tool_rounds
        messages = self._messages(text, lang)
        self.requests = []
        tools_allowed = True
        malformed_rounds = 0
        spoken: List[str] = []
        tool_results: List[Tuple[str, Dict[str, Any]]] = []

        for round_no in range(1, max_rounds + 1):
            tools = bridge.registry.schemas(bridge.walking) if tools_allowed else None
            self.requests.append({"messages": list(messages), "tools": tools})
            splitter = SentenceSplitter()
            round_text: List[str] = []
            calls: List[ToolCall] = []
            try:
                async for event in self.client.stream(messages, tools):
                    if event.kind == "text":
                        round_text.append(event.text)
                        for sentence in splitter.feed(event.text):
                            self.speaker.say(sentence)
                            spoken.append(sentence)
                    elif event.kind == "done":
                        calls = event.calls
            except LLMUnavailable as err:
                bridge.log.log("error", f"LLM unavailable: {err}")
                for sentence in splitter.flush():
                    self.speaker.say(sentence)
                self.speaker.say(msg("apology", lang))
                await self.speaker.drain()
                return TurnResult("apology", rounds=round_no, tool_results=tool_results)
            for sentence in splitter.flush():
                self.speaker.say(sentence)
                spoken.append(sentence)

            if not calls or not tools_allowed:
                await self.speaker.drain()
                return TurnResult("answered", " ".join(spoken), round_no, tool_results)

            # Speak first, then act.
            await self.speaker.drain()
            messages.append(self._assistant_message("".join(round_text), calls))
            malformed = False
            for call in calls:
                result = await asyncio.to_thread(
                    bridge.gate.check_and_run, call.name, call.arguments, "llm", lang)
                malformed = malformed or result.malformed
                tool_results.append((call.name, result.as_dict()))
                messages.append({"role": "tool", "tool_call_id": call.id,
                                 "content": json.dumps(result.as_dict(), ensure_ascii=False)})
            if malformed:
                malformed_rounds += 1
                if malformed_rounds >= 2:
                    tools_allowed = False

        fallback = msg("fallback", lang)
        self.speaker.say(fallback)
        await self.speaker.drain()
        return TurnResult("fallback", " ".join(spoken + [fallback]), max_rounds, tool_results)

    @staticmethod
    def _assistant_message(text: str, calls: List[ToolCall]) -> Dict[str, Any]:
        tool_calls = []
        for c in calls:
            try:
                json.loads(c.arguments or "{}")
                args = c.arguments or "{}"
            except ValueError:
                args = "{}"  # keep the history valid for strict endpoints
            tool_calls.append({"id": c.id, "type": "function",
                               "function": {"name": c.name, "arguments": args}})
        return {"role": "assistant", "content": text or None, "tool_calls": tool_calls}
