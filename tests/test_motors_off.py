"""Motors-off mode against the fake robot: every routine skips motion and nothing else."""

import asyncio

import pytest

from conftest import make_config
from llm_stub import ScriptedClient
from naollm.admin.commands import Commands
from naollm.bridge import Bridge, RobotNotAtRest
from naollm.llm.loop import ConversationLoop
from naollm.llm.prompts import system_prompt
from naollm.robot.adapter import EventKind
from naollm.state import State
from naollm.tools.registry import MOTOR_TOOLS

MOTOR_ACTIONS = {"wake_up", "rest", "go_to_posture", "stop_all_behaviors", "stop_move", "set_balancer",
                 "move_to", "set_head", "run_behavior", "play_animation", "play_keyframes", "set_tracking"}
OFF = {"motors_enabled": False}


def no_motor_actions(robot):
    return not MOTOR_ACTIONS & set(robot.action_names())


def run(commands, *lines):
    async def go():
        for line in lines:
            await commands.execute(line)
    asyncio.run(go())


def make_commands(bridge, conversation=None):
    lines = []

    async def confirm(question):
        return "y"

    return Commands(bridge, "admin", lines.append, confirm, conversation), lines


# -- startup and at rest ---------------------------------------------------

def test_startup_commands_no_motor(off_robot):
    off_robot.clear_actions()
    b = Bridge(make_config(motion=OFF), off_robot)
    b.startup()
    assert b.state.state == State.IDLE
    assert no_motor_actions(off_robot)
    assert off_robot.eyes == "#FFFFFF"
    assert "say" in off_robot.action_names()


def test_stiff_robot_refuses_start(off_robot):
    off_robot.stiff = True
    b = Bridge(make_config(motion=OFF), off_robot)
    off_robot.clear_actions()
    with pytest.raises(RobotNotAtRest, match="awake or stiff"):
        b.startup()
    assert off_robot.action_names() == ["connect", "disconnect"]
    assert not off_robot.connected


def test_autonomous_life_refuses_start_and_names_init(off_robot):
    off_robot.autonomous_life_state = "solitary"
    b = Bridge(make_config(motion=OFF), off_robot)
    with pytest.raises(RobotNotAtRest, match="naollm init"):
        b.startup()
    assert off_robot.autonomous_life_state == "solitary"


def test_robot_with_motors_enabled_still_wakes(robot):
    b = Bridge(make_config(), robot)
    robot.clear_actions()
    b.startup(greet=False)
    assert {"wake_up", "go_to_posture", "set_tracking"} <= set(robot.action_names())


# -- wake-up during a run --------------------------------------------------

def test_robot_waking_is_reported_without_a_motor_command(off_bridge, off_robot):
    off_robot.stiff = True
    off_bridge.rest_tick()
    assert off_bridge.state.state == State.STOPPED
    assert any("ROBOT AWAKE" in e.message for e in off_bridge.log.entries("alert"))
    assert no_motor_actions(off_robot)
    assert off_robot.eyes == "#FF0000"


def test_waking_alert_is_not_repeated(off_bridge, off_robot):
    off_robot.stiff = True
    off_bridge.rest_tick()
    off_bridge.rest_tick()
    assert len([e for e in off_bridge.log.entries("alert") if "ROBOT AWAKE" in e.message]) == 1


def test_resume_refused_while_awake(off_bridge, off_robot):
    off_robot.stiff = True
    off_bridge.rest_tick()
    assert not off_bridge.resume()
    off_robot.stiff = False
    assert off_bridge.resume()
    assert off_bridge.state.state == State.IDLE


def test_rest_tick_quiet_at_rest_and_when_offline(off_bridge, off_robot):
    off_bridge.rest_tick()
    assert off_bridge.state.state == State.IDLE
    off_robot.inject(EventKind.CONNECTION_LOST)
    off_robot.stiff = True
    off_bridge.rest_tick()
    assert off_bridge.state.state == State.OFFLINE


def test_rest_tick_does_nothing_with_motors_enabled(bridge):
    bridge.rest_tick()  # the fake is stiff here
    assert bridge.state.state == State.IDLE


# -- routines --------------------------------------------------------------

def test_estop_has_no_motion_and_no_failed_steps(off_bridge, off_robot):
    cancelled = []
    off_bridge.register_turn(lambda: cancelled.append(True))
    off_robot.inject(EventKind.REAR_HEAD)
    assert off_bridge.state.state == State.STOPPED
    assert cancelled == [True]
    assert "stop_speech" in off_robot.action_names()
    assert off_robot.eyes == "#FF0000"
    assert no_motor_actions(off_robot)
    assert off_bridge.log.entries("error") == []
    assert "failed" not in off_bridge.log.entries("estop")[-1].fields


def test_shutdown_only_unsubscribes_and_disconnects(off_bridge, off_robot):
    off_bridge.shutdown()
    assert off_robot.action_names() == ["disconnect"]
    assert off_bridge.state.state == State.OFFLINE
    assert off_robot._handlers == []


