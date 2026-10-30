import threading
import time

from naollm.robot.adapter import (
    Audio,
    EventKind,
    Events,
    Motion,
    Perception,
    RobotAdapter,
    Speech,
)
from naollm.robot.fake_adapter import FakeRobot


def test_fake_satisfies_every_protocol(robot):
    assert isinstance(robot, RobotAdapter)
    assert isinstance(robot.motion, Motion)
    assert isinstance(robot.speech, Speech)
    assert isinstance(robot.audio, Audio)
    assert isinstance(robot.events, Events)
    assert isinstance(robot.perception, Perception)


def test_posture_change(robot):
    assert robot.motion.go_to_posture("Sit")
    assert robot.events.status().posture == "Sit"


def test_simulated_low_battery(robot):
    robot.battery_pct = 15
    assert robot.events.status().battery_pct == 15


def test_records_actions_in_order(robot):
    robot.speech.say("Hallo")
    robot.motion.play_animation("animations/Stand/Gestures/Hey_1")
    assert [str(a) for a in robot.actions] == [
        "say(text='Hallo', animated=True)",
        "play_animation(path='animations/Stand/Gestures/Hey_1')",
    ]


def test_injected_event_reaches_handler(robot):
    seen = []
    robot.events.subscribe(seen.append)
    robot.inject(EventKind.MIDDLE_HEAD)
    assert [e.kind for e in seen] == [EventKind.MIDDLE_HEAD]


def test_stop_during_dance_interrupts_quickly():
    robot = FakeRobot(installed_behaviors=["showcase/dance_macarena"], time_scale=10 / 6)  # 10 s dance
    result = {}

    def dance():
        result["outcome"] = robot.motion.run_behavior("showcase/dance_macarena")
        result["ended"] = time.monotonic()

    t = threading.Thread(target=dance)
    t.start()
    time.sleep(1.0)
    stop_at = time.monotonic()
    robot.motion.stop_all_behaviors()
    t.join(timeout=2)
    assert result["outcome"] == "interrupted"
    assert result["ended"] - stop_at < 0.5


def test_long_action_finishes(robot):
    assert robot.motion.run_behavior("showcase/dance_macarena") == "finished"


def test_dry_run_prints_actions():
    lines = []
    robot = FakeRobot(time_scale=0, printer=lines.append)
    robot.speech.say("Hallo")
    assert lines and "say" in lines[0] and "Hallo" in lines[0]
