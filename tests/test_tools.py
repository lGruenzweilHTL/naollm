import threading
import time

from conftest import make_config
from naollm.animations.model import Animation
from naollm.state import State
from naollm.tools.schemas import MAX_TOOLS


def names(schemas):
    return [s["function"]["name"] for s in schemas]


def test_schema_shape(bridge):
    schemas = bridge.registry.schemas(walking=True)
    assert len(schemas) <= MAX_TOOLS
    for s in schemas:
        params = s["function"]["parameters"]
        assert params["additionalProperties"] is False
        assert "\n" not in s["function"]["description"]
        for prop in params["properties"].values():
            assert prop["type"] in ("string", "integer")


def test_walking_off_hides_turn_and_step(bridge):
    assert not {"turn", "step"} & set(names(bridge.registry.schemas(bridge.walking)))


def test_walking_toggle_shows_tools(bridge):
    bridge.set_walking(True)
    assert {"turn", "step"} <= set(names(bridge.registry.schemas(bridge.walking)))


def test_missing_behavior_left_out(make_bridge, robot):
    robot.behaviors.remove("showcase/dance_robot")
    b = make_bridge()
    dance = next(s for s in b.registry.schemas(False) if s["function"]["name"] == "dance")
    assert dance["function"]["parameters"]["properties"]["name"]["enum"] == ["macarena"]
    assert any("dances.robot" in e.message for e in b.log.entries("warning"))


def victory() -> Animation:
    return Animation.model_validate({
        "name": "victory", "duration_s": 1.0, "reviewed_by": "lukas",
        "tracks": {"HeadPitch": {"times": [0.5, 1.0], "angles_deg": [-10, 0]}},
    })


def test_keyframe_gesture_in_enum(make_bridge):
    cfg = make_config(gestures={"wave": "animations/Stand/Gestures/Hey_1", "victory": "anim:victory"})
    b = make_bridge(cfg, keyframes={"victory": victory()})
    gesture = next(s for s in b.registry.schemas(False) if s["function"]["name"] == "play_gesture")
    assert "victory" in gesture["function"]["parameters"]["properties"]["name"]["enum"]
    assert b.gate.check_and_run("play_gesture", {"name": "victory"}).ok


def test_successful_gesture(bridge, robot):
    r = bridge.gate.check_and_run("play_gesture", {"name": "wave"})
    assert r.as_dict() == {"ok": True, "result": "finished"}
    assert robot.actions[-1].args == {"path": "animations/Stand/Gestures/Hey_1"}


def test_custom_gesture_runs_behavior(bridge, robot):
    assert bridge.gate.check_and_run("play_gesture", {"name": "bow"}).ok
    assert "run_behavior" in robot.action_names()


def test_reserved_colour(bridge, robot):
    before = robot.eyes
    r = bridge.gate.check_and_run("set_eyes", {"color": "red"})
    assert not r.ok and r.reason == "reserved_colour"
    assert robot.eyes == before


def test_eye_enum_has_no_status_colours(bridge):
    eyes = next(s for s in bridge.registry.schemas(False) if s["function"]["name"] == "set_eyes")
    assert set(eyes["function"]["parameters"]["properties"]["color"]["enum"]) == {
        "white", "purple", "orange", "pink", "cyan"}


def test_colour_reverts(bridge, robot):
    assert bridge.gate.check_and_run("set_eyes", {"color": "purple"}).ok
    assert robot.eyes == "#8000FF"
    time.sleep(0.15)
    assert robot.eyes == "#FFFFFF"


def test_look_left_and_back_to_tracking(bridge, robot):
    assert bridge.gate.check_and_run("look", {"direction": "left"}).ok
    head = next(a for a in robot.actions if a.name == "set_head")
    assert head.args["yaw_rad"] > 0.5
    assert robot.tracking is False
    time.sleep(0.15)
    assert robot.tracking is True


def test_turn_right(make_bridge, robot):
    b = make_bridge(make_config(motion={"walking_enabled": True}))
    assert b.gate.check_and_run("turn", {"degrees": -45}).ok
    move = next(a for a in robot.actions if a.name == "move_to")
    assert move.args == {"x_m": 0.0, "y_m": 0.0, "theta_rad": -0.785}


def test_soft_stop_during_dance(bridge, robot):
    robot.time_scale = 1.0
    out = {}
    t = threading.Thread(target=lambda: out.setdefault("r", bridge.gate.check_and_run("dance", {"name": "macarena"}, source="admin")))
    t.start()
    while bridge.state.state != State.ACTING:
        time.sleep(0.005)
    robot.clear_actions()
    assert bridge.gate.check_and_run("stop", {}).as_dict() == {"ok": True, "result": "stopped"}
    t.join(2)
    assert out["r"].result == "interrupted"
    assert "go_to_posture" not in robot.action_names()
    assert bridge.state.state == State.IDLE


def test_status(bridge, robot):
    robot.battery_pct = 64
    r = bridge.gate.check_and_run("get_status", {})
    assert "64" in r.result and "Stand" in r.result and "ok" in r.result


def test_who_is_here_names_only_enrolled(bridge, robot):
    robot.people = 3
    robot.faces = ["Lukas"]
    r = bridge.gate.check_and_run("who_is_here", {}, lang="en")
    assert r.result == "3 people; known club members: Lukas"


def test_who_is_here_without_face_recognition(make_bridge, robot):
    b = make_bridge(make_config(privacy={"face_recognition_enabled": False}))
    robot.people = 2
    robot.faces = ["Lukas"]
    assert b.gate.check_and_run("who_is_here", {}, lang="en").result == "2 people"


def test_dance_finished(bridge):
    assert bridge.gate.check_and_run("dance", {"name": "macarena"}).as_dict() == {"ok": True, "result": "finished"}


def test_names_not_logged_in_event_mode(make_bridge, robot):
    b = make_bridge(make_config(event_mode=True))
    robot.faces = ["Lukas"]
    b.gate.check_and_run("who_is_here", {})
    assert not any("Lukas" in e.render() for e in b.log.entries())
