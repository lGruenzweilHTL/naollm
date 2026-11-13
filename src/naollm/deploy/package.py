"""`naollm deploy`: build and install the Choregraphe package (SPEC §9, §20.2).

Steps: motors check, manifest check, version bump, `qipkg make-package`,
`qipkg deploy-package`, verify against the configured catalogue. The bump is
undone when the build or the install fails, so the manifest only moves on when a
package with that version reached the robot.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional
from urllib.parse import urlparse

from naollm.config import Config

PROJECT_DIR = Path("choregraphe") / "showcase"
PML = "showcase.pml"
MANIFEST = "manifest.xml"
PACKAGE_ID = "showcase"
# The version attribute of the root element. Only this text changes, so the
# rest of the file, comments and layout included, stays as Choregraphe wrote it.
VERSION_RE = re.compile(r'(<package\b[^>]*?\bversion=")([^"]*)(")', re.S)

PASS = "pass"
FAIL = "fail"
SKIP = "skip"
MARK = {PASS: "✓", FAIL: "✗", SKIP: "-"}

Out = Callable[[str], None]
# Runs a command with the terminal attached and returns its exit code.
Runner = Callable[[List[str]], int]


class DeployError(Exception):
    """A deploy step failed. `hint` says how to fix it."""

    def __init__(self, message: str, hint: str = "") -> None:
        super().__init__(message)
        self.hint = hint


@dataclass(frozen=True)
class DeployStep:
    name: str
    status: str
    detail: str = ""
    hint: str = ""


def run_command(cmd: List[str]) -> int:
    return subprocess.run(cmd, check=False).returncode


def find_qipkg() -> Optional[str]:
    """`qipkg` from PATH, or next to the running Python, as in a `.venv`."""
    found = shutil.which("qipkg")
    if found:
        return found
    local = Path(sys.executable).parent / "qipkg"
    return str(local) if local.is_file() else None


def robot_host(robot: Optional[str], config: Config) -> str:
    if robot:
        if "://" in robot:
            return urlparse(robot).hostname or robot
        return robot
    host = urlparse(config.robot.url).hostname
    if not host:
        raise DeployError(f"robot.url {config.robot.url!r} has no host", "give --robot <ip-or-hostname>")
    return host


def bump_patch(version: str) -> str:
    parts = version.split(".")
    if len(parts) != 3 or not all(p.isdigit() for p in parts):
        raise DeployError(f"manifest version {version!r} is not major.minor.patch", "set version=\"0.0.1\"")
    major, minor, patch = (int(p) for p in parts)
    return f"{major}.{minor}.{patch + 1}"


def parse_manifest(text: str) -> ET.Element:
    try:
        root = ET.fromstring(text)
    except ET.ParseError as err:
        raise DeployError(f"manifest.xml is not valid XML: {err}") from None
    if root.tag != "package":
        raise DeployError(f"manifest.xml root is <{root.tag}>, not <package>")
    if root.get("uuid") != PACKAGE_ID:
        raise DeployError(f"manifest.xml uuid is {root.get('uuid')!r}, not {PACKAGE_ID!r}")
    return root


def auto_start_behaviors(root: ET.Element) -> List[str]:
    """Behaviors with a launch trigger. Choregraphe stores it as a non-empty `autonomous/condition`."""
    found = []
    for content in root.iter("behaviorContent"):
        for condition in content.iter("condition"):
            if (condition.text or "").strip():
                found.append(content.get("path") or "?")
                break
    return found


def with_version(text: str, version: str) -> str:
    new, count = VERSION_RE.subn(lambda m: m.group(1) + version + m.group(3), text, count=1)
    if count != 1:
        raise DeployError("manifest.xml has no version attribute on <package>")
    return new


def write_atomic(path: Path, text: str) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def expected_behaviors(config: Config) -> List[str]:
    """Catalogue entries that the showcase package must provide."""
    targets = list(config.dances.values()) + list(config.gestures.values())
    return sorted({t for t in targets if t.startswith(PACKAGE_ID + "/")})


def installed_from_robot(config: Config, host: str) -> List[str]:
    """Read-only: connects with motors off, whatever the config says."""
    from naollm.robot.qi_adapter import QiRobot

    robot = QiRobot(listen_ip=config.robot.listen_ip)
    robot.connect(f"tcp://{host}:9559")
    try:
        return robot.motion.installed_behaviors()
    finally:
        robot.disconnect()


class Deploy:
    def __init__(self, config: Config, host: str, out: Out, run: Runner = run_command,
                 qipkg: Optional[str] = None, installed: Optional[Callable[[], List[str]]] = None,
                 project_dir: Path = PROJECT_DIR) -> None:
        self.config = config
        self.host = host
        self.out = out
        self.run_cmd = run
        self.qipkg = qipkg
        self.installed = installed or (lambda: installed_from_robot(config, host))
        self.project_dir = Path(project_dir)
        self.steps: List[DeployStep] = []
        self.version: Optional[str] = None

    def _record(self, step: DeployStep) -> None:
        self.steps.append(step)
        self.out(f"{MARK[step.status]} {step.name}: {step.detail}")
        if step.hint:
            self.out(f"    fix: {step.hint}")

    def _fail(self, name: str, err: DeployError) -> int:
        self._record(DeployStep(name, FAIL, str(err), err.hint))
        return 1

    def run(self) -> int:
        """Prints one line per step. Returns 0 when every step passed."""
        if not self.config.motion.motors_enabled:
            return self._fail("motors", DeployError(
                "motors are off: an installed behavior can be started by the robot itself",
                "deploy only with motion.motors_enabled: true, after the on-robot motion checks"))
        manifest = self.project_dir / MANIFEST
        pml = self.project_dir / PML
        try:
            original = manifest.read_text(encoding="utf-8")
            root = parse_manifest(original)
            if not pml.is_file():
                raise DeployError(f"{pml} not found")
            auto = auto_start_behaviors(root)
            if auto:
                raise DeployError(f"auto-start trigger on {', '.join(auto)}",
                                  "remove the launch trigger in Choregraphe; behaviors start only from the bridge")
        except OSError as err:
            return self._fail("manifest", DeployError(f"cannot read {manifest}: {err.strerror}"))
        except DeployError as err:
            return self._fail("manifest", err)
        self._record(DeployStep("manifest", PASS, f"{manifest}, no auto-start behavior"))

        qipkg = self.qipkg or find_qipkg()
        if qipkg is None:
            return self._fail("build", DeployError("qipkg not found", "pip install qibuild in this environment"))
        try:
            old = root.get("version") or ""
            new = bump_patch(old)
            write_atomic(manifest, with_version(original, new))
        except DeployError as err:
            return self._fail("version", err)
        self._record(DeployStep("version", PASS, f"{old} -> {new}"))
        self.version = new

        with tempfile.TemporaryDirectory(prefix="naollm-deploy-") as tmp:
            pkg = Path(tmp) / f"{PACKAGE_ID}-{new}.pkg"
            code = self.run_cmd([qipkg, "make-package", str(pml), "-o", str(pkg)])
            if code != 0 or not pkg.is_file():
                self._undo(manifest, original, old)
                return self._fail("build", DeployError(f"qipkg make-package exited with {code}; nothing was sent",
                                                       "fix the errors above, then run naollm deploy again"))
            self._record(DeployStep("build", PASS, pkg.name))
            code = self.run_cmd([qipkg, "deploy-package", str(pkg), "--url", f"nao@{self.host}"])
            if code != 0:
                self._undo(manifest, original, old)
                return self._fail("install", DeployError(
                    f"qipkg deploy-package exited with {code}",
                    "check SSH access (naollm init sets up the key) and that the robot is on"))
        self._record(DeployStep("install", PASS, f"installed on nao@{self.host}"))
        return self.verify()

    def verify(self) -> int:
        expected = expected_behaviors(self.config)
        try:
            installed = set(self.installed())
        except Exception as err:
            return self._fail("verify", DeployError(f"cannot read the installed behaviors: {err}"))
        missing = [path for path in expected if path not in installed]
        if missing:
            return self._fail("verify", DeployError(f"not installed: {', '.join(missing)}",
                                                    "add the behavior to the Choregraphe project, or fix the config"))
        self._record(DeployStep("verify", PASS, f"{len(expected)} configured showcase behaviors installed"))
        return 0

    def _undo(self, manifest: Path, original: str, old: str) -> None:
        try:
            write_atomic(manifest, original)
            self.out(f"    version bump undone: back to {old}")
        except OSError as err:
            self.out(f"    could not undo the version bump: {err}")


def run_deploy_cli(args, config: Config) -> int:
    try:
        host = robot_host(getattr(args, "robot", None), config)
    except DeployError as err:
        print(f"✗ robot: {err}", file=sys.stderr)
        return 2
    deploy = Deploy(config, host, lambda line: print(line, flush=True))
    return deploy.run()