def test_idle_timeout_issues_no_posture(off_bridge, off_robot):
    off_robot.posture = "Stand"
    off_bridge.idle_tick(now=off_bridge.last_activity + 10_000)
    assert no_motor_actions(off_robot)
    assert not off_bridge.idle_crouched


def test_before_turn_issues_no_posture(off_bridge, off_robot):
    off_bridge.idle_crouched = True
    off_bridge.before_turn()
    assert no_motor_actions(off_robot)


def test_fall_only_changes_state(off_bridge, off_robot):
    off_robot.inject(EventKind.FALL)
    assert off_bridge.state.state == State.FALLEN
    assert no_motor_actions(off_robot)


def test_critical_battery_skips_motion(off_bridge, off_robot):
    off_robot.inject(EventKind.BATTERY, 5)
    assert off_bridge.state.state == State.RESTING
    assert no_motor_actions(off_robot)
    says = [a.args["text"] for a in off_robot.actions if a.name == "say"]
    assert says and "setze mich" not in says[0]
    assert "Pause" in says[0]


def test_hot_joints_skip_motion(off_bridge, off_robot):
    off_robot.inject(EventKind.THERMAL, "hot")
    assert off_bridge.state.state == State.RESTING
    assert no_motor_actions(off_robot)


def test_catalogue_logs_one_summary_line(off_robot):
    b = Bridge(make_config(motion=OFF, dances={"gone": "showcase/gone"}), off_robot)
    b.startup(greet=False)
    assert b.log.entries("warning") == []
    assert len([e for e in b.log.entries("robot") if "catalogue" in e.message]) == 1


# -- gate and tools --------------------------------------------------------

MOTOR_CALLS = [
    ("play_gesture", {"name": "wave"}),
    ("dance", {"name": "macarena"}),
    ("set_posture", {"posture": "Stand"}),
    ("look", {"direction": "left"}),
    ("turn", {"degrees": 10}),
    ("step", {"direction": "forward", "cm": 10}),
    ("stop", {}),
]


@pytest.mark.parametrize("source", ["llm", "admin", "idle"])
@pytest.mark.parametrize("name,args", MOTOR_CALLS)
def test_gate_rejects_every_motion_tool_from_every_source(off_bridge, off_robot, name, args, source):
    result = off_bridge.gate.check_and_run(name, args, source=source)
    assert not result.ok and result.reason == "motors_off"
    assert "bewegen" in result.error
    assert no_motor_actions(off_robot)


def test_motors_off_reason_survives_an_unknown_action(off_bridge, off_robot):
    """On the real robot the dance enum is empty, because the package is not installed."""
    result = off_bridge.gate.check_and_run("dance", {"name": "tango"}, source="admin")
    assert result.reason == "motors_off"


def test_malformed_motion_call_is_still_malformed(off_bridge):
    assert off_bridge.gate.check_and_run("turn", {"degrees": 500}).reason == "too_far_turn"
    assert off_bridge.gate.check_and_run("step", {"direction": "forward"}).reason == "missing_arg"


def test_eyes_and_status_still_work(off_bridge, off_robot):
    assert off_bridge.gate.check_and_run("set_eyes", {"color": "purple"}).ok
    assert off_robot.eyes == "#8000FF"
    assert off_bridge.gate.check_and_run("get_status", {}).ok


def test_perception_off_rejects_who_is_here(off_bridge, off_robot):
    off_robot.available = False
    off_bridge.refresh_catalogue()
    result = off_bridge.gate.check_and_run("who_is_here", {}, source="admin")
    assert not result.ok and result.reason == "perception_off"
    names = {s["function"]["name"] for s in off_bridge.registry.schemas(False)}
    assert "who_is_here" not in names


def test_llm_is_not_offered_motion(off_bridge):
    names = {s["function"]["name"] for s in off_bridge.registry.schemas(True)}
    assert not names & MOTOR_TOOLS
    assert names == {"set_eyes", "get_status", "who_is_here"}


def test_turn_request_lists_only_allowed_tools_and_motor_free_prompt(off_bridge):
    client = ScriptedClient([["Hello."]])
    loop = ConversationLoop(off_bridge, client)
    asyncio.run(loop.run_turn("Can you dance?", "en"))
    request = client.requests[0]
    assert not {t["function"]["name"] for t in request["tools"]} & MOTOR_TOOLS
    prompt = request["messages"][0]["content"]
    assert "cannot move right now" in prompt and "dance" not in prompt


@pytest.mark.parametrize("lang,phrase", [("en", "cannot move right now"), ("de", "gerade nicht bewegen")])
def test_motor_free_prompts(lang, phrase):
    text = system_prompt(lang, "X", motors=False)
    assert phrase in text
    for word in ("dance", "gesture", "tanzen", "Gesten", "stand up", "aufstehen"):
        assert word not in text
    assert "X" in text


