"""Consent registry for enrolled faces (SPEC §18.6). A local file, and nothing else is kept."""

from __future__ import annotations

import json
import os
import stat
import tempfile
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import List, Optional

DEFAULT_PATH = Path("~/.naollm/consent.json")
FIELDS = ("name", "consent_form_id", "enrolled_at", "delete_after")


class RegistryDamaged(Exception):
    """The file cannot be parsed. It stays as it is: it may be the only proof of consent."""


@dataclass(frozen=True)
class ConsentRecord:
    name: str
    consent_form_id: str
    enrolled_at: str   # ISO date
    delete_after: str  # ISO date

    def overdue(self, today: date) -> bool:
        return date.fromisoformat(self.delete_after) < today


class ConsentRegistry:
    def __init__(self, path: Path | str = DEFAULT_PATH) -> None:
        self.path = Path(path).expanduser()

    def load(self) -> List[ConsentRecord]:
        """Raises RegistryDamaged. A missing file means no records."""
        try:
            text = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return []
        except (OSError, UnicodeDecodeError) as err:
            raise RegistryDamaged(f"cannot read {self.path}: {err}") from None
        try:
            data = json.loads(text)
            if not isinstance(data, list):
                raise ValueError("top level is not a list")
            records = []
            for item in data:
                if not isinstance(item, dict) or set(item) != set(FIELDS):
                    raise ValueError("a record has the wrong fields")  # no values: this text reaches the log
                if not all(isinstance(item[k], str) for k in FIELDS):
                    raise ValueError("record fields must be text")
                date.fromisoformat(item["enrolled_at"])
                date.fromisoformat(item["delete_after"])
                records.append(ConsentRecord(**item))
        except ValueError as err:
            raise RegistryDamaged(f"{self.path} is damaged: {err}") from None
        return records

    def find(self, name: str) -> Optional[ConsentRecord]:
        return next((r for r in self.load() if r.name == name), None)

    def add(self, record: ConsentRecord) -> None:
        self._write(self.load() + [record])

    def remove(self, name: str) -> None:
        self._write([r for r in self.load() if r.name != name])

    def clear(self) -> None:
        self.load()  # refuses to overwrite a damaged file
        self._write([])

    def overdue(self, today: Optional[date] = None) -> List[ConsentRecord]:
        today = today or date.today()
        return [r for r in self.load() if r.overdue(today)]

    def _write(self, records: List[ConsentRecord]) -> None:
        """Atomic, and readable and writable only by the owner."""
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=self.path.name, suffix=".tmp")
        try:
            os.fchmod(fd, stat.S_IRUSR | stat.S_IWUSR)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump([asdict(r) for r in records], f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
