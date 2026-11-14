"""Init steps and the animation preview with motors on (robot-init, package-deploy, keyframe-animations)."""


import pytest

from conftest import make_config, write_animation
from naollm.animations.preview import preview, slowed
from naollm.animations.model import load_animation
from naollm.deploy.init import FAIL, PASS, SKIP, Init
from naollm.deploy.selftest import SelfTest
from naollm.deploy.ssh import ensure_key_login
from naollm.main import main
from naollm.robot.fake_adapter import FakeRobot
from naollm.robot.qi_adapter import QiRobot
from qi_double import QiDouble

URL = "tcp://10.0.0.135:9559"
BASE = dict(robot={"url": URL, "language": "English", "battery_reliable": True},
            stt={"languages": ["en"], "fallback_language": "en"},
            audio={"wake_language": "English", "wake_words": ["Hey NAO"], "wake_threshold": 0.4})


def config(tmp_path, motors=True, **extra):
    write_animation(tmp_path / "anims")
    return make_config(**{**BASE, "motion": {"motors_enabled": motors},
                          "robot_profile_dir": str(tmp_path / "robots"),
                          "animations_dir": str(tmp_path / "anims"), **extra})


def make_init(cfg, robot, answers=(), **kwargs):
    lines, answers = [], list(answers)
    init = Init(cfg, robot, URL, lines.append, lambda q: answers.pop(0) if answers else None, **kwargs)
    init.lines = lines
    return init


def names(init):
    return [r.name for r in init.results]


def status(init, name):
    return next(r.status for r in init.results if r.name == name)


# -- step list -----------------------------------------------------------------

def test_motors_on_adds_ssh_and_deploy_in_order(tmp_path):
    deployed, keyed = [], []
    # the double's joint limits are +-1 rad, too narrow for the repository's animations
    init = make_init(config(tmp_path, animations_dir=str(tmp_path)),
                     QiRobot(qi_module=QiDouble(motors=True), motors_enabled=True),
                     skip_selftest=True, ssh=lambda host: keyed.append(host) or (True, "key login works", ""),
                     deploy=lambda host: deployed.append(host) or 0)
    assert init.run() == 0, init.lines
    assert names(init) == ["connect", "ssh", "languages", "settings", "deploy", "profile", "animations", "selftest"]
    assert keyed == ["10.0.0.135"] and deployed == ["10.0.0.135"]


def test_motors_off_skips_ssh_and_deploy(tmp_path):
    init = make_init(config(tmp_path, motors=False), QiRobot(qi_module=QiDouble()), skip_selftest=True,
                     ssh=lambda host: pytest.fail("ssh with motors off"),
                     deploy=lambda host: pytest.fail("deploy with motors off"))
    init.run()
    assert "ssh" not in names(init) and "deploy" not in names(init)


def test_only_deploy_with_motors_off_is_skipped(tmp_path):
    init = make_init(config(tmp_path, motors=False), QiRobot(qi_module=QiDouble()), only="deploy")
    init.run()
    assert status(init, "deploy") == SKIP


def test_failed_deploy_fails_init(tmp_path):
    init = make_init(config(tmp_path), QiRobot(qi_module=QiDouble(motors=True), motors_enabled=True),
                     skip_selftest=True, ssh=lambda host: (True, "", ""), deploy=lambda host: 1)
    assert init.run() == 1
    assert status(init, "deploy") == FAIL


def test_dry_run_skips_ssh_and_deploy(tmp_path):
    init = make_init(config(tmp_path), FakeRobot(), dry_run=True, skip_selftest=True)
    init.run()
    assert status(init, "ssh") == SKIP and status(init, "deploy") == SKIP


# -- ssh -------------------------------------------------------------------------

def test_key_already_installed_runs_no_copy(tmp_path):
    interactive = []
    ok, detail, _ = ensure_key_login("nao.local", print, check=lambda cmd: 0,
                                     interactive=lambda cmd: interactive.append(cmd) or 0, home=tmp_path)
    assert ok and interactive == []


def test_no_public_key_names_ssh_keygen(tmp_path):
    ok, detail, hint = ensure_key_login("nao.local", print, check=lambda cmd: 255,
                                        interactive=lambda cmd: pytest.fail("copied without a key"), home=tmp_path)
    assert not ok and "ssh-keygen" in hint


def test_key_is_copied_with_ssh_copy_id(tmp_path):
    (tmp_path / ".ssh").mkdir()
    (tmp_path / ".ssh" / "id_ed25519.pub").write_text("ssh-ed25519 AAAA test")
    results = [255, 0]
    copied = []
    ok, detail, _ = ensure_key_login("nao.local", lambda line: None, check=lambda cmd: results.pop(0),
                                     interactive=lambda cmd: copied.append(cmd) or 0, home=tmp_path)
    assert ok
    assert copied == [["ssh-copy-id", "-i", str(tmp_path / ".ssh" / "id_ed25519.pub"), "nao@nao.local"]]


# -- name ------------------------------------------------------------------------

def test_name_is_set_only_when_given(tmp_path):
    double = QiDouble()
    double.allow_name_change = True
    init = make_init(config(tmp_path, motors=False), QiRobot(qi_module=double), only="settings")
    init.run()
    assert double.robot_name_set is None

    init = make_init(config(tmp_path, motors=False), QiRobot(qi_module=double), only="settings", name="Robbie")
    assert init.run() == 0
    assert double.robot_name_set == "Robbie"
    assert any("Robbie" in line for line in init.lines if line.startswith("robot name"))
    assert double.violations == []


