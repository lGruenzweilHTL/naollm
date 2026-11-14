"""`naollm deploy`: build and install the Choregraphe package (SPEC §9, §20.2).

Steps: motors check, manifest check, version bump, `qipkg make-package`,
install, verify against the configured catalogue. The bump is undone when the
build or the install fails, so the manifest only moves on when a package with
that version reached the robot.

The install does not use `qipkg deploy-package`: it connects to a fixed port
9559, which NAOqi 2.8.7 no longer listens on, and exits with 0 when the install
fails. It copies the package with `scp`, installs it through `PackageManager`
on the configured NAOqi URL, and removes the copy over `ssh`.
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
from typing import Any, Callable, List, Optional, Protocol
from urllib.parse import urlparse

from naollm.config import Config, robot_url

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
# Fails at once instead of asking for a password: `naollm init` installs the key.
SSH_OPTIONS = ["-o", "BatchMode=yes"]
ROBOT_HOME = "/home/nao"


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


def has_messages(ts_file: Path) -> bool:
    """True when a Qt `.ts` file holds at least one `<message>` to translate."""
    try:
        root = ET.parse(ts_file).getroot()
    except OSError as err:
        raise DeployError(f"cannot read translation file {ts_file}: {err.strerror}") from None
    except ET.ParseError as err:
        raise DeployError(f"translation file {ts_file} is not valid XML: {err}") from None
    return root.find(".//message") is not None


def stage_project(project_dir: Path, dest: Path) -> Path:
    """Copies the project to `dest` for the build and returns the copy's `.pml`.

    Choregraphe adds `translations/*.ts` files and `<Translation>` entries when it
    saves the project, even with nothing to translate. `qipkg make-package` then
    needs Qt's `lrelease`. The copy leaves out every entry whose file has no
    message, so the build needs `lrelease` only for real translations. The
    project itself stays as Choregraphe wrote it.
    """
    shutil.copytree(project_dir, dest)
    pml = dest / PML
    try:
        tree = ET.parse(pml)
    except ET.ParseError as err:
        raise DeployError(f"{PML} is not valid XML: {err}") from None
    translations = tree.getroot().find("Translations")
    if translations is None:
        return pml
    kept = []
    for entry in translations.findall("Translation"):
        if has_messages(dest / (entry.get("src") or "")):
            kept.append(entry.get("src"))
        else:
            translations.remove(entry)
    tree.write(pml, encoding="UTF-8", xml_declaration=True)
    if kept and shutil.which("lrelease") is None:
        raise DeployError(f"{', '.join(kept)} need Qt's lrelease, which is not installed",
                          "install the Qt linguist tools (Debian/Ubuntu: qt6-l10n-tools or qttools5-dev-tools)")
    return pml


def expected_behaviors(config: Config) -> List[str]:
    """Catalogue entries that the showcase package must provide."""
    targets = list(config.dances.values()) + list(config.gestures.values())
    return sorted({t for t in targets if t.startswith(PACKAGE_ID + "/")})


def naoqi_url(host: str, config: Config) -> str:
    """The configured URL when `host` is the configured robot, so its port is kept."""
    if urlparse(config.robot.url).hostname == host:
        return config.robot.url
    return robot_url(host)


class PackageTarget(Protocol):
    """The robot side of a deploy: install a copied package, list the behaviors."""

    def install(self, remote_path: str) -> bool: ...

    def installed(self) -> List[str]: ...

    def close(self) -> None: ...


class QiPackageTarget:
    """`PackageManager` and `ALBehaviorManager` over one `qi` connection.

    Pass a connected robot (as `naollm init` does), or none to connect on first
    use to `url` with motors off and disconnect in `close`. Install and verify
    share the connection, because `qi.Application` exists once per process.
    """

    def __init__(self, url: str, listen_ip: Optional[str] = None, robot: Any = None) -> None:
        self.url = url
        self.listen_ip = listen_ip
        self._robot = robot
        self._owned = robot is None

    def _connected(self) -> Any:
        if self._robot is None:
            from naollm.robot.qi_adapter import QiRobot

            robot = QiRobot(listen_ip=self.listen_ip)
            robot.connect(self.url)
            self._robot = robot
        return self._robot

    def install(self, remote_path: str) -> bool:
        manager = self._connected().service("PackageManager")
        try:
            manager.removePkg(PACKAGE_ID)
        except Exception:
            pass  # not installed yet; install() reports any real problem
        return manager.install(remote_path) is True

    def installed(self) -> List[str]:
        return list(self._connected().service("ALBehaviorManager").getInstalledBehaviors())

    def close(self) -> None:
        if self._owned and self._robot is not None:
            robot, self._robot = self._robot, None
            robot.disconnect()


class Deploy:
    def __init__(self, config: Config, host: str, out: Out, run: Runner = run_command,
                 qipkg: Optional[str] = None, target: Optional[PackageTarget] = None,
                 project_dir: Path = PROJECT_DIR) -> None:
        self.config = config
        self.host = host
        self.out = out
        self.run_cmd = run
        self.qipkg = qipkg
        self.url = naoqi_url(host, config)
        self.target = target or QiPackageTarget(self.url, config.robot.listen_ip)
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
        try:
            return self._run()
        finally:
            try:
                self.target.close()
            except Exception as err:
                self.out(f"    could not close the robot connection: {err}")

    def _run(self) -> int:
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
            try:
                staged = stage_project(self.project_dir, Path(tmp) / PACKAGE_ID)
            except DeployError as err:
                self._undo(manifest, original, old)
                return self._fail("build", err)
            code = self.run_cmd([qipkg, "make-package", str(staged), "-o", str(pkg)])
            if code != 0 or not pkg.is_file():
                self._undo(manifest, original, old)
                return self._fail("build", DeployError(f"qipkg make-package exited with {code}; nothing was sent",
                                                       "fix the errors above, then run naollm deploy again"))
            self._record(DeployStep("build", PASS, pkg.name))
            try:
                self.install(pkg)
            except DeployError as err:
                self._undo(manifest, original, old)
                return self._fail("install", err)
        self._record(DeployStep("install", PASS, f"{pkg.name} installed through PackageManager at {self.url}"))
        return self.verify()

    def install(self, pkg: Path) -> None:
        """Copy, install, remove the copy. Raises DeployError on any failure."""
        login = f"nao@{self.host}"
        code = self.run_cmd(["scp", *SSH_OPTIONS, str(pkg), f"{login}:"])
        if code != 0:
            raise DeployError(f"scp to {login} exited with {code}; nothing was installed",
                              "check that the robot is on and key login works (naollm init sets up the key)")
        try:
            ok = self.target.install(f"{ROBOT_HOME}/{pkg.name}")
        except Exception as err:
            raise DeployError(f"PackageManager install over {self.url} failed: {err}",
                              "check robot.url and that NAOqi is running") from None
        finally:
            code = self.run_cmd(["ssh", *SSH_OPTIONS, login, "rm", "-f", pkg.name])
            if code != 0:
                self.out(f"    could not remove {ROBOT_HOME}/{pkg.name} from the robot (ssh exited with {code})")
        if not ok:
            raise DeployError(f"PackageManager.install({ROBOT_HOME}/{pkg.name}) returned False",
                              "see the PackageManager log on the robot; the package may be broken")

    def verify(self) -> int:
        expected = expected_behaviors(self.config)
        try:
            installed = set(self.target.installed())
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
