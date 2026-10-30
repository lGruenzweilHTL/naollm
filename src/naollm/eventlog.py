"""In-memory event log (SPEC §12.2, §18.2). Nothing is written to disk."""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable, Deque, Dict, List, Optional


@dataclass(frozen=True)
class Entry:
    time: float
    kind: str
    message: str
    source: Optional[str] = None
    text: Optional[str] = None
    fields: Dict[str, Any] = field(default_factory=dict)

    def render(self) -> str:
        stamp = time.strftime("%H:%M:%S", time.localtime(self.time))
        parts = [stamp, self.kind.upper()]
        if self.source:
            parts.append(f"source={self.source}")
        parts.append(self.message)
        parts.extend(f"{k}={v}" for k, v in self.fields.items())
        if self.text is not None:
            parts.append(repr(self.text))
        return " ".join(parts)


class EventLog:
    """`text` holds personal content (transcripts, spoken text, names).

    In event mode it is dropped before the entry is stored or shown.
    """

    def __init__(self, event_mode: bool = False, maxlen: int = 2000) -> None:
        self.event_mode = event_mode
        self._entries: Deque[Entry] = deque(maxlen=maxlen)
        self._subscribers: List[Callable[[Entry], None]] = []
        self._lock = threading.Lock()

    def subscribe(self, fn: Callable[[Entry], None]) -> None:
        self._subscribers.append(fn)

    def log(self, kind: str, message: str = "", *, source: Optional[str] = None,
            text: Optional[str] = None, **fields: Any) -> Entry:
        if self.event_mode:
            text = None
        entry = Entry(time.time(), kind, message, source, text, fields)
        with self._lock:
            self._entries.append(entry)
        for fn in list(self._subscribers):
            try:
                fn(entry)
            except Exception:
                pass
        return entry

    def entries(self, kind: Optional[str] = None) -> List[Entry]:
        with self._lock:
            items = list(self._entries)
        return [e for e in items if kind is None or e.kind == kind]