def test_name_option_on_the_command_line(tmp_path, capsys):
    path = tmp_path / "c.yaml"
    path.write_text(f"robot: {{url: '{URL}'}}\nrobot_profile_dir: '{tmp_path}/r'\n")
    assert main(["init", "--robot", "fake", "--dry-run", "--skip-selftest", "--name", "Robbie",
                 "--config", str(path)]) == 0
    assert "would set the name Robbie" in capsys.readouterr().out


# -- self-test with motors ----------------------------------------------------------

def run_selftest(cfg, robot, answer):
    lines = []
    test = SelfTest(cfg, robot, lines.append, lambda q: answer, touch_s=0.01, mic_s=0.01, wake_s=0.01)
    test.motion()
    return {c.name: c for c in test.checks}


def test_area_not_confirmed_sends_no_motor_command(tmp_path):
    double = QiDouble(motors=True)
    robot = QiRobot(qi_module=double, motors_enabled=True)
    robot.connect(URL)
    checks = run_selftest(config(tmp_path), robot, "n")
    assert {c.status for c in checks.values()} == {SKIP}
    assert double.motor_calls() == []


def test_gesture_check_stands_plays_and_crouches(tmp_path):
    double = QiDouble(motors=True)
    robot = QiRobot(qi_module=double, motors_enabled=True)
    robot.connect(URL)
    checks = run_selftest(config(tmp_path), robot, "y")
    assert [checks[n].status for n in ("stand", "gesture", "crouch")] == [PASS, PASS, PASS]
    postures = [a[0] for a in double.called("ALRobotPosture", "goToPosture")]
    assert postures == ["Stand", "Crouch"]
    assert double.called("ALAnimationPlayer", "run") == [("animations/Stand/Gestures/Hey_1",)]
    assert double.called("ALMotion", "rest")
    assert double.violations == []


def test_failed_stand_rests_and_fails(tmp_path):
    double = QiDouble(motors=True)
    double.posture_results = [False]
    robot = QiRobot(qi_module=double, motors_enabled=True)
    robot.connect(URL)
    checks = run_selftest(config(tmp_path), robot, "y")
    assert checks["stand"].status == FAIL and checks["gesture"].status == SKIP
    assert double.called("ALMotion", "rest")


def test_unreliable_battery_skips_motion(tmp_path):
    double = QiDouble(motors=True)
    robot = QiRobot(qi_module=double, motors_enabled=True)
    robot.connect(URL)
    cfg = config(tmp_path, robot={**BASE["robot"], "battery_reliable": False})
    checks = run_selftest(cfg, robot, "y")
    assert {c.status for c in checks.values()} == {SKIP}
    assert double.motor_calls() == []


def test_motors_off_selftest_has_no_motion_check(tmp_path):
    checks = run_selftest(config(tmp_path, motors=False), FakeRobot(), "y")
    assert checks == {}


# -- anim preview ------------------------------------------------------------------

def run_preview(cfg, answer="y", speed=0.5, robot=None):
    robot = robot or FakeRobot(time_scale=0)
    lines = []
    code = preview(cfg, "cheer", speed, robot, lines.append, lambda q: answer)
    return code, robot, lines


def test_slow_preview_doubles_every_time(tmp_path):
    code, robot, lines = run_preview(config(tmp_path))
    assert code == 0, lines
    played = [a for a in robot.actions if a.name == "play_keyframes"]
    assert played and played[0].args["duration_s"] == 6.0  # the 3 s test animation at half speed
    assert any("not reviewed yet" in line for line in lines)  # accepted without reviewed_by
    assert any("TOOL" in line and "play_gesture ok" in line for line in lines)  # through the gate
    assert robot.action_names()[-1] == "disconnect"


def test_slowed_scales_times(tmp_path):
    anim = load_animation(write_animation(tmp_path))
    slow = slowed(anim, 0.5)
    assert slow.tracks["HeadPitch"].times == [t * 2 for t in anim.tracks["HeadPitch"].times]
    assert slow.duration_s == anim.duration_s * 2


@pytest.mark.parametrize("speed", [1.5, 0, -0.5])
def test_factor_out_of_range(tmp_path, speed):
    code, robot, _ = run_preview(config(tmp_path), speed=speed)
    assert code != 0 and robot.actions == []


def test_factor_out_of_range_on_the_command_line(capsys):
    assert main(["anim", "preview", "cheer", "--speed", "1.5", "--dry-run"]) != 0
    assert "at most 1" in capsys.readouterr().err


def test_operator_declines_preview(tmp_path):
    code, robot, _ = run_preview(config(tmp_path), answer="n")
    assert code != 0 and robot.actions == []


def test_preview_refused_with_motors_off(tmp_path):
    code, robot, lines = run_preview(config(tmp_path, motors=False))
    assert code != 0 and robot.actions == []
    assert "motors are off" in lines[0]


def test_preview_refuses_an_invalid_animation(tmp_path):
    (tmp_path / "bad.anim.yaml").write_text(
        "name: bad\nduration_s: 1.0\ntracks:\n  HeadYaw: {times: [0.5], angles_deg: [400]}\n")
    robot = FakeRobot(time_scale=0)
    lines = []
    code = preview(config(tmp_path, animations_dir=str(tmp_path)), "bad", 0.5, robot, lines.append, lambda q: "y")
    assert code != 0 and robot.actions == []
    assert "validation" in lines[-1]
