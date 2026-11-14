"""Real motion over `qi`, with motors on, against the strict double (qi-motion spec).

The double allows the motion mapping of SPEC §6.2 on top of the motors-off
allowlist, and fails on every call that would switch off a NAOqi protection.
"""

import asyncio
import threading
import time
from pathlib import Path

import pytest
import yaml

import naollm.robot.qi_adapter as qa
from conftest import animation_text, make_config
from llm_stub import ScriptedClient, call
from naollm.admin.commands import Commands
from naollm.animations.model import Animation
from naollm.bridge import Bridge, StartupRefused
from naollm.llm.loop import ConversationLoop
from naollm.main import run_app
from naollm.robot.adapter import Keyframes, MotionFailed, MotorsDisabled
from naollm.robot.qi_adapter import QiRobot
from naollm.state import State
from qi_double import DENIED, Future, QiDouble

ROOT = Path(__file__).resolve().parents[1]
URL = "tcp://10.0.0.135:9559"
CHEER = Animation.model_validate(yaml.safe_load(animation_text()))
CONFIG = dict(
    robot={"url": URL, "language": "English", "battery_reliable": True},
    stt={"languages": ["en"], "fallback_language": "en"},
    audio={"wake_language": "English", "wake_words": ["Hey NAO"], "wake_threshold": 0.4},
    motion={"motors_enabled": True},
    dances={"macarena": "showcase/dance_macarena", "robot": "showcase/dance_robot"},
    gestures={"wave": "animations/Stand/Gestures/Hey_1", "cheer": "anim:cheer"},
)


def motors_double():
    double = QiDouble(motors=True)
    double.behaviors = ["animations/Stand/Gestures/Hey_1", "showcase/dance_macarena"]
    return double


def make_bridge(double, **overrides):
    robot = QiRobot(qi_module=double, motors_enabled=True)
    config = make_config(**{**CONFIG, **overrides})
    bridge = Bridge(config, robot, keyframe_loader=lambda: ({"cheer": CHEER}, {}))
    bridge.look_hold_s = 0.05
    return bridge


@pytest.fixture
def double():
    return motors_double()


@pytest.fixture
def bridge(double):
    b = make_bridge(double)
    b.startup(greet=False)
    double.posture = double.posture_family = "Stand"
    double.calls.clear()
    yield b
    b.shutdown()
    assert double.violations == []


def in_thread(fn, *args):
    box = {}
    thread = threading.Thread(target=lambda: box.setdefault("result", fn(*args)), daemon=True)
    thread.start()
    return thread, box


