"""`naollm preflight` and `naollm cleanup` (event-readiness spec)."""

import datetime
import json

import pytest

from conftest import make_config, write_animation
from naollm.admin.consent import ConsentRecord, ConsentRegistry
from naollm.deploy.cleanup import Cleanup
from naollm.deploy.preflight import FAIL, PASS, SKIP, WARN, Preflight, profiles_in_use
from naollm.eval import case_file_hash, result_path
from naollm.llm.residency import ModelResidencyError, ModelStatus
from naollm.main import main
from naollm.robot.fake_adapter import FakeRobot

NOW = datetime.datetime(2026, 11, 20, 12, 0, tzinfo=datetime.timezone.utc)


class Residency:
    def __init__(self, loaded=True, fail=None):
        self.loaded, self.fail = loaded, fail
        self.calls = []

    def status(self):
        if self.fail:
            raise ModelResidencyError(self.fail)
        return ModelStatus(loaded=self.loaded, expires_at=None)

    def load(self):
        self.calls.append("load")

    def unload(self):
        self.calls.append("unload")


def event_config(tmp_path, **extra):
    data = dict(event_mode=True, robot={"url": "tcp://10.0.0.2:9559", "language": "English"},
                stt={"languages": ["en"], "fallback_language": "en"},
                audio={"wake_language": "English", "wake_words": ["Hey NAO"]},
                motion={"motors_enabled": False}, privacy={"face_recognition_enabled": False},
                animations_dir=str(tmp_path / "anims"), robot_profile_dir=str(tmp_path / "robots"),
                gestures={"wave": "animations/Stand/Gestures/Hey_1"}, dances={})
    for key, value in extra.items():
        data[key] = {**data[key], **value} if isinstance(value, dict) and isinstance(data.get(key), dict) else value
    return make_config(**data)


def cases_file(tmp_path):
    path = tmp_path / "cases.yaml"
    if not path.exists():
        path.write_text("- {profile: event_default, prompt: x, expect: {no_tool: true}}\n")
    return path


def write_result(tmp_path, model="gemma4:e4b", days_old=1, sha=None, scores=None):
    path = result_path(model, tmp_path / "results")
    path.parent.mkdir(parents=True, exist_ok=True)
    scores = scores or {"event_default": 0.95, "walking_on": 0.92, "age_game_on": 0.9}
    path.write_text(json.dumps({
        "date": (NOW - datetime.timedelta(days=days_old)).isoformat(),
        "model": model,
        "case_file_sha256": sha or case_file_hash(cases_file(tmp_path)),
        "profiles": {p: {"score": s} for p, s in scores.items()},
    }))


def fake_robot():
    robot = FakeRobot(installed_behaviors=["animations/Stand/Gestures/Hey_1"])
    robot.languages = ["English"]
    robot.asr_languages = ["English"]
    return robot


def preflight(tmp_path, config=None, robot=None, residency=None, records=(), unreachable=False):
    lines = []
    registry = ConsentRegistry(tmp_path / "consent.json")
    for record in records:
        registry.add(record)

    def factory():
        if unreachable:
            raise ConnectionError("no route to host")
        return robot or fake_robot()

    p = Preflight(config or event_config(tmp_path), lines.append, robot_factory=factory,
                  residency_factory=lambda: residency or Residency(), results_dir=tmp_path / "results",
                  cases_path=cases_file(tmp_path), registry=registry, now=NOW)
    code = p.run()
    p.lines = lines
    return p, code


def status(p, name):
    return next(c for c in p.checks if c.name == name)


# -- the whole run --------------------------------------------------------------

def test_all_checks_pass(tmp_path):
    write_result(tmp_path)
    p, code = preflight(tmp_path)
    assert code == 0, p.lines
    assert p.lines[-1].startswith("ready for the event")
    assert {c.status for c in p.checks} <= {PASS, SKIP}


def test_debug_logging_fails_and_names_the_key(tmp_path):
    write_result(tmp_path)
    p, code = preflight(tmp_path, event_config(tmp_path, event_mode=False, privacy={"debug_logging": True}))
    assert code != 0
    check = status(p, "privacy")
    assert check.status == FAIL and "privacy.debug_logging" in check.hint


def test_cloud_endpoint_fails_and_mentions_signage(tmp_path):
    write_result(tmp_path)
    p, code = preflight(tmp_path, event_config(tmp_path, llm={"base_url": "https://api.example.com/v1"}))
    assert code != 0
    assert status(p, "endpoint").status == FAIL and "signage" in status(p, "endpoint").detail


def test_age_game_is_a_warning_not_a_failure(tmp_path):
    write_result(tmp_path)
    p, code = preflight(tmp_path, event_config(tmp_path, privacy={"age_game_enabled": True}))
    assert code == 0
    assert status(p, "age_game").status == WARN and "signage" in status(p, "age_game").detail


