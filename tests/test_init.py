"""`naollm init` and its self-test, against the fake robot and the strict `qi` double."""

import argparse
import os
import threading
from pathlib import Path

import pytest
import yaml

from conftest import make_config
from naollm.animations.limits import load_limits
from naollm.deploy.init import FAIL, PASS, SKIP, Init, profile_data, robot_url, run_init_cli, write_atomic
from naollm.deploy.selftest import SelfTest
from naollm.main import main
from naollm.robot.adapter import RobotInfo
from naollm.robot.fake_adapter import FakeRobot
from naollm.robot.qi_adapter import QiRobot
from qi_double import QiDouble

ROOT = Path(__file__).resolve().parents[1]
URL = "tcp://10.0.0.135:9559"
EN = dict(robot={"url": URL, "language": "English", "battery_reliable": False},
          stt={"languages": ["en"], "fallback_language": "en"},
          audio={"wake_language": "English", "wake_words": ["Hey NAO"], "wake_threshold": 0.4},
          motion={"motors_enabled": False})


def config(tmp_path, **extra):
    return make_config(**{**EN, "robot_profile_dir": str(tmp_path / "robots"),
                          "animations_dir": str(tmp_path / "animations"), **extra})


def make_init(cfg, robot, answers=(), **kwargs):
    lines = []
    answers = list(answers)
    asked = []

    def ask(question):
        asked.append(question)
        return answers.pop(0) if answers else None

    init = Init(cfg, robot, URL, lines.append, ask, **kwargs)
    init.lines, init.asked = lines, asked
    return init


def status(init, name):
    return next(r.status for r in init.results if r.name == name)


@pytest.fixture
def double():
    return QiDouble()


@pytest.fixture
def real(double):
    return QiRobot(qi_module=double)


# -- connect ---------------------------------------------------------------

def test_unsupported_version_stops(tmp_path, double, real):
    double.version = "2.5.1"
    init = make_init(config(tmp_path), real, skip_selftest=True)
    assert init.run() == 1
    assert [r.name for r in init.results] == ["connect"]
    assert status(init, "connect") == FAIL
    assert not (tmp_path / "robots").exists()


def test_unreachable_robot(tmp_path, double, real):
    double.connect_error = "no route"
    init = make_init(config(tmp_path), real)
    assert init.run() == 1
    assert "no route" in "\n".join(init.lines)


def test_url_forms():
    assert robot_url("10.0.0.135") == URL
    assert robot_url("nao.local") == "tcp://nao.local:9559"
    assert robot_url(URL) == URL


# -- languages -------------------------------------------------------------

def test_english_configuration_passes_and_german_is_not_reported(tmp_path, real):
    init = make_init(config(tmp_path), real, only="languages")
    assert init.run() == 0
    assert "German" not in "\n".join(init.lines)


def test_german_configuration_fails_with_instructions(tmp_path, real):
    cfg = config(tmp_path, robot={"url": URL, "language": "German"}, stt={"languages": ["de"], "fallback_language": "de"},
                 audio={"wake_language": "German"})
    init = make_init(cfg, real, only="languages")
    assert init.run() == 1
    text = "\n".join(init.lines)
    assert "TTS German" in text and "ASR German" in text and "maxtronics" in text


# -- settings --------------------------------------------------------------

def test_disabled_life_is_left_alone(tmp_path, double, real):
    init = make_init(config(tmp_path), real, only="settings")
    assert init.run() == 0
    assert init.asked == []
    assert double.called("ALAutonomousLife", "setState") == []


def test_operator_declines(tmp_path, double, real):
    double.life = "solitary"
    init = make_init(config(tmp_path), real, answers=["n"], only="settings")
    assert init.run() == 1
    assert double.life == "solitary"
    assert any("untested" in line or "not tested" in line for line in init.lines)


def test_no_answer_counts_as_declined(tmp_path, double, real):
    double.life = "solitary"
    assert make_init(config(tmp_path), real, only="settings").run() == 1
    assert double.life == "solitary"


def test_operator_confirms(tmp_path, double, real):
    double.life = "solitary"
    double.allow_life_change = True
    init = make_init(config(tmp_path), real, answers=["y"], only="settings")
    assert init.run() == 0
    assert double.called("ALAutonomousLife", "setState") == [("disabled",)]


def test_robot_wakes_after_the_change_stops_init(tmp_path, double, real, monkeypatch):
    double.life = "solitary"
    double.allow_life_change = True

    def wakes(state):
        double.life = state
        double.awake = True

    monkeypatch.setattr(double, "_ALAutonomousLife_setState", wakes)
    init = make_init(config(tmp_path), real, answers=["y"], skip_selftest=True)
    assert init.run() == 1
    assert [r.name for r in init.results] == ["connect", "languages", "settings"]
    assert not (tmp_path / "robots").exists()
    assert double.violations == []