def until(predicate, timeout=3.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return
        time.sleep(0.005)
    raise AssertionError("condition not reached")


# -- choice at connect -------------------------------------------------------

def test_motion_class_is_chosen_at_connect():
    off = QiRobot(qi_module=QiDouble())
    off.connect(URL)
    assert isinstance(off.motion, qa.QiMotionDisabled) and not off.motors_live
    on = QiRobot(qi_module=motors_double(), motors_enabled=True)
    assert isinstance(on.motion, qa.QiMotionDisabled)  # nothing before the first connect
    on.connect(URL)
    assert isinstance(on.motion, qa.QiMotion) and on.motors_live
    motion = on.motion
    on.disconnect()
    on.motors_enabled = False
    on.connect(URL)
    assert on.motion is motion  # a reconnect keeps the choice


def test_motors_off_adapter_still_refuses_with_the_motion_allowlist():
    double = motors_double()
    robot = QiRobot(qi_module=double)  # motors_enabled defaults to False
    robot.connect(URL)
    with pytest.raises(MotorsDisabled):
        robot.motion.go_to_posture("Stand")
    assert double.motor_calls() == []


# -- startup -------------------------------------------------------------------

def test_normal_start_wakes_stands_and_tracks(double):
    b = make_bridge(double)
    b.startup(greet=True)
    assert b.state.state == State.IDLE
    order = [m for _, m, _ in double.motor_calls()]
    assert order[:4] == ["wakeUp", "goToPosture", "setTrackingMode", "setEnabled"]
    assert double.called("ALRobotPosture", "goToPosture")[0] == ("Stand", qa.POSTURE_SPEED)
    assert double.called("ALBasicAwareness", "setEnabled")[0] == (True,)
    assert double.called("ALAnimatedSpeech", "say") == [("I'm ready.",)]
    assert double.called("ALLeds", "fadeRGB")[-1] == ("FaceLeds", 0xFFFFFF, 0.2)
    b.shutdown()
    assert double.violations == []


def test_stand_fails_rests_and_names_the_step(double):
    double.posture_results = [False]
    b = make_bridge(double)
    with pytest.raises(StartupRefused, match="stand"):
        b.startup(greet=False)
    assert double.called("ALMotion", "rest")
    assert not double.is_open
    assert any("startup step stand failed" in e.message for e in b.log.entries("alert"))


def test_low_battery_refuses_before_any_motor_command(double):
    double.battery = 15
    b = make_bridge(double)
    with pytest.raises(StartupRefused, match="battery_motion_min_pct"):
        b.startup(greet=False)
    assert double.motor_calls() == []
    assert not double.is_open


def test_autonomous_life_refuses_with_motors_on(double):
    double.life = "solitary"
    b = make_bridge(double)
    with pytest.raises(StartupRefused, match="naollm init"):
        b.startup(greet=False)
    assert double.motor_calls() == []
    assert double.called("ALAutonomousLife", "setState") == []


def test_missing_behavior_is_dropped_from_the_enum(bridge):
    assert any("showcase/dance_robot" in e.message for e in bridge.log.entries("warning"))
    dance = bridge.registry.get("dance")
    assert dance.params[0].choices == ("macarena",)


def test_main_low_battery_exits_non_zero(tmp_path, capsys):
    from test_real_lifecycle import write_config

    from naollm.config import load_config

    double = motors_double()
    double.battery = 15
    config = load_config(write_config(tmp_path, "true", battery="true"))
    code = asyncio.run(run_app(config, "admin", False, robot=QiRobot(qi_module=double, motors_enabled=True)))
    assert code == 2
    assert "battery 15 %" in capsys.readouterr().err
    assert double.motor_calls() == []


# -- mapping -------------------------------------------------------------------

def test_posture_change_uses_the_fixed_speed(bridge, double):
    result = bridge.gate.check_and_run("set_posture", {"posture": "Sit"})
    assert result.ok
    assert double.called("ALRobotPosture", "goToPosture") == [("Sit", qa.POSTURE_SPEED)]


def test_posture_speed_from_a_caller_is_ignored(bridge, double):
    bridge.robot.motion.go_to_posture("Crouch", speed=1.0)
    assert double.called("ALRobotPosture", "goToPosture") == [("Crouch", qa.POSTURE_SPEED)]


def test_head_look_uses_the_fixed_table(bridge, double):
    assert bridge.gate.check_and_run("look", {"direction": "left"}).ok
    joints, angles, speed = double.called("ALMotion", "setAngles")[0]
    assert joints == ["HeadYaw", "HeadPitch"] and speed == qa.HEAD_SPEED
    assert angles == pytest.approx([0.6981, 0.0], abs=1e-3)
    until(lambda: double.called("ALBasicAwareness", "setEnabled")[-1:] == [(True,)])


def test_gesture_dance_and_steps(bridge, double):
    assert bridge.gate.check_and_run("play_gesture", {"name": "wave"}).ok
    assert double.called("ALAnimationPlayer", "run") == [("animations/Stand/Gestures/Hey_1",)]
    assert bridge.gate.check_and_run("dance", {"name": "macarena"}).ok
    assert double.called("ALBehaviorManager", "runBehavior") == [("showcase/dance_macarena",)]
    bridge.set_walking(True)
    assert bridge.gate.check_and_run("step", {"direction": "forward", "cm": 20}).ok
    assert double.called("ALMotion", "moveTo") == [(0.2, 0.0, 0.0)]


def test_keyframes_use_the_balancer(bridge, double):
    assert bridge.gate.check_and_run("play_gesture", {"name": "cheer"}).ok
    names = [m for s, m, _ in double.motor_calls() if s == "ALMotion"]
    assert names == ["wbEnable", "wbFootState", "wbEnableBalanceConstraint", "angleInterpolationBezier", "wbEnable"]
    assert double.called("ALMotion", "wbEnable") == [(True,), (False,)]
    joints, times, angles = double.called("ALMotion", "angleInterpolationBezier")[0]
    assert joints == list(CHEER.tracks)


def test_linear_keyframes_use_angle_interpolation(double):
    robot = QiRobot(qi_module=double, motors_enabled=True)
    robot.connect(URL)
    frames = Keyframes(names=["HeadYaw"], times=[[1.0]], angles=[[0.1]], bezier=False, duration_s=1.0)
    assert robot.motion.play_keyframes(frames) == "finished"
    assert double.called("ALMotion", "angleInterpolation") == [(["HeadYaw"], [[0.1]], [[1.0]], True)]


# -- behaviors ---------------------------------------------------------------

def test_behavior_not_installed_starts_nothing(bridge, double):
    double.behaviors.remove("showcase/dance_macarena")
    result = bridge.gate.check_and_run("dance", {"name": "macarena"})
    assert not result.ok and result.reason == "not_installed"
    assert result.details["detail"] == "showcase/dance_macarena"
    assert double.called("ALBehaviorManager", "runBehavior") == []


def test_behavior_that_runs_too_long_is_stopped(bridge, double, monkeypatch):
    monkeypatch.setattr(qa, "BEHAVIOR_LIMIT_S", 0.1)
    double.hold[("ALBehaviorManager", "runBehavior")] = Future(finished=False)
    result = bridge.gate.check_and_run("dance", {"name": "macarena"})
    assert not result.ok and result.reason == "timeout"
    assert double.called("ALBehaviorManager", "stopBehavior") == [("showcase/dance_macarena",)]
    assert bridge.state.state == State.IDLE


def test_behavior_error_is_a_robot_error(bridge, double):
    double.hold[("ALBehaviorManager", "runBehavior")] = Future(error="box crashed")
    result = bridge.gate.check_and_run("dance", {"name": "macarena"})
    assert not result.ok and result.reason == "robot_error"


# -- emergency stop, shutdown, fall ---------------------------------------------

def test_estop_during_a_dance(bridge, double):
    double.hold[("ALBehaviorManager", "runBehavior")] = Future(finished=False)
    thread, box = in_thread(bridge.gate.check_and_run, "dance", {"name": "macarena"})
    until(lambda: double.called("ALBehaviorManager", "runBehavior"))
    double.fire("RearTactilTouched", 1.0)
    thread.join(2)
    assert box["result"].result == "interrupted"
    assert bridge.state.state == State.STOPPED
    assert double.called("ALBehaviorManager", "stopAllBehaviors")
    assert double.called("ALMotion", "stopMove")
    assert double.called("ALTextToSpeech", "stopAll")
    assert double.called("ALRobotPosture", "goToPosture")[-1] == ("Crouch", qa.POSTURE_SPEED)
    assert double.called("ALLeds", "fadeRGB")[-1] == ("FaceLeds", 0xFF0000, 0.2)
    assert bridge.log.entries("error") == []


def test_estop_during_keyframes(bridge, double):
    double.hold[("ALMotion", "angleInterpolationBezier")] = Future(finished=False)
    thread, box = in_thread(bridge.gate.check_and_run, "play_gesture", {"name": "cheer"})
    until(lambda: double.called("ALMotion", "angleInterpolationBezier"))
    bridge.estop("hotkey")
    thread.join(2)
    assert box["result"].result == "interrupted"
    assert double.called("ALMotion", "killTasksUsingResources") == [(list(CHEER.tracks),)]
    assert (False,) in double.called("ALMotion", "wbEnable")
    assert double.called("ALRobotPosture", "goToPosture")[-1][0] == "Crouch"
    assert bridge.state.state == State.STOPPED


def test_estop_step_failure_does_not_skip_the_crouch(bridge, double):
    double.fail[("ALBehaviorManager", "stopAllBehaviors")] = RuntimeError("busy")
    bridge.estop("command")
    assert double.called("ALRobotPosture", "goToPosture")[-1][0] == "Crouch"
    assert bridge.state.state == State.STOPPED


def test_shutdown_runs_every_step_when_crouch_fails(double):
    b = make_bridge(double)
    b.startup(greet=False)
    double.fail[("ALRobotPosture", "goToPosture")] = RuntimeError("fallen over")
    b.shutdown()
    assert double.called("ALMotion", "rest")
    assert not double.is_open
    assert any("shutdown step crouch failed" in e.message for e in b.log.entries("error"))
    assert double.violations == []


def test_fall_sends_no_posture(bridge, double):
    double.fire("robotHasFallen", 1.0)
    assert bridge.state.state == State.FALLEN
    assert double.called("ALBehaviorManager", "stopAllBehaviors") and double.called("ALMotion", "stopMove")
    assert double.called("ALRobotPosture", "goToPosture") == []
    assert any("FALLEN" in e.message for e in bridge.log.entries("alert"))


# -- speech and tracking -------------------------------------------------------

def test_say_is_animated_with_motors_on(bridge, double):
    bridge.robot.speech.say("Hello", animated=True)
    bridge.robot.speech.say("Plain", animated=False)
    assert double.called("ALAnimatedSpeech", "say") == [("Hello",)]
    assert double.called("ALTextToSpeech", "say") == [("Plain",)]


def test_stop_speech_stops_both(bridge, double):
    double.hold[("ALAnimatedSpeech", "say")] = held = Future(finished=False)
    thread, _ = in_thread(bridge.robot.speech.say, "A long sentence")
    until(lambda: double.called("ALAnimatedSpeech", "say"))
    bridge.robot.speech.stop_speech()
    thread.join(2)
    assert not thread.is_alive()
    assert double.called("ALTextToSpeech", "stopAll") and held.isFinished()


def test_motion_tool_follows_speech(double):
    b = make_bridge(double)
    b.startup(greet=False)
    double.posture = double.posture_family = "Stand"
    client = ScriptedClient([["Watch this!", call("dance", {"name": "macarena"})], ["Done."]])
    loop = ConversationLoop(b, client)
    double.calls.clear()
    asyncio.run(loop.run_turn("Please dance", "en"))
    order = [(s, m) for s, m, _ in double.calls if (s, m) in (("ALAnimatedSpeech", "say"),
                                                               ("ALBehaviorManager", "runBehavior"))]
    assert order[0] == ("ALAnimatedSpeech", "say") and ("ALBehaviorManager", "runBehavior") in order
    assert order.index(("ALAnimatedSpeech", "say")) < order.index(("ALBehaviorManager", "runBehavior"))
    b.shutdown()


def test_tracking_stops_for_a_dance_and_resumes(bridge, double):
    assert bridge.gate.check_and_run("dance", {"name": "macarena"}).ok
    seq = [(m, a) for s, m, a in double.calls
           if (s, m) in (("ALBasicAwareness", "setEnabled"), ("ALBehaviorManager", "runBehavior"))]
    assert seq == [("setEnabled", (False,)), ("runBehavior", ("showcase/dance_macarena",)),
                   ("setEnabled", (True,))]


def test_tracking_stays_off_after_an_estop_until_resume(bridge, double):
    bridge.estop("command")
    assert double.called("ALBasicAwareness", "setEnabled")[-1] == (False,)
    double.calls.clear()
    bridge.resume_tracking()
    assert double.called("ALBasicAwareness", "setEnabled") == []
    double.posture = double.posture_family = "Crouch"
    assert bridge.resume()
    assert double.called("ALBasicAwareness", "setEnabled")[-1] == (True,)


def test_look_timer_does_not_restart_tracking_after_an_estop(bridge, double):
    assert bridge.gate.check_and_run("look", {"direction": "up"}).ok
    bridge.estop("command")
    time.sleep(0.15)
    assert double.called("ALBasicAwareness", "setEnabled")[-1] == (False,)


# -- the full lifecycle with motors on ---------------------------------------------

def test_full_lifecycle_with_motors_on_keeps_the_protections():
    double = motors_double()
    b = make_bridge(double)
    client = ScriptedClient([[call("dance", {"name": "macarena"})], ["Done."]])
    conversation = ConversationLoop(b, client)
    lines = []

    async def confirm(question):
        return "y"

    commands = Commands(b, "run", lines.append, confirm, conversation)

    async def go():
        b.startup(greet=True)
        await commands.execute("chat dance please")
        for line in ("posture Sit", "posture Stand", "walk on", "tool turn 10", "tool step forward 10",
                     "tool look left", "tool play_gesture wave", "tool play_gesture cheer", "tool stop",
                     "tool set_eyes cyan", "tool get_status", "status", "behaviors", "rest", "wake"):
            await commands.execute(line)
        double.fire("MiddleTactilTouched", 1.0)
        assert b.state.state == State.STOPPED
        await commands.execute("resume")
        await commands.execute("quit")
        b.shutdown()

    asyncio.run(go())
    assert double.violations == []
    assert not any(line.startswith("error") for line in lines), lines
    assert not any("refused, motors are off" in line for line in lines)
    assert double.called("ALMotion", "rest") and not double.is_open
    for service, methods in DENIED.items():
        for method in methods:
            assert double.called(service, method) == []


def test_denied_calls_fail_in_both_modes():
    for motors in (False, True):
        double = QiDouble(motors=motors)
        double.is_open = True
        with pytest.raises(AssertionError):
            double.session.service("ALMotion").setFallManagerEnabled(False)
        assert double.violations


def test_adapter_has_no_protection_code():
    source = (ROOT / "src" / "naollm" / "robot" / "qi_adapter.py").read_text()
    for methods in DENIED.values():
        for method in methods:
            assert method not in source


def test_motion_failed_carries_its_reason():
    err = MotionFailed("timeout", "x ran too long")
    assert err.reason == "timeout" and "x ran too long" in str(err)


def test_status_line_has_no_motors_off_marker(bridge):
    lines = []
    commands = Commands(bridge, "admin", lines.append, None)
    assert "motors OFF" not in commands.status_line()
    asyncio.run(commands.execute("tools"))
    assert not any("motors are off" in line for line in lines)
    assert any(line.strip().startswith("dance") and "[LLM]" in line for line in lines)


def test_critical_battery_sits_and_rests(bridge, double):
    double.fire("BatteryChargeChanged", 5)
    assert double.called("ALRobotPosture", "goToPosture")[-1][0] == "Sit"
    assert double.called("ALMotion", "rest")
    assert bridge.state.state == State.RESTING


def test_hot_joints_crouch_and_rest(bridge, double):
    double.fire("HeadProcessorIsCriticallyHot", 1)
    assert double.called("ALRobotPosture", "goToPosture")[-1][0] == "Crouch"
    assert double.called("ALMotion", "rest")
    assert bridge.state.state == State.RESTING


def test_idle_crouch_and_stand_on_the_next_turn(bridge, double):
    bridge.idle_tick(now=bridge.last_activity + bridge.config.motion.idle_crouch_s + 1)
    assert double.called("ALRobotPosture", "goToPosture")[-1][0] == "Crouch"
    bridge.before_turn()
    assert double.called("ALRobotPosture", "goToPosture")[-1][0] == "Stand"
