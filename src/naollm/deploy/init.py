"""`naollm init`: check and prepare a robot, with no motor command (SPEC §20.1).

Left out until real motion exists: SSH access, the package deploy and the Stand
and gesture checks. `--name` is left out as well, because it changes a robot
setting that nobody has tested.
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import yaml

from naollm.animations.library import find_profile, load_library
from naollm.animations.limits import HAND_JOINTS, load_limits
from naollm.config import Config
from naollm.robot.adapter import RobotInfo

PASS = "pass"
FAIL = "fail"
SKIP = "skip"
MARK = {PASS: "✓", FAIL: "✗", SKIP: "-"}

LANGUAGE_HELP = (
    "Language packs cannot be installed from here. Sign in at https://cloud.nao6.maxtronics.com/, select the "
    "language for this robot, then update the robot's applications from Robot Settings. "
    "See 'Installing German' in spike/FINDINGS.md."
)

TTS_LANGUAGE = {"de": "German", "en": "English"}

Out = Callable[[str], None]
# Returns the operator's answer, or None when nobody can answer (end of input).
Ask = Callable[[str], Optional[str]]


@dataclass(frozen=True)
class StepResult:
    name: str
    status: str
    detail: str = ""
    hint: str = ""


class StopInit(Exception):
    """A step failed in a way that makes further robot commands unsafe."""


def degrees(radians: float) -> float:
    return radians * 180.0 / 3.141592653589793


def profile_data(info: RobotInfo, tts: List[str], asr: List[str], limits: Dict[str, Any]) -> Dict[str, Any]:
    joints = {}
    for joint, (low, high, velocity) in limits.items():
        # Hands are open fractions, 0..1, in the robot's values and in the animation format.
        convert = (lambda v: float(v)) if joint in HAND_JOINTS else degrees
        joints[joint] = {"min_deg": convert(low), "max_deg": convert(high), "max_velocity_rad_s": float(velocity)}
    return {
        "naoqi_version": info.naoqi_version,
        "robot_id": info.robot_id,
        "languages": {"tts": list(tts), "asr": list(asr)},
        "joint_limits": joints,
    }


def write_atomic(path: Path, text: str) -> None:
    """A failure part-way leaves any older file as it was."""
    path.parent.mkdir(parents=True, exist_ok=True)
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


class Init:
    def __init__(self, config: Config, robot: Any, url: str, out: Out, ask: Ask, dry_run: bool = False,
                 skip_selftest: bool = False, only: Optional[str] = None) -> None:
        self.config = config
        self.robot = robot
        self.url = url
        self.out = out
        self.ask = ask
        self.dry_run = dry_run
        self.skip_selftest = skip_selftest
        self.only = only
        self.results: List[StepResult] = []
        self.info: Optional[RobotInfo] = None
        self.profile_path: Optional[Path] = None

    # -- running ---------------------------------------------------------
    def run(self) -> int:
        try:
            self._step("connect", self.connect)
            if self.results[-1].status == PASS:
                self._run_rest()
        except StopInit:
            pass
        finally:
            self._close()
        return self.summary()

    def _run_rest(self) -> None:
        steps = (("languages", self.languages), ("settings", self.settings), ("profile", self.profile),
                 ("animations", self.animations), ("selftest", self.selftest))
        for name, fn in steps:
            if self.only is not None and name != self.only:
                continue
            if name == "selftest" and self.skip_selftest:
                self._record(StepResult(name, SKIP, "--skip-selftest"))
                continue
            self._step(name, fn)

    def _step(self, name: str, fn: Callable[[], StepResult]) -> None:
        try:
            result = fn()
        except StopInit:
            raise
        except Exception as err:
            result = StepResult(name, FAIL, f"{type(err).__name__}: {err}")
        self._record(result)
        if result.status == FAIL and name in ("connect", "settings"):
            raise StopInit

    def _record(self, result: StepResult) -> None:
        self.results.append(result)
        self.out(f"{MARK[result.status]} {result.name}: {result.detail}")
        if result.hint:
            self.out(f"    fix: {result.hint}")

    def _close(self) -> None:
        if getattr(self.robot, "connected", False):
            try:
                self.robot.disconnect()
            except Exception as err:
                self.out(f"disconnect failed: {err}")

    def summary(self) -> int:
        failed = [r.name for r in self.results if r.status == FAIL]
        passed = len([r for r in self.results if r.status == PASS])
        skipped = [r.name for r in self.results if r.status == SKIP]
        self.out(f"summary: {passed} passed, {len(failed)} failed"
                 + (f" ({', '.join(failed)})" if failed else "")
                 + (f", skipped: {', '.join(skipped)}" if skipped else ""))
        return 1 if failed else 0

    # -- steps -----------------------------------------------------------
    def connect(self) -> StepResult:
        try:
            self.robot.connect(self.url)
            self.info = self.robot.info()
        except Exception as err:
            return StepResult("connect", FAIL, str(err), "check the address and that the robot has booted")
        version = self.info.naoqi_version
        detail = f"robot '{self.info.name}', id {self.info.robot_id}, NAOqi {version}"
        if not self.dry_run and not version.startswith("2.8"):
            return StepResult("connect", FAIL, f"{detail}: need NAOqi 2.8.x", "this bridge supports NAOqi 2.8 only")
        return StepResult("connect", PASS, detail)

    def languages(self) -> StepResult:
        cfg = self.config
        tts_needed = sorted({TTS_LANGUAGE[c] for c in cfg.stt.languages} | {cfg.robot.language})
        asr_needed = [cfg.audio.wake_language]
        tts = self.robot.speech.installed_languages()
        asr = self.robot.audio.wake_languages()
        missing = [f"TTS {x}" for x in tts_needed if x not in tts] + [f"ASR {x}" for x in asr_needed if x not in asr]
        if missing:
            return StepResult("languages", FAIL, f"missing: {', '.join(missing)}", LANGUAGE_HELP)
        return StepResult("languages", PASS, f"TTS {', '.join(tts_needed)}; ASR {', '.join(asr_needed)}")

    def settings(self) -> StepResult:
        awake = self.robot.rest_problem(check_life=False)
        if awake:
            return StepResult("settings", FAIL, awake, "put the robot at rest; init sends no motor command")
        state = self.robot.autonomous_life()
        if state == "disabled":
            return StepResult("settings", PASS, "Autonomous Life already disabled")
        self.out(f"Autonomous Life is {state}. Disabling it was not tested on this robot, "
                 "and the robot may move or lose stiffness.")
        answer = self.ask("Robot in a safe position? Set Autonomous Life to disabled now? [y/N] ")
        if (answer or "").strip().lower() != "y":
            return StepResult("settings", FAIL, f"Autonomous Life stays {state}",
                              "run init again and confirm, or disable it from the robot's web page")
        self.robot.disable_autonomous_life()
        awake = self.robot.rest_problem(check_life=False)
        if awake:
            return StepResult("settings", FAIL, f"after disabling Autonomous Life: {awake}",
                              "put the robot at rest, then run init again")
        return StepResult("settings", PASS, "Autonomous Life set to disabled")

    def profile(self) -> StepResult:
        assert self.info is not None
        data = profile_data(self.info, self.robot.speech.installed_languages(), self.robot.audio.wake_languages(),
                            self.robot.motion.joint_limits())
        path = Path(self.config.robot_profile_dir).expanduser() / f"{self.info.robot_id}.yaml"
        if self.dry_run:
            return StepResult("profile", PASS, f"dry run: would write {path} ({len(data['joint_limits'])} joints)")
        write_atomic(path, yaml.safe_dump(data, sort_keys=False))
        self.profile_path = path
        return StepResult("profile", PASS, f"wrote {path} ({len(data['joint_limits'])} joints)")

    def animations(self) -> StepResult:
        assert self.info is not None
        profile = self.profile_path or (None if self.dry_run
                                        else find_profile(self.config.robot_profile_dir, self.info.robot_id))
        limits = load_limits(profile)
        entries = load_library(self.config.animations_dir, limits)
        bad = [e for e in entries if not e.valid]
        for entry in bad:
            self.out(f"    {entry.name}: {entry.error or ''}")
            for violation in entry.violations:
                self.out(f"      {violation}")
        detail = f"{len(entries) - len(bad)} valid, {len(bad)} invalid, none played (limits: {limits.source})"
        if bad:
            return StepResult("animations", FAIL, detail, "fix the listed animations; see 'naollm anim validate --all'")
        return StepResult("animations", PASS, detail)

    def selftest(self) -> StepResult:
        from naollm.deploy.selftest import SelfTest

        problem = self.robot.rest_problem()
        if problem:
            return StepResult("selftest", FAIL, f"not started: {problem}", "put the robot at rest and run init again")
        checks = SelfTest(self.config, self.robot, self.out, self.ask).run()
        failed = [c.name for c in checks if c.status == FAIL]
        skipped = [c.name for c in checks if c.status == SKIP]
        detail = f"{len(checks) - len(failed) - len(skipped)} passed, {len(failed)} failed, {len(skipped)} skipped"
        if failed:
            return StepResult("selftest", FAIL, f"{detail}: {', '.join(failed)}")
        return StepResult("selftest", PASS, detail)


def _ask(question: str) -> Optional[str]:
    try:
        return input(question)
    except EOFError:
        return None


def robot_url(robot: str) -> str:
    return robot if "://" in robot else f"tcp://{robot}:9559"


def run_init_cli(args: argparse.Namespace, config: Config, robot: Any = None) -> int:
    from naollm.robot.fake_adapter import FakeRobot
    from naollm.robot.qi_adapter import QiRobot, QiUnavailable

    if args.dry_run:
        robot = FakeRobot(printer=print)
    elif robot is None:
        robot = QiRobot(listen_ip=config.robot.listen_ip, log=lambda kind, message: print(f"{kind}: {message}"))
    init = Init(config, robot, robot_url(args.robot), lambda s: print(s, flush=True), _ask, dry_run=args.dry_run,
                skip_selftest=args.skip_selftest, only=args.only)
    try:
        return init.run()
    except QiUnavailable as err:
        print(str(err), file=sys.stderr)
        return 2
