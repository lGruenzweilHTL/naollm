"""Test doubles for the LLM endpoint."""

from __future__ import annotations

import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List, Optional, Union

from naollm.llm.client import LLMUnavailable, StreamEvent, ToolCall

Step = Union[str, ToolCall, asyncio.Event, Exception]


def call(name: str, args: Any, call_id: Optional[str] = None) -> ToolCall:
    raw = args if isinstance(args, str) else json.dumps(args)
    return ToolCall(call_id or f"call_{name}", name, raw)


class ScriptedClient:
    """Plays back one scripted response per request.

    A response is a list of steps: text chunks, ToolCalls (sent at the end),
    an asyncio.Event to wait on, or an exception to raise.
    """

    def __init__(self, responses: List[List[Step]], repeat_last: bool = False) -> None:
        self.responses = list(responses)
        self.repeat_last = repeat_last
        self.requests: List[Dict[str, Any]] = []

    async def stream(self, messages, tools):
        self.requests.append({"messages": [dict(m) for m in messages], "tools": tools})
        if self.responses:
            steps = self.responses.pop(0) if (len(self.responses) > 1 or not self.repeat_last) else self.responses[0]
        else:
            steps = ["(no script)"]
        calls: List[ToolCall] = []
        for step in steps:
            if isinstance(step, str):
                yield StreamEvent("text", text=step)
                await asyncio.sleep(0)
            elif isinstance(step, ToolCall):
                calls.append(step)
            elif isinstance(step, asyncio.Event):
                await step.wait()
            elif isinstance(step, Exception):
                raise step
        yield StreamEvent("done", calls=calls)


def sse_chunks(deltas: List[Dict[str, Any]]) -> bytes:
    out = []
    for i, delta in enumerate(deltas):
        chunk = {
            "id": "chatcmpl-1", "object": "chat.completion.chunk", "created": 0, "model": "stub",
            "choices": [{"index": 0, "delta": delta, "finish_reason": None}],
        }
        out.append(f"data: {json.dumps(chunk)}\n\n")
    out.append("data: [DONE]\n\n")
    return "".join(out).encode()


class StubServer:
    """Minimal OpenAI-compatible streaming endpoint on 127.0.0.1."""

    def __init__(self, deltas: List[Dict[str, Any]], delay_s: float = 0.0) -> None:
        self.deltas = deltas
        self.delay_s = delay_s
        self.bodies: List[Dict[str, Any]] = []
        stub = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                length = int(self.headers.get("Content-Length", 0))
                stub.bodies.append(json.loads(self.rfile.read(length)))
                if stub.delay_s:
                    threading.Event().wait(stub.delay_s)
                body = sse_chunks(stub.deltas)
                try:
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_address[1]}/v1"

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()


__all__ = ["ScriptedClient", "StubServer", "call", "LLMUnavailable"]
