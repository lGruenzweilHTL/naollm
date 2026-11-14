"""`naollm preflight`: is this setup ready for the event? (event-readiness spec)

Read-only. It connects to the robot with motors off whatever the config says, so
no check can command a motor, and it changes no setting on the robot or in the
configuration. The model check loads the model when it is not loaded yet, and
releases it again afterwards.
"""

from __future__ import annotations

import datetime
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, List, Optional

from naollm.config import Config, is_local_endpoint, startup_problems

PASS = "pass"
FAIL = "fail"
WARN = "warn"
SKIP = "skip"
MARK = {PASS: "✓", FAIL: "✗", WARN: "!", SKIP: "-"}

EVAL_MAX_AGE_DAYS = 14
EVAL_MIN_SCORE = 0.9
DEFAULT_CASES = Path("tests/eval/tool_calls.yaml")
TTS_LANGUAGE = {"de": "German", "en": "English"}

Out = Callable[[str], None]


@dataclass(frozen=True)
class Check:
    name: str
    status: str
    detail: str = ""
    hint: str = ""


def profiles_in_use(config: Config) -> List[str]:
    """The eval profiles that describe what this configuration can offer the LLM."""
    used = ["event_default"]
    if config.privacy.age_game_enabled:
        used.append("age_game_on")
    elif config.motion.motors_enabled:
        used.append("walking_on")  # the operator can switch walking on
    return used