def test_awake_robot_fails_settings(tmp_path, double, real):
    double.stiffness = [0.5]
    init = make_init(config(tmp_path), real, only="settings")
    assert init.run() == 1
    assert double.called("ALAutonomousLife", "setState") == []


# -- profile ---------------------------------------------------------------

def test_profile_round_trip(tmp_path, double, real):
    init = make_init(config(tmp_path), real, only="profile")
    assert init.run() == 0
    path = tmp_path / "robots" / "P0000TEST.yaml"
    data = yaml.safe_load(path.read_text())
    assert data["naoqi_version"] == "2.8.5.11" and data["robot_id"] == "P0000TEST"
    assert "English" in data["languages"]["tts"] and "English" in data["languages"]["asr"]
    limits = load_limits(path)
    low, high, velocity = limits.limits["HeadYaw"]
    assert low == pytest.approx(-57.29578, abs=1e-4) and high == pytest.approx(57.29578, abs=1e-4)
    assert velocity == pytest.approx(8.27)
    assert "robot profile" in limits.source


def test_hand_limits_stay_fractions():
    info = RobotInfo("2.8.5.11", "id", "nao")
    data = profile_data(info, [], [], {"LHand": (0.0, 1.0, 8.33), "HeadYaw": (-1.0, 1.0, 8.0)})
    assert data["joint_limits"]["LHand"]["max_deg"] == 1.0
    assert data["joint_limits"]["HeadYaw"]["max_deg"] == pytest.approx(57.29578, abs=1e-4)


def test_failed_write_keeps_the_old_file(tmp_path, monkeypatch):
    path = tmp_path / "a.yaml"
    path.write_text("old")

    def fail(*args):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", fail)
    with pytest.raises(OSError):
        write_atomic(path, "new")
    assert path.read_text() == "old"
    assert [p.name for p in tmp_path.iterdir()] == ["a.yaml"]


def test_rerun_is_safe(tmp_path, real, double):
    cfg = config(tmp_path)
    assert make_init(cfg, real, skip_selftest=True).run() == 0
    assert make_init(cfg, real, skip_selftest=True).run() == 0
    assert len(double.applications) == 1


# -- animations ------------------------------------------------------------

def test_invalid_animation_is_reported_and_never_played(tmp_path, double, real):
    directory = tmp_path / "animations"
    directory.mkdir()
    (directory / "wild.anim.yaml").write_text((ROOT / "animations" / "victory.anim.yaml").read_text())
    data = yaml.safe_load((directory / "wild.anim.yaml").read_text())
    joint = next(iter(data["tracks"]))
    data["tracks"][joint]["angles_deg"] = [500.0 for _ in data["tracks"][joint]["angles_deg"]]
    (directory / "wild.anim.yaml").write_text(yaml.safe_dump(data))
    init = make_init(config(tmp_path), real, skip_selftest=True)
    assert init.run() == 1
    assert status(init, "animations") == FAIL
    assert any("limit" in line for line in init.lines)
    assert double.violations == []


def test_repository_animations_pass_with_builtin_limits(tmp_path):
    robot = FakeRobot(time_scale=0.0)
    cfg = config(tmp_path, animations_dir=str(ROOT / "animations"))
    init = make_init(cfg, robot, dry_run=True, skip_selftest=True)
    assert init.run() == 0, init.lines


# -- dry run ---------------------------------------------------------------

def test_dry_run_writes_no_file(tmp_path, capsys):
    cfg = config(tmp_path)
    args = argparse.Namespace(dry_run=True, robot="10.0.0.135", skip_selftest=True, only=None)
    assert run_init_cli(args, cfg) == 0
    assert not (tmp_path / "robots").exists()
    assert "would write" in capsys.readouterr().out


def test_command_line(tmp_path, capsys):
    path = tmp_path / "c.yaml"
    path.write_text(f"robot: {{url: '{URL}'}}\nanimations_dir: '{tmp_path}/none'\n"
                    f"robot_profile_dir: '{tmp_path}/robots'\n")
    assert main(["init", "--robot", "10.0.0.135", "--config", str(path), "--dry-run", "--skip-selftest"]) == 0
    out = capsys.readouterr().out
    assert "summary:" in out and "skip" in out


def test_only_runs_connect_and_the_step(tmp_path, real):
    init = make_init(config(tmp_path), real, only="animations")
    assert init.run() == 0
    assert [r.name for r in init.results] == ["connect", "animations"]


def test_skip_selftest_is_skipped_not_passed(tmp_path, real):
    init = make_init(config(tmp_path), real, skip_selftest=True)
    init.run()
    assert status(init, "selftest") == SKIP


# -- self-test -------------------------------------------------------------

