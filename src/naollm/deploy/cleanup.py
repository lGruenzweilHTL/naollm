"""`naollm cleanup`: the after-event deletion checklist (event-readiness spec, SPEC §12.3).

Faces are forgotten on the robot before the consent records go, because the
records are the only list of faces still to delete. The deletion log holds the
date and two counts, never a name, so the proof of deletion is not personal data.
"""

from __future__ import annotations

import datetime
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable, List, Optional

from naollm.config import Config

NAOLLM_DIR = Path("~/.naollm")
CONFIRM_WORD = "DELETE"
AUDIO_SUFFIXES = frozenset({".wav", ".flac", ".mp3", ".ogg", ".opus", ".pcm", ".raw", ".m4a"})
MANUAL_ITEMS = (
    "File the signed consent forms, or destroy them, as the school requires.",
    "Check the laptop for audio recordings or transcripts made outside naollm (none are expected).",
    "Take down the signage and switch privacy.face_recognition_enabled back to false.",
    "Keep the deletion log line as proof, or delete it as the school requires.",
)

Out = Callable[[str], None]
Ask = Callable[[str], Optional[str]]


def leftover_files(naollm_dir: Path, temp_dir: Path) -> List[Path]:
    """Audio or transcript files under ~/.naollm, and anything named naollm* in the temp folder."""
    found: List[Path] = []
    if naollm_dir.is_dir():
        for path in sorted(naollm_dir.rglob("*")):
            if path.is_file() and (path.suffix.lower() in AUDIO_SUFFIXES or "transcript" in path.name.lower()):
                found.append(path)
    if temp_dir.is_dir():
        found += sorted(p for p in temp_dir.iterdir() if p.name.lower().startswith("naollm"))
    return found


class Cleanup:
    def __init__(self, config: Config, out: Out, ask: Ask, robot_factory: Optional[Callable[[], Any]] = None,
                 registry: Any = None, naollm_dir: Path = NAOLLM_DIR, temp_dir: Optional[Path] = None,
                 now: Optional[datetime.datetime] = None) -> None:
        self.config = config
        self.out = out
        self.ask = ask
        self.robot_factory = robot_factory
        self.registry = registry
        self.naollm_dir = Path(naollm_dir).expanduser()
        self.temp_dir = Path(temp_dir or tempfile.gettempdir())
        self.now = now or datetime.datetime.now(datetime.timezone.utc)
        self.failed: List[str] = []

    @property
    def log_path(self) -> Path:
        return self.naollm_dir / "deletions.log"

    def _fail(self, name: str, detail: str) -> None:
        self.failed.append(name)
        self.out(f"✗ {name}: {detail}")

    def run(self) -> int:
        from naollm.admin.consent import DEFAULT_PATH, ConsentRegistry, RegistryDamaged

        self.out("This forgets every learned face on the robot and removes every consent record.")
        answer = self.ask(f"Type {CONFIRM_WORD} to continue: ")
        if (answer or "").strip() != CONFIRM_WORD:
            self.out("cleanup aborted: nothing was deleted")
            return 1
        registry = self.registry or ConsentRegistry(DEFAULT_PATH)
        forgotten = self._forget_faces()
        removed = 0
        if forgotten is None:
            self.out("  the consent records stay: they are the list of faces that must be forgotten first")
        else:
            try:
                removed = len(registry.load())
                registry.clear()
                self.out(f"✓ records: {removed} consent record(s) removed")
            except (RegistryDamaged, OSError) as err:
                self._fail("records", f"{err}; the file was not changed")
            self._write_log(forgotten, removed)
        self._leftovers()
        self.out("Still to do by hand:")
        for item in MANUAL_ITEMS:
            self.out(f"  - {item}")
        if self.failed:
            self.out(f"cleanup NOT complete: {', '.join(self.failed)}")
            return 1
        self.out("cleanup complete")
        return 0

    def _forget_faces(self) -> Optional[int]:
        """The number of faces forgotten, or None when the robot did not do it."""
        try:
            robot = self.robot_factory() if self.robot_factory else self._qi_robot()
            robot.connect(self.config.robot.url)
        except Exception as err:
            self._fail("faces", f"robot not reachable at {self.config.robot.url}: {err}")
            return None
        try:
            count = len(robot.perception.learned_names())
            if not robot.perception.forget_all_faces():
                self._fail("faces", "the robot still lists learned faces after forgetting all")
                return None
            self.out(f"✓ faces: {count} face(s) forgotten on the robot")
            return count
        except Exception as err:
            self._fail("faces", f"forgetting faces failed: {err}")
            return None
        finally:
            try:
                robot.disconnect()
            except Exception as err:
                self.out(f"  disconnect failed: {err}")

    def _qi_robot(self) -> Any:
        from naollm.robot.qi_adapter import QiRobot

        return QiRobot(listen_ip=self.config.robot.listen_ip)  # motors off whatever the config says

    def _write_log(self, faces: int, records: int) -> None:
        line = f"{self.now.isoformat(timespec='seconds')} faces_forgotten={faces} records_removed={records}\n"
        try:
            self.naollm_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            with self.log_path.open("a", encoding="utf-8") as f:
                f.write(line)
            self.out(f"✓ log: one line added to {self.log_path}, with counts only")
        except OSError as err:
            self._fail("log", f"cannot write {self.log_path}: {err}")

    def _leftovers(self) -> None:
        files = leftover_files(self.naollm_dir, self.temp_dir)
        if not files:
            self.out("✓ files: no audio or transcript files left")
            return
        self._fail("files", f"{len(files)} leftover file(s) found; the bridge writes none, so check each")
        for path in files:
            answer = self.ask(f"  Delete {path}? [y/N] ")
            if (answer or "").strip().lower() != "y":
                self.out(f"  kept {path}")
                continue
            try:
                if path.is_dir():
                    shutil.rmtree(path)
                else:
                    path.unlink()
                self.out(f"  deleted {path}")
            except OSError as err:
                self.out(f"  could not delete {path}: {err}")


def _ask(question: str) -> Optional[str]:
    try:
        return input(question)
    except EOFError:
        return None


def run_cleanup_cli(args, config: Config) -> int:
    try:
        return Cleanup(config, lambda line: print(line, flush=True), _ask).run()
    except KeyboardInterrupt:
        print("cleanup interrupted", file=sys.stderr)
        return 130