class Preflight:
    def __init__(self, config: Config, out: Out, config_path: str = "config.yaml",
                 robot_factory: Optional[Callable[[], Any]] = None,
                 residency_factory: Optional[Callable[[], Any]] = None,
                 results_dir: Optional[Path] = None, cases_path: Path = DEFAULT_CASES,
                 registry: Any = None, now: Optional[datetime.datetime] = None) -> None:
        self.config = config
        self.out = out
        self.config_path = config_path
        self.robot_factory = robot_factory
        self.residency_factory = residency_factory
        self.results_dir = results_dir
        self.cases_path = Path(cases_path)
        self.registry = registry
        self.now = now or datetime.datetime.now(datetime.timezone.utc)
        self.checks: List[Check] = []
        self.robot: Any = None

    # -- output ----------------------------------------------------------------
    def _add(self, check: Check) -> None:
        self.checks.append(check)
        self.out(f"{MARK[check.status]} {check.name}: {check.detail}")
        if check.hint:
            self.out(f"    fix: {check.hint}")

    def _guarded(self, name: str, fn: Callable[[], Check]) -> None:
        try:
            check = fn()
        except Exception as err:
            check = Check(name, FAIL, f"{type(err).__name__}: {err}")
        self._add(check)

    def run(self) -> int:
        self.configuration()
        self._guarded("model", self.model)
        self._guarded("eval", self.eval)
        self.robot_checks()
        self._guarded("animations", self.animations)  # with the robot's profile, when it answered
        self.consent()
        failed = [c.name for c in self.checks if c.status == FAIL]
        warned = [c.name for c in self.checks if c.status == WARN]
        if failed:
            self.out(f"NOT ready: {len(failed)} failed ({', '.join(failed)})"
                     + (f", {len(warned)} warnings" if warned else ""))
            return 1
        self.out("ready for the event" + (f", with {len(warned)} warnings to check ({', '.join(warned)})"
                                          if warned else ""))
        return 0

    # -- configuration ------------------------------------------------------------
    def configuration(self) -> None:
        cfg = self.config
        if cfg.event_mode:
            self._add(Check("event_mode", PASS, "event_mode is true"))
        else:
            self._add(Check("event_mode", FAIL, "event_mode is false: the log may show spoken and visitor text",
                            f"set event_mode: true in {self.config_path}"))
        problems = []
        if cfg.privacy.persist_transcripts:
            problems.append("privacy.persist_transcripts")
        if cfg.privacy.debug_logging:
            problems.append("privacy.debug_logging")
        problems += [p for p in startup_problems(cfg) if not any(k in p for k in problems)]
        if problems:
            self._add(Check("privacy", FAIL, f"{', '.join(problems)} must be false at the event",
                            f"set {' and '.join(f'{p}: false' for p in problems)} in {self.config_path}"))
        else:
            self._add(Check("privacy", PASS, "no transcripts kept, no debug logging"))
        if is_local_endpoint(cfg.llm.base_url):
            self._add(Check("endpoint", PASS, f"{cfg.llm.base_url} is on this machine or the local network"))
        else:
            self._add(Check("endpoint", FAIL, f"{cfg.llm.base_url} is not local: conversation data would leave "
                                              "the local network, and the signage would have to change",
                            "use the local Ollama endpoint, e.g. http://localhost:11434/v1"))
        if cfg.privacy.age_game_enabled:
            self._add(Check("age_game", WARN, "the age game is on: the signage must announce it, and the school "
                                              "must have cleared it (README, 'Age game')"))
        if cfg.privacy.face_recognition_enabled:
            self._add(Check("faces", WARN, "face recognition is on: signed consent forms must exist for every "
                                           "enrolled face"))

    # -- model and eval -------------------------------------------------------------
    def model(self) -> Check:
        from naollm.llm.residency import ModelResidency, ModelResidencyError

        residency = self.residency_factory() if self.residency_factory else ModelResidency(self.config.llm)
        name = self.config.llm.model
        try:
            if residency.status().loaded:
                return Check("model", PASS, f"{name} is loaded")
            residency.load()
            residency.unload()
        except ModelResidencyError as err:
            return Check("model", FAIL, f"{name} cannot be loaded: {err}",
                         f"start Ollama and run 'ollama pull {name}'")
        return Check("model", PASS, f"{name} loads (released again)")

    def eval(self) -> Check:
        from naollm import eval as ev

        model = self.config.llm.model
        command = f"naollm eval --config {self.config_path}"
        path = ev.result_path(model, self.results_dir or ev.RESULTS_DIR)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return Check("eval", FAIL, f"no eval result for {model} ({path})", f"run '{command}'")
        except (OSError, ValueError) as err:
            return Check("eval", FAIL, f"cannot read {path}: {err}", f"run '{command}'")
        try:
            date = datetime.datetime.fromisoformat(data["date"])
            if date.tzinfo is None:
                date = date.replace(tzinfo=datetime.timezone.utc)
            age = (self.now - date).days
            if age > EVAL_MAX_AGE_DAYS:
                return Check("eval", FAIL, f"the last eval of {model} is {age} days old "
                                           f"(at most {EVAL_MAX_AGE_DAYS})", f"run '{command}'")
            if data["case_file_sha256"] != ev.case_file_hash(self.cases_path):
                return Check("eval", FAIL, f"the cases changed since the last eval of {model}", f"run '{command}'")
            scores = {p: float(v["score"]) for p, v in data["profiles"].items()}
        except (KeyError, TypeError, ValueError) as err:
            return Check("eval", FAIL, f"{path} is not a result file ({err})", f"run '{command}'")
        except OSError as err:
            return Check("eval", FAIL, f"cannot read the case file {self.cases_path}: {err}")
        missing = [p for p in profiles_in_use(self.config) if p not in scores]
        low = [f"{p} {scores[p]:.0%}" for p in profiles_in_use(self.config) if p in scores
               and scores[p] < EVAL_MIN_SCORE]
        if missing or low:
            parts = ([f"not run: {', '.join(missing)}"] if missing else []) + \
                    ([f"below {EVAL_MIN_SCORE:.0%}: {', '.join(low)}"] if low else [])
            return Check("eval", FAIL, "; ".join(parts), f"improve the prompt or model, then run '{command}'")
        shown = ", ".join(f"{p} {scores[p]:.0%}" for p in profiles_in_use(self.config))
        return Check("eval", PASS, f"{model}, {age} days old, {shown}")

    def animations(self) -> Check:
        from naollm.animations.library import find_profile, load_library
        from naollm.animations.limits import load_limits

        profile = None
        if self.robot is not None:
            profile = find_profile(self.config.robot_profile_dir, self.robot.info().robot_id)
        entries = load_library(self.config.animations_dir, load_limits(profile))
        used = {t.split(":", 1)[1] for t in list(self.config.gestures.values()) + list(self.config.dances.values())
                if t.startswith("anim:")}
        invalid = [e.name for e in entries if not e.valid and e.name in used]
        unreviewed = [e.name for e in entries if e.valid and not e.reviewed and e.name in used]
        if invalid or unreviewed:
            parts = ([f"invalid: {', '.join(invalid)}"] if invalid else []) + \
                    ([f"no reviewed_by: {', '.join(unreviewed)}"] if unreviewed else [])
            return Check("animations", WARN, "; ".join(parts) + " (dropped from the tool enums at the event)",
                         "preview with 'naollm anim preview <name>', then set reviewed_by")
        return Check("animations", PASS, f"{len(used)} configured, all valid and reviewed")

    # -- robot ------------------------------------------------------------------------
    def robot_checks(self) -> None:
        cfg = self.config
        if cfg.motion.motors_enabled and not cfg.robot.battery_reliable:
            self._add(Check("battery", FAIL, "motors are on but robot.battery_reliable is false",
                            "fit and check a working battery, or set motion.motors_enabled: false"))
        elif cfg.motion.motors_enabled:
            self._add(Check("battery", PASS, "motors on with a reliable battery reading"))
        names = ("robot", "languages", "behaviors")
        try:
            robot = self.robot_factory() if self.robot_factory else self._qi_robot()
            robot.connect(cfg.robot.url)
            info = robot.info()
        except Exception as err:
            for name in names:
                self._add(Check(name, FAIL, f"robot not reachable at {cfg.robot.url}: {err}",
                                "switch the robot on, check the router and robot.url"))
            return
        self.robot = robot
        if info.naoqi_version.startswith("2.8"):
            self._add(Check("robot", PASS, f"{info.name}, NAOqi {info.naoqi_version}"))
        else:
            self._add(Check("robot", FAIL, f"NAOqi {info.naoqi_version}; this bridge supports 2.8.x only"))
        self._guarded("languages", self.languages)
        self._guarded("behaviors", self.behaviors)

    def _qi_robot(self) -> Any:
        from naollm.robot.qi_adapter import QiRobot

        return QiRobot(listen_ip=self.config.robot.listen_ip)  # motors off: reads only

    def languages(self) -> Check:
        cfg = self.config
        tts_needed = sorted({TTS_LANGUAGE[c] for c in cfg.stt.languages} | {cfg.robot.language})
        tts = self.robot.speech.installed_languages()
        missing = [f"TTS {x}" for x in tts_needed if x not in tts]
        asr = self.robot.audio.wake_languages()
        missing += [f"ASR {x}" for x in [cfg.audio.wake_language] if x not in asr]
        if missing:
            return Check("languages", FAIL, f"missing: {', '.join(missing)}",
                         "install the language pack (README, 'Languages'), or switch to English only")
        return Check("languages", PASS, f"TTS {', '.join(tts_needed)}; ASR {cfg.audio.wake_language}")

    def behaviors(self) -> Check:
        cfg = self.config
        if not cfg.motion.motors_enabled:
            return Check("behaviors", SKIP, "motors are off: no behavior is used")
        installed = set(self.robot.motion.installed_behaviors())
        wanted = [t for t in list(cfg.dances.values()) + list(cfg.gestures.values()) if not t.startswith("anim:")]
        missing = sorted(t for t in wanted if t not in installed)
        if missing:
            return Check("behaviors", WARN, f"not installed, dropped from the tool enums: {', '.join(missing)}",
                         "run 'naollm deploy', or fix the config")
        return Check("behaviors", PASS, f"all {len(wanted)} configured behaviors installed")

    # -- consent ------------------------------------------------------------------------
    def consent(self) -> None:
        from naollm.admin.consent import DEFAULT_PATH, ConsentRegistry, RegistryDamaged

        registry = self.registry or ConsentRegistry(DEFAULT_PATH)
        try:
            records = registry.load()
        except RegistryDamaged as err:
            self._add(Check("consent", FAIL, str(err), "fix or restore the file; it is never overwritten"))
            return
        overdue = [r for r in records if r.overdue(self.now.date())]
        if overdue:
            self._add(Check("consent", FAIL, f"{len(overdue)} record(s) past their deletion date",
                            "run 'naollm admin', then 'faces delete <name>' for each, or accept at startup"))
        else:
            self._add(Check("consent", PASS, f"{len(records)} record(s), none overdue"))
        if self.robot is None:
            self._add(Check("faces_match", FAIL, "robot not reachable: learned faces not compared",
                            "switch the robot on and run preflight again"))
            return
        try:
            learned = set(self.robot.perception.learned_names())
        except Exception as err:
            self._add(Check("faces_match", FAIL, f"cannot read the learned faces: {err}"))
            return
        names = {r.name for r in records}
        no_record = sorted(learned - names)
        no_face = sorted(names - learned)
        if not no_record and not no_face:
            self._add(Check("faces_match", PASS, f"{len(learned)} learned face(s), each with a record"))
            return
        if self.config.event_mode:  # counts only, no names
            detail = f"{len(no_record)} face(s) with no record, {len(no_face)} record(s) with no face"
        else:
            detail = f"faces with no record: {no_record or '-'}; records with no face: {no_face or '-'}"
        self._add(Check("faces_match", FAIL, detail, "run 'faces list' in 'naollm admin' and delete to resolve"))

    def close(self) -> None:
        if self.robot is not None:
            try:
                self.robot.disconnect()
            except Exception as err:
                self.out(f"disconnect failed: {err}")


def run_preflight_cli(args, config: Config) -> int:
    preflight = Preflight(config, lambda line: print(line, flush=True), config_path=args.config,
                          results_dir=Path(args.results_dir) if args.results_dir else None,
                          cases_path=Path(args.cases))
    try:
        return preflight.run()
    except KeyboardInterrupt:
        print("preflight interrupted", file=sys.stderr)
        return 130
    finally:
        preflight.close()