class Operator:
    """Answers questions and presses sensors when the console asks for it."""

    def __init__(self, robot, double, missing=(), wake=("Hey NAO", 0.44), answer="y"):
        self.robot, self.double, self.missing, self.wake, self.answer = robot, double, missing, wake, answer
        self.lines = []

    def out(self, line):
        self.lines.append(line)
        for sensor, event in (("front", "FrontTactilTouched"), ("middle", "MiddleTactilTouched"),
                              ("rear", "RearTactilTouched")):
            if f"Touch the {sensor}" in line and sensor not in self.missing:
                self.double.fire(event, 1.0)
                self.double.fire(event, 0.0)
        if line.startswith("Say ") and self.wake:
            self.double.fire("WordRecognized", [f"<...> {self.wake[0]} <...>", self.wake[1]])
        if line.startswith("Speak to the robot"):
            threading.Timer(0.02, lambda: self.double.send_audio(b"\x10\x20" * 2000)).start()

    def ask(self, question):
        return self.answer


def selftest(cfg, real, double, **kwargs):
    real.connect(URL)
    operator = Operator(real, double, **kwargs)
    test = SelfTest(cfg, real, operator.out, operator.ask, touch_s=0.05, mic_s=0.1, wake_s=0.05)
    return {c.name: c for c in test.run()}, operator


def test_all_checks_pass_without_a_forbidden_call(tmp_path, real, double):
    checks, _ = selftest(config(tmp_path), real, double)
    assert checks["touch_rear"].status == PASS
    assert checks["speech_english"].status == PASS
    assert checks["microphone"].status == PASS
    assert checks["wake_word"].status == PASS
    assert double.violations == []


def test_missing_touch_names_the_sensor(tmp_path, real, double):
    checks, _ = selftest(config(tmp_path), real, double, missing=("rear",))
    assert checks["touch_rear"].status == FAIL and "rear" in checks["touch_rear"].detail
    assert checks["touch_front"].status == PASS


def test_weak_wake_word_reports_confidences(tmp_path, real, double):
    cfg = config(tmp_path, audio={"wake_language": "English", "wake_words": ["Hey NAO"], "wake_threshold": 0.6})
    checks, _ = selftest(cfg, real, double, wake=("Hey NAO", 0.44))
    check = checks["wake_word"]
    assert check.status == FAIL and "0.44" in check.detail and "ears test" in check.detail


def test_silent_wake_word_fails(tmp_path, real, double):
    checks, _ = selftest(config(tmp_path), real, double, wake=None)
    assert checks["wake_word"].status == FAIL


def test_unreliable_battery_is_skipped(tmp_path, real, double):
    checks, _ = selftest(config(tmp_path), real, double)
    assert checks["battery"].status == SKIP and "battery_reliable" in checks["battery"].detail


def test_reliable_battery_is_read(tmp_path, real, double):
    cfg = config(tmp_path, robot={"url": URL, "language": "English"})
    checks, _ = selftest(cfg, real, double)
    assert checks["battery"].status == PASS and "100 %" in checks["battery"].detail


def test_no_answer_is_skipped_never_passed(tmp_path, real, double):
    checks, _ = selftest(config(tmp_path), real, double, answer=None)
    assert checks["speech_english"].status == SKIP and checks["eyes"].status == SKIP


def test_operator_says_no(tmp_path, real, double):
    checks, _ = selftest(config(tmp_path), real, double, answer="n")
    assert checks["eyes"].status == FAIL


def test_no_mic_audio_fails(tmp_path, real, double):
    real.connect(URL)
    test = SelfTest(config(tmp_path), real, lambda s: None, lambda q: "y", touch_s=0.01, mic_s=0.01, wake_s=0.01)
    test.microphone()
    assert test.checks[0].status == FAIL and "listen_ip" in test.checks[0].detail


def test_touch_does_not_run_the_emergency_stop(tmp_path, real, double):
    """No EventWatcher is attached: touching rear only records the event."""
    checks, operator = selftest(config(tmp_path), real, double)
    assert checks["touch_rear"].status == PASS
    assert double.called("ALTextToSpeech", "stopAll") == []
    assert not any(args == ("FaceLeds", 0xFF0000, 0.2) for args in double.called("ALLeds", "fadeRGB"))


def test_every_check_states_what_to_do(tmp_path, real, double):
    _, operator = selftest(config(tmp_path), real, double)
    text = "\n".join(operator.lines)
    for phrase in ("Listen:", "Watch the eyes", "Touch the front", "Touch the middle", "Touch the rear",
                   "Speak to the robot", "Say 'Hey NAO'"):
        assert phrase in text


def test_full_init_with_selftest_against_the_double(tmp_path, double, real):
    cfg = config(tmp_path)
    operator = Operator(real, double)
    init = Init(cfg, real, URL, operator.out, operator.ask)
    original_touch = SelfTest.__init__

    def fast(self, config, robot, out, ask):
        original_touch(self, config, robot, out, ask, touch_s=0.05, mic_s=0.1, wake_s=0.05)

    SelfTest.__init__ = fast
    try:
        assert init.run() == 0, operator.lines
    finally:
        SelfTest.__init__ = original_touch
    assert double.violations == []
    assert not double.is_open
    assert status(init, "selftest") == PASS
