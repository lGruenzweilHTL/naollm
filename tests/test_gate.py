import threading
import time


from conftest import make_config
from naollm.robot.adapter import EventKind
from naollm.state import State, StateMachine


# -- state machine ---------------------------------------------------------

def test_fall_from_thinking(bridge, robot):
    bridge.state.transition(State.THINKING)
    robot.inject(EventKind.FALL)
    assert bridge.state.state == State.FALLEN
    assert "stop_all_behaviors" in robot.action_names()
    assert "stop_move" in robot.action_names()
    assert any(e.kind == "alert" and "FALLEN" in e.message for e in bridge.log.entries())


def test_resume_from_stopped(bridge):
    bridge.estop("command")
    assert bridge.state.state == State.STOPPED
    assert bridge.resume()
    assert bridge.state.state == State.IDLE


def test_resume_refused_while_fallen(bridge, robot):
    robot.inject(EventKind.FALL)
    assert not bridge.resume()
    robot.fallen = False
    robot.posture = "Stand"
    assert bridge.resume()


def test_stopped_and_fallen_reachable_from_every_state():
    for start in State:
        for target in (State.STOPPED, State.FALLEN):
            sm = StateMachine(start)
            sm.force(target)
            assert sm.state == target
            assert not sm.transition(State.THINKING)


# -- ordered checks --------------------------------------------------------

def test_unknown_tool(bridge, robot):
    r = bridge.gate.check_and_run("fly", {})
    assert not r.ok and r.reason == "unknown_tool"
    assert robot.actions == []


def test_out_of_range_step_is_rejected_not_clamped(make_bridge, robot):
    b = make_bridge(make_config(motion={"walking_enabled": True}))
    r = b.gate.check_and_run("step", {"direction": "forward", "cm": 50})
    assert not r.ok and "30" in r.error
    assert "move_to" not in robot.action_names()


def test_walking_disabled(bridge, robot):
    r = bridge.gate.check_and_run("turn", {"degrees": 45})
    assert not r.ok and r.reason == "walking_off"
    assert "move_to" not in robot.action_names()


def test_bad_json_string(bridge):
    r = bridge.gate.check_and_run("dance", '{"name": ')
    assert not r.ok and r.reason == "bad_json" and r.malformed


def test_bad_enum(bridge):
    r = bridge.gate.check_and_run("dance", {"name": "tango"})
    assert not r.ok and r.reason == "bad_choice"
    assert r.details["choices"] == ["macarena", "robot"]


def test_handler_failure_releases_lock(bridge, robot):
    robot.fail_on["play_animation"] = RuntimeError("motor error")
    r = bridge.gate.check_and_run("play_gesture", {"name": "wave"})
    assert not r.ok and r.reason == "robot_error"
    assert set(r.as_dict()) == {"ok", "error"}
    del robot.fail_on["play_animation"]
    assert bridge.gate.check_and_run("play_gesture", {"name": "wave"}).ok
    assert bridge.state.state == State.IDLE


def test_result_format(bridge):
    assert bridge.gate.check_and_run("play_gesture", {"name": "wave"}).as_dict() == {"ok": True, "result": "finished"}


def run_in_thread(fn):
    out = {}
    t = threading.Thread(target=lambda: out.setdefault("r", fn()))
    t.start()
    return t, out


def wait_for(cond, timeout=2.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.005)
    return False


def test_concurrent_motion_rejected(bridge, robot):
    robot.time_scale = 1.0
    t, out = run_in_thread(lambda: bridge.gate.check_and_run("dance", {"name": "macarena"}))
    assert wait_for(lambda: bridge.state.state == State.ACTING)
    r = bridge.gate.check_and_run("look", {"direction": "left"}, source="admin")
    assert not r.ok and r.error == "Ich bewege mich gerade schon."
    robot.motion.stop_all_behaviors()
    t.join(2)
    assert out["r"].as_dict() == {"ok": True, "result": "interrupted"}


def test_gesture_refused_while_stopped(bridge, robot):
    bridge.estop("command")
    robot.clear_actions()
    r = bridge.gate.check_and_run("play_gesture", {"name": "wave"})
    assert not r.ok and r.reason == "stopped"
    assert "play_animation" not in robot.action_names()


def test_status_allowed_while_stopped(bridge):
    bridge.estop("command")
    assert bridge.gate.check_and_run("get_status", {}).ok
    assert bridge.gate.check_and_run("stop", {}).ok


# -- battery, thermal, displacement ------------------------------------------

def test_dance_at_25_percent(bridge, robot):
    robot.battery_pct = 25
    r = bridge.gate.check_and_run("dance", {"name": "macarena"})
    assert not r.ok and "Akku" in r.error


def test_sit_at_15_percent(bridge, robot):
    robot.battery_pct = 15
    assert bridge.gate.check_and_run("set_posture", {"posture": "Sit"}).ok


def test_gesture_at_15_percent_refused(bridge, robot):
    robot.battery_pct = 15
    r = bridge.gate.check_and_run("play_gesture", {"name": "wave"})
    assert not r.ok and r.reason == "battery_low"