def test_preflight_commands_no_motor_and_changes_nothing(tmp_path):
    write_result(tmp_path)
    robot = fake_robot()
    preflight(tmp_path, robot=robot)
    assert set(robot.action_names()) <= {"connect", "learned_names", "disconnect"}


# -- model and eval ---------------------------------------------------------------

def test_model_that_cannot_load(tmp_path):
    write_result(tmp_path)
    p, code = preflight(tmp_path, residency=Residency(fail="connection refused"))
    assert code != 0 and status(p, "model").status == FAIL


def test_unloaded_model_is_loaded_and_released(tmp_path):
    write_result(tmp_path)
    residency = Residency(loaded=False)
    p, code = preflight(tmp_path, residency=residency)
    assert code == 0 and residency.calls == ["load", "unload"]


def test_stale_eval_prints_the_command(tmp_path):
    write_result(tmp_path, days_old=20)
    p, code = preflight(tmp_path)
    check = status(p, "eval")
    assert code != 0 and check.status == FAIL and "20 days old" in check.detail
    assert "naollm eval --config" in check.hint


def test_changed_cases_fail_the_eval_check(tmp_path):
    write_result(tmp_path, sha="0" * 64)
    p, _ = preflight(tmp_path)
    assert "cases changed" in status(p, "eval").detail


def test_missing_eval(tmp_path):
    p, code = preflight(tmp_path)
    assert code != 0 and "no eval result" in status(p, "eval").detail


def test_low_score_in_a_used_profile(tmp_path):
    write_result(tmp_path, scores={"event_default": 0.85})
    p, _ = preflight(tmp_path)
    assert "event_default 85%" in status(p, "eval").detail


def test_profiles_in_use():
    assert profiles_in_use(make_config(motion={"motors_enabled": False})) == ["event_default"]
    assert profiles_in_use(make_config()) == ["event_default", "walking_on"]
    assert profiles_in_use(make_config(privacy={"age_game_enabled": True})) == ["event_default", "age_game_on"]


# -- robot ------------------------------------------------------------------------------

def test_robot_off_fails_the_robot_checks_and_names_the_url(tmp_path):
    write_result(tmp_path)
    p, code = preflight(tmp_path, unreachable=True)
    assert code != 0
    for name in ("robot", "languages", "behaviors"):
        check = status(p, name)
        assert check.status == FAIL and "tcp://10.0.0.2:9559" in check.detail


def test_missing_language(tmp_path):
    write_result(tmp_path)
    robot = fake_robot()
    p, code = preflight(tmp_path, event_config(tmp_path, robot={"language": "German"},
                                                stt={"languages": ["de", "en"]}), robot=robot)
    assert code != 0 and "TTS German" in status(p, "languages").detail


def test_unreviewed_animation_warns(tmp_path):
    write_result(tmp_path)
    write_animation(tmp_path / "anims")
    p, code = preflight(tmp_path, event_config(tmp_path, gestures={"cheer": "anim:cheer"}))
    check = status(p, "animations")
    assert check.status == WARN and "cheer" in check.detail and "dropped" in check.detail
    assert code == 0


def test_motors_on_needs_a_reliable_battery(tmp_path):
    write_result(tmp_path)
    p, code = preflight(tmp_path, event_config(tmp_path, motion={"motors_enabled": True},
                                                robot={"battery_reliable": False}))
    assert code != 0 and status(p, "battery").status == FAIL


def test_missing_behavior_warns_with_motors_on(tmp_path):
    write_result(tmp_path)
    p, _ = preflight(tmp_path, event_config(tmp_path, motion={"motors_enabled": True},
                                             dances={"robot": "showcase/dance_robot"}))
    assert status(p, "behaviors").status == WARN and "showcase/dance_robot" in status(p, "behaviors").detail


# -- consent --------------------------------------------------------------------------------

def record(name, delete_after="2027-01-01"):
    return ConsentRecord(name, "F-1", "2026-10-01", delete_after)


def test_overdue_record_fails_with_the_count(tmp_path):
    write_result(tmp_path)
    robot = fake_robot()
    robot.faces = ["Anna"]
    p, code = preflight(tmp_path, robot=robot, records=[record("Anna", "2026-11-01")])
    check = status(p, "consent")
    assert code != 0 and check.status == FAIL and "1 record" in check.detail and "faces delete" in check.hint


def test_mismatch_shows_counts_not_names_in_event_mode(tmp_path):
    write_result(tmp_path)
    robot = fake_robot()
    robot.faces = ["Ben"]
    p, code = preflight(tmp_path, robot=robot, records=[record("Anna")])
    check = status(p, "faces_match")
    assert code != 0 and check.status == FAIL
    assert "1 face(s) with no record" in check.detail
    assert "Anna" not in "\n".join(p.lines) and "Ben" not in "\n".join(p.lines)


