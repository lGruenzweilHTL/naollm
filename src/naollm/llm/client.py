"""OpenAI-compatible chat-completions client with streaming (SPEC §7.1)."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Dict, List, Optional, Protocol

from naollm.config import LLMConfig


class LLMUnavailable(Exception):
    """Timeout or error from the endpoint."""


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: str


@dataclass(frozen=True)
class StreamEvent:
    """One piece of a streamed answer.

    kind is "reasoning" (thinking text, never spoken), "text" (the answer),
    "tool" (a tool call has started; text is its name) or "done" (calls,
    finish reason and token usage, when the endpoint reports them).
    """

    kind: str
    text: str = ""
    calls: List[ToolCall] = field(default_factory=list)
    finish: str = ""
    usage: Optional[Dict[str, int]] = None


class ChatClient(Protocol):
    def stream(self, messages: List[Dict[str, Any]],
               tools: Optional[List[Dict[str, Any]]]) -> AsyncIterator[StreamEvent]: ...


class ToolCallAccumulator:
    """Collects tool-call deltas.

    OpenAI sends fragments keyed by `index` (name once, arguments in pieces).
    Ollama sends each call complete in one delta, sometimes without a distinct
    index. A delta with a new `id` at an occupied index starts a new call.
    """

    def __init__(self) -> None:
        self._calls: Dict[int, Dict[str, str]] = {}
        self._last: Optional[int] = None

    def add(self, deltas: Any) -> None:
        for tc in deltas or []:
            fn = getattr(tc, "function", None)
            name = getattr(fn, "name", None) or ""
            args = getattr(fn, "arguments", None) or ""
            call_id = getattr(tc, "id", None) or ""
            idx = getattr(tc, "index", None)
            if idx is None:
                idx = self._next_index() if (call_id or name or self._last is None) else self._last
            entry = self._calls.get(idx)
            if entry is not None and call_id and entry["id"] and call_id != entry["id"]:
                idx = self._next_index()
                entry = None
            if entry is None:
                entry = self._calls[idx] = {"id": "", "name": "", "arguments": ""}
            if call_id:
                entry["id"] = call_id
            if name and not entry["name"]:
                entry["name"] = name
            entry["arguments"] += args
            self._last = idx

    def names(self) -> List[str]:
        return [self._calls[i]["name"] for i in sorted(self._calls) if self._calls[i]["name"]]

    def _next_index(self) -> int:
        return max(self._calls) + 1 if self._calls else 0

    def result(self) -> List[ToolCall]:
        out = []
        for n, idx in enumerate(sorted(self._calls)):
            c = self._calls[idx]
            out.append(ToolCall(c["id"] or f"call_{n}", c["name"], c["arguments"]))
        return out


class OpenAIChatClient:
    def __init__(self, cfg: LLMConfig, residency: Any = None) -> None:
        from openai import AsyncOpenAI

        self.cfg = cfg
        self.residency = residency  # a ModelResidency, or None
        self._client = AsyncOpenAI(base_url=cfg.base_url, api_key=cfg.api_key,
                                   timeout=cfg.timeout_s, max_retries=0)

    async def stream(self, messages: List[Dict[str, Any]],
                     tools: Optional[List[Dict[str, Any]]]) -> AsyncIterator[StreamEvent]:
        import openai

        kwargs: Dict[str, Any] = dict(
            model=self.cfg.model,
            messages=messages,
            temperature=self.cfg.temperature,
            max_tokens=self.cfg.max_tokens,
            stream=True,
            stream_options={"include_usage": True},
        )
        if self.cfg.reasoning_effort is not None:
            kwargs["reasoning_effort"] = self.cfg.reasoning_effort
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
        if self.residency is not None and self.residency.held:
            kwargs["extra_body"] = {"keep_alive": -1}  # Ollama: do not unload the model we hold
        timeout = self.cfg.timeout_s
        acc = ToolCallAccumulator()
        finish = ""
        usage: Optional[Dict[str, int]] = None
        try:
            response = await asyncio.wait_for(self._client.chat.completions.create(**kwargs), timeout)
            iterator = response.__aiter__()
            while True:
                try:
                    chunk = await asyncio.wait_for(iterator.__anext__(), timeout)
                except StopAsyncIteration:
                    break
                if getattr(chunk, "usage", None) is not None:
                    usage = {k: int(getattr(chunk.usage, k, 0) or 0)
                             for k in ("prompt_tokens", "completion_tokens")}
                if not chunk.choices:
                    continue
                choice = chunk.choices[0]
                if choice.finish_reason:
                    finish = choice.finish_reason
                delta = choice.delta
                if delta is None:
                    continue
                # Ollama sends thinking as `reasoning`, vLLM and others as `reasoning_content`.
                reasoning = getattr(delta, "reasoning", None) or getattr(delta, "reasoning_content", None)
                if isinstance(reasoning, str) and reasoning:
                    yield StreamEvent("reasoning", text=reasoning)
                if delta.content:
                    yield StreamEvent("text", text=delta.content)
                if delta.tool_calls:
                    known = len(acc.names())
                    acc.add(delta.tool_calls)
                    for name in acc.names()[known:]:
                        yield StreamEvent("tool", text=name)
        except asyncio.TimeoutError:
            raise LLMUnavailable(f"no answer within {timeout} s") from None
        except openai.OpenAIError as err:
            raise LLMUnavailable(f"{type(err).__name__}: {err}") from None
        yield StreamEvent("done", calls=acc.result(), finish=finish, usage=usage)

    async def close(self) -> None:
        await self._client.close()