def test_warm_blocks_dance_not_gesture(bridge, robot):
    robot.thermal = "warm"
    assert bridge.gate.check_and_run("dance", {"name": "macarena"}).reason == "too_warm"
    assert bridge.gate.check_and_run("play_gesture", {"name": "wave"}).ok


def test_hot_blocks_all_but_crouch(bridge, robot):
    robot.thermal = "hot"
    assert bridge.gate.check_and_run("play_gesture", {"name": "wave"}).reason == "too_hot"
    assert bridge.gate.check_and_run("set_posture", {"posture": "Crouch"}).ok


def test_dance_needs_stand(bridge, robot):
    robot.posture = "Sit"
    r = bridge.gate.check_and_run("dance", {"name": "macarena"})
    assert not r.ok and r.error == "Dafür muss ich erst aufstehen."


def test_displacement_budget(make_bridge):
    b = make_bridge(make_config(motion={"walking_enabled": True}))
    for _ in range(3):
        assert b.gate.check_and_run("step", {"direction": "forward", "cm": 30}).ok
    assert b.displacement_cm == 90
    r = b.gate.check_and_run("step", {"direction": "forward", "cm": 20})
    assert not r.ok and r.reason == "distance_budget"
    assert b.gate.check_and_run("step", {"direction": "back", "cm": 10}).ok


def test_english_reasons(bridge, robot):
    robot.battery_pct = 25
    r = bridge.gate.check_and_run("dance", {"name": "macarena"}, lang="en")
    assert "battery" in r.error


# -- emergency stop and event watcher --------------------------------------

def test_estop_step_order(bridge, robot):
    bridge.estop("command")
    names = robot.action_names()
    order = ["stop_speech", "stop_all_behaviors", "stop_move", "set_balancer", "go_to_posture", "set_eyes"]
    # The state listener turns the eyes red at once; the routine sets them again last.
    positions = [len(names) - 1 - names[::-1].index(n) if n == "set_eyes" else names.index(n) for n in order]
    assert positions == sorted(positions)
    assert robot.actions[-1].args == {"rgb": "#FF0000"}
    assert bridge.state.state == State.STOPPED
    assert any(e.kind == "estop" and "source=command" in e.message for e in bridge.log.entries())


def test_failing_estop_step_does_not_skip_rest(bridge, robot):
    robot.fail_on["stop_all_behaviors"] = RuntimeError("boom")
    bridge.estop("hotkey")
    names = robot.action_names()
    assert "go_to_posture" in names and names[-1] == "set_eyes"
    assert robot.posture == "Crouch"
    assert bridge.state.state == State.STOPPED
    assert any(e.kind == "error" and "stop_behaviors" in e.message for e in bridge.log.entries())


def test_estop_cancels_turn(bridge):
    cancelled = []
    bridge.register_turn(lambda: cancelled.append(True))
    bridge.estop("command")
    assert cancelled == [True]


def test_rear_head_touch_during_dance(bridge, robot):
    robot.time_scale = 1.0
    t, out = run_in_thread(lambda: bridge.gate.check_and_run("dance", {"name": "macarena"}))
    assert wait_for(lambda: bridge.state.state == State.ACTING)
    toucher = threading.Thread(target=robot.inject, args=(EventKind.REAR_HEAD,))
    toucher.start()
    toucher.join(5)
    t.join(5)
    assert out["r"].result == "interrupted"
    assert robot.posture == "Crouch"
    assert robot.eyes == "#FF0000"
    assert bridge.state.state == State.STOPPED


def test_front_head_requests_listen(bridge, robot):
    heard = []
    bridge.listen_handler = heard.append
    robot.inject(EventKind.FRONT_HEAD)
    assert heard == ["touch"]


def test_front_head_ignored_while_acting(bridge, robot):
    heard = []
    bridge.listen_handler = heard.append
    bridge.state.transition(State.ACTING)
    robot.inject(EventKind.FRONT_HEAD)
    assert heard == []


def test_battery_critical_sits_and_rests(bridge, robot):
    robot.inject(EventKind.BATTERY, 8)
    names = robot.action_names()
    assert names.index("go_to_posture") < names.index("say") < names.index("rest")
    assert bridge.state.state == State.RESTING


def test_hot_joints_crouch_and_rest(bridge, robot):
    robot.inject(EventKind.THERMAL, "hot")
    assert "rest" in robot.action_names()
    assert bridge.state.state == State.RESTING


def test_connection_lost(bridge, robot):
    robot.inject(EventKind.CONNECTION_LOST)
    assert bridge.state.state == State.OFFLINE
    assert not bridge.gate.check_and_run("play_gesture", {"name": "wave"}).ok


def test_idle_crouch_and_stand_again(bridge, robot):
    bridge.idle_tick(now=bridge.last_activity + 181)
    assert robot.posture == "Crouch" and bridge.idle_crouched
    bridge.before_turn()
    assert robot.posture == "Stand" and not bridge.idle_crouched
