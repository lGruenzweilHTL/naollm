"""Dance cooldown in the gate (safety-gate spec, harden-for-event)."""

import asyncio

from conftest import make_config
from naollm.admin.commands import Commands


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def with_clock(bridge):
    clock = Clock()
    bridge.clock = clock
    return clock


def test_second_dance_too_soon(bridge):
    clock = with_clock(bridge)
    assert bridge.gate.check_and_run("dance", {"name": "macarena"}).ok
    clock.now += 30
    result = bridge.gate.check_and_run("dance", {"name": "robot"}, lang="en")
    assert not result.ok and result.reason == "dance_cooldown"
    assert result.details["seconds"] == 90
    assert "90 seconds" in result.error


def test_operator_is_held_to_the_same_rule(bridge):
    clock = with_clock(bridge)
    assert bridge.gate.check_and_run("dance", {"name": "macarena"}, source="admin").ok
    clock.now += 10
    lines = []
    asyncio.run(Commands(bridge, "admin", lines.append, None).execute("tool dance macarena"))
    assert "refused" in lines[0] and "110" in lines[0]


def test_gesture_during_cooldown(bridge, robot):
    clock = with_clock(bridge)
    assert bridge.gate.check_and_run("dance", {"name": "macarena"}).ok
    clock.now += 30
    assert bridge.gate.check_and_run("play_gesture", {"name": "wave"}).ok


def test_cooldown_ends(bridge):
    clock = with_clock(bridge)
    assert bridge.gate.check_and_run("dance", {"name": "macarena"}).ok
    clock.now += 120
    assert bridge.gate.check_and_run("dance", {"name": "macarena"}).ok


def test_interrupted_dance_counts(bridge, robot):
    clock = with_clock(bridge)
    robot.fail_on["run_behavior"] = RuntimeError("box crashed")
    assert not bridge.gate.check_and_run("dance", {"name": "macarena"}).ok
    robot.fail_on.clear()
    assert bridge.gate.check_and_run("dance", {"name": "macarena"}).reason == "dance_cooldown"
    assert clock.now == bridge.last_dance_end


def test_rule_off(make_bridge):
    bridge = make_bridge(make_config(motion={"dance_cooldown_s": 0}))
    assert bridge.gate.check_and_run("dance", {"name": "macarena"}).ok
    assert bridge.gate.check_and_run("dance", {"name": "macarena"}).ok


def test_default_is_120_s():
    assert make_config().motion.dance_cooldown_s == 120