def test_default_prompts_unchanged():
    assert "dance" in system_prompt("en", "X")
    assert system_prompt("en", "X") == system_prompt("en", "X", motors=True)


# -- console ---------------------------------------------------------------

@pytest.mark.parametrize("line", ["wake", "rest", "posture Stand", "walk on", "anim play victory"])
def test_console_motion_commands_refuse(off_bridge, off_robot, line):
    commands, lines = make_commands(off_bridge)
    state = off_bridge.state.state
    run(commands, line)
    assert any("motors are off" in text for text in lines), lines
    assert no_motor_actions(off_robot)
    assert off_bridge.state.state == state
    assert not off_bridge.walking


def test_walk_off_still_allowed(off_bridge):
    commands, lines = make_commands(off_bridge)
    run(commands, "walk off")
    assert lines == ["walking OFF"]


def test_walking_stays_off_even_if_configured(off_robot, make_bridge):
    b = make_bridge(make_config(motion={"motors_enabled": False, "walking_enabled": True}))
    assert not b.walking


def test_status_and_tools_show_motors_off(off_bridge, off_robot):
    commands, lines = make_commands(off_bridge)
    run(commands, "status", "tools", "behaviors")
    assert "motors OFF" in lines[0]
    assert any("motors are off" in text for text in lines)
    assert no_motor_actions(off_robot)
    assert "run_behavior" not in off_robot.action_names()


def test_admin_motion_tool(off_bridge, off_robot):
    commands, lines = make_commands(off_bridge)
    run(commands, "tool dance macarena")
    assert "refused" in lines[-1]
    assert no_motor_actions(off_robot)


def test_command_error_does_not_end_the_console(off_bridge, off_robot):
    commands, lines = make_commands(off_bridge)
    off_robot.fail_on["say"] = RuntimeError("no session")
    run(commands, 'say "hello"')
    assert lines == ["error: no session"]


# -- battery reliability ---------------------------------------------------

BAD_BATTERY = {"url": "tcp://10.0.0.2:9559", "battery_reliable": False}


def test_status_tool_has_no_battery_value(off_robot, make_bridge):
    b = make_bridge(make_config(robot=BAD_BATTERY, motion=OFF))
    for lang, word in (("en", "battery"), ("de", "Akku")):
        result = b.gate.check_and_run("get_status", {}, lang=lang)
        assert word not in result.result and "%" not in result.result


def test_status_tool_keeps_battery_by_default(off_bridge):
    assert "100 %" in off_bridge.gate.check_and_run("get_status", {}).result


def test_status_line_hides_battery(off_robot, make_bridge):
    b = make_bridge(make_config(robot=BAD_BATTERY, motion=OFF))
    commands, _ = make_commands(b)
    line = commands.status_line()
    assert "battery hidden" in line and "%" not in line


def test_low_reading_is_only_logged(off_robot, make_bridge):
    b = make_bridge(make_config(robot=BAD_BATTERY, motion=OFF))
    off_robot.inject(EventKind.BATTERY, 5)
    assert b.state.state == State.IDLE
    assert any("5 %" in e.message for e in b.log.entries("sensor"))
    assert b.log.entries("alert") == []
    assert off_robot.action_names() == []


# -- language handling -----------------------------------------------------

EN = dict(robot={"url": "tcp://10.0.0.2:9559", "language": "English"},
          stt={"languages": ["en"], "fallback_language": "en"}, motion=OFF)


def test_english_only_startup_logs_no_language_warning(off_robot):
    off_robot.languages = ["Japanese", "Chinese", "English"]
    b = Bridge(make_config(**EN), off_robot)
    b.startup(greet=False)
    assert not [e for e in b.log.entries("warning") if "language" in e.message]
    assert off_robot.language == "English"


def test_startup_warns_for_configured_language_without_voice(off_robot):
    off_robot.languages = ["English"]
    b = Bridge(make_config(motion=OFF, robot={"url": "tcp://10.0.0.2:9559", "language": "English"}), off_robot)
    b.startup(greet=False)
    warnings = [e.message for e in b.log.entries("warning")]
    assert any("German" in m for m in warnings) and not any("English" in m for m in warnings)


def test_turn_without_a_voice_falls_back(off_robot):
    off_robot.languages = ["Japanese", "Chinese", "English"]
    b = Bridge(make_config(**EN), off_robot)
    b.startup(greet=False)
    off_robot.clear_actions()
    client = ScriptedClient([["Hello."]])
    commands = Commands(b, "run", lambda s: None, None, ConversationLoop(b, client))
    run(commands, "chat --de hallo")
    assert "in English" in client.requests[0]["messages"][0]["content"]
    assert any(a.name == "set_language" and a.args["language"] == "English" for a in off_robot.actions)
    assert not any(a.name == "set_language" and a.args["language"] == "German" for a in off_robot.actions)
    assert any("no German voice" in e.message for e in b.log.entries("warning"))
