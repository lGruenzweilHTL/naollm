"""Split streamed text into sentences so they can be spoken early (SPEC §11.3)."""

from __future__ import annotations

import re
from typing import List

_BOUNDARY = re.compile(r"[.!?…]+[\"'”»)]*\s+")
_ABBREVIATIONS = frozenset({
    "z.b", "bzw", "usw", "ca", "dr", "prof", "nr", "str", "vgl", "evtl", "d.h", "u.a",
    "mr", "mrs", "ms", "e.g", "i.e", "vs", "st",
})


def _is_false_boundary(text: str, dot_pos: int) -> bool:
    if text[dot_pos] != ".":
        return False
    word = text[:dot_pos].split()[-1:] or [""]
    token = word[0].lower().lstrip("(\"'")
    if token in _ABBREVIATIONS:
        return True
    # Ordinals and dates: "am 3. Mai"
    return token.isdigit()


class SentenceSplitter:
    def __init__(self) -> None:
        self._buf = ""

    def feed(self, delta: str) -> List[str]:
        self._buf += delta
        out: List[str] = []
        start = 0
        for m in _BOUNDARY.finditer(self._buf):
            if _is_false_boundary(self._buf, m.start()):
                continue
            sentence = self._buf[start:m.end()].strip()
            if sentence:
                out.append(sentence)
            start = m.end()
        self._buf = self._buf[start:]
        return out

    def flush(self) -> List[str]:
        rest = self._buf.strip()
        self._buf = ""
        return [rest] if rest else []
