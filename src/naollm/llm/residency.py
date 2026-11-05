"""Keeps the Ollama model loaded while the bridge runs (design D12).

Ollama unloads a model 5 minutes after its last request. This loads it with
`keep_alive: -1` through the native API, and unloads it with `keep_alive: 0`.
"""

from __future__ import annotations

import asyncio
import json
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional, TypeVar

from naollm.config import LLMConfig

LOAD_TIMEOUT_S = 300.0   # a cold load from a slow disk
REQUEST_TIMEOUT_S = 10.0

T = TypeVar("T")


class ModelResidencyError(RuntimeError):
    """The native API is missing, unreachable, or refused the request."""


@dataclass(frozen=True)
class ModelStatus:
    loaded: bool
    expires_at: Optional[str] = None


def native_root(base_url: str) -> str:
    """`http://localhost:11434/v1` -> `http://localhost:11434`."""
    root = base_url.rstrip("/")
    return root[: -len("/v1")] if root.endswith("/v1") else root


async def in_daemon_thread(fn: Callable[[], T]) -> T:
    """Like `asyncio.to_thread`, but a slow call cannot hold up the process exit."""
    loop = asyncio.get_running_loop()
    future: asyncio.Future = loop.create_future()

    def run() -> None:
        try:
            result = fn()
        except BaseException as err:  # noqa: BLE001 - handed to the waiting coroutine
            # Bound as a default: Python deletes `err` when the except block ends.
            loop.call_soon_threadsafe(lambda e=err: future.done() or future.set_exception(e))
        else:
            loop.call_soon_threadsafe(lambda: future.done() or future.set_result(result))

    threading.Thread(target=run, daemon=True, name="naollm-model").start()
    return await future


class ModelResidency:
    def __init__(self, cfg: LLMConfig) -> None:
        self.cfg = cfg
        self.root = native_root(cfg.base_url)
        # True from a load request until an unload: chat requests then ask to keep the model.
        self.held = False

    def _request(self, path: str, body: Optional[Dict[str, Any]], timeout: float) -> Dict[str, Any]:
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(self.root + path, data=data, method="POST" if data else "GET",
                                         headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read() or b"{}")
        except urllib.error.HTTPError as err:
            try:
                detail = json.loads(err.read()).get("error", "")
            except Exception:
                detail = ""
            raise ModelResidencyError(f"{self.root}{path} answered {err.code}" + (f": {detail}" if detail else "")
                                      + "; is this an Ollama endpoint?") from None
        except (urllib.error.URLError, OSError, ValueError) as err:
            reason = getattr(err, "reason", err)
            raise ModelResidencyError(f"cannot reach {self.root}{path}: {reason}") from None

    def load(self) -> None:
        """Blocks until the model is in memory. Run it with `in_daemon_thread`."""
        self.held = True
        try:
            reply = self._request("/api/generate", {"model": self.cfg.model, "keep_alive": -1}, LOAD_TIMEOUT_S)
        except ModelResidencyError:
            self.held = False
            raise
        if reply.get("error"):
            self.held = False
            raise ModelResidencyError(str(reply["error"]))

    def unload(self) -> None:
        self._request("/api/generate", {"model": self.cfg.model, "keep_alive": 0}, REQUEST_TIMEOUT_S)
        self.held = False

    def status(self) -> ModelStatus:
        for model in self._request("/api/ps", None, REQUEST_TIMEOUT_S).get("models") or []:
            if self.cfg.model in (model.get("name"), model.get("model")):
                return ModelStatus(True, model.get("expires_at"))
        return ModelStatus(False)