def test_mismatch_names_outside_event_mode(tmp_path):
    write_result(tmp_path)
    robot = fake_robot()
    robot.faces = ["Ben"]
    p, _ = preflight(tmp_path, event_config(tmp_path, event_mode=False), robot=robot, records=[record("Anna")])
    assert "Ben" in status(p, "faces_match").detail


def test_command_line_runs_and_fails_without_a_robot(tmp_path, capsys, monkeypatch):
    config = tmp_path / "c.yaml"
    config.write_text("robot: {url: 'tcp://127.0.0.1:9'}\nevent_mode: true\n")
    monkeypatch.setattr("naollm.deploy.preflight.Preflight._qi_robot",
                        lambda self: (_ for _ in ()).throw(ConnectionError("refused")))
    monkeypatch.setattr("naollm.deploy.preflight.Preflight.model", lambda self: __import__(
        "naollm.deploy.preflight", fromlist=["Check"]).Check("model", PASS, "stub"))
    code = main(["preflight", "--config", str(config), "--results-dir", str(tmp_path / "r")])
    assert code == 1
    assert "NOT ready" in capsys.readouterr().out


# -- cleanup ----------------------------------------------------------------------------------

def run_cleanup(tmp_path, answers, robot=None, unreachable=False, records=None):
    records = [record("Anna")] if records is None else records
    lines = []
    answers = list(answers)
    registry = ConsentRegistry(tmp_path / "home" / "consent.json")
    for r in records:
        registry.add(r)
    robot = robot or fake_robot()
    if not unreachable:
        robot.faces = [r.name for r in records]

    def factory():
        if unreachable:
            raise ConnectionError("no route to host")
        return robot

    temp = tmp_path / "temp"
    temp.mkdir(exist_ok=True)
    c = Cleanup(event_config(tmp_path), lines.append, lambda q: answers.pop(0) if answers else None,
                robot_factory=factory, registry=registry, naollm_dir=tmp_path / "home", temp_dir=temp, now=NOW)
    code = c.run()
    return code, lines, robot, registry, c


def test_operator_types_delete(tmp_path):
    code, lines, robot, registry, c = run_cleanup(tmp_path, ["DELETE"])
    assert code == 0, lines
    assert robot.faces == [] and registry.load() == []
    log = c.log_path.read_text().splitlines()
    assert log == ["2026-11-20T12:00:00+00:00 faces_forgotten=1 records_removed=1"]
    assert "Anna" not in c.log_path.read_text()
    assert any("consent forms" in line for line in lines)


def test_operator_aborts(tmp_path):
    code, lines, robot, registry, c = run_cleanup(tmp_path, ["delete"])
    assert code != 0
    assert robot.faces == ["Anna"] and len(registry.load()) == 1
    assert not c.log_path.exists() and "nothing was deleted" in lines[-1]


def test_leftover_audio_file_is_reported_and_asked(tmp_path):
    (tmp_path / "temp").mkdir()
    leftover = tmp_path / "temp" / "naollm-turn.wav"
    leftover.write_bytes(b"RIFF")
    code, lines, *_ = run_cleanup(tmp_path, ["DELETE", "n"])
    assert code != 0 and leftover.exists()
    assert any(line.startswith("✗ files") for line in lines)
    code, lines, *_ = run_cleanup(tmp_path, ["DELETE", "y"])
    assert not leftover.exists()


def test_unreachable_robot_keeps_the_records(tmp_path):
    code, lines, robot, registry, c = run_cleanup(tmp_path, ["DELETE"], unreachable=True)
    assert code != 0
    assert len(registry.load()) == 1
    assert any("forgotten first" in line for line in lines)
    assert not c.log_path.exists()


def test_cleanup_command_aborts_without_delete(tmp_path, monkeypatch, capsys):
    config = tmp_path / "c.yaml"
    config.write_text("robot: {url: 'tcp://127.0.0.1:9'}\n")
    monkeypatch.setattr("builtins.input", lambda q: "no")
    assert main(["cleanup", "--config", str(config)]) == 1
    assert "nothing was deleted" in capsys.readouterr().out


@pytest.mark.parametrize("name", ["voice.wav", "turn-transcript.json"])
def test_leftover_scan_under_naollm(tmp_path, name):
    from naollm.deploy.cleanup import leftover_files

    home = tmp_path / "home"
    (home / "x").mkdir(parents=True)
    (home / "x" / name).write_text("x")
    (home / "consent.json").write_text("[]")
    assert [p.name for p in leftover_files(home, tmp_path / "none")] == [name]
