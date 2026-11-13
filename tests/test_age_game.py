"""The age game (age-game spec, SPEC §8.6) against the fake robot and the strict qi double."""

import asyncio
import json
import re

import pytest

from conftest import make_config
from llm_stub import ScriptedClient, call
from naollm.admin.commands import Commands
from naollm.bridge import Bridge
from naollm.config import ConfigError
from naollm.llm.loop import ConversationLoop
from naollm.llm.prompts import system_prompt
from naollm.robot.adapter import AgeEstimate
from naollm.robot.qi_adapter import QiRobot
from naollm.state import State
from naollm.tools.handlers import age_line
from qi_double import QiDouble

GAME = {"age_game_enabled": True}


def game_bridge(robot, **privacy):
    b = Bridge(make_config(privacy={**GAME, **privacy}), robot)
    b.startup(greet=False)
    robot.clear_actions()
    return b


def offered(bridge):
    return {t["function"]["name"] for t in bridge.registry.schemas(bridge.walking)}


def said(robot):
    return [a.args["text"] for a in robot.actions if a.name == "say"]


# -- off unless enabled -------------------------------------------------------------

def test_default_configuration_does_not_offer_the_tool(bridge):
    assert "guess_age" not in offered(bridge)
    result = bridge.gate.check_and_run("guess_age", {}, source="admin")
    assert not result.ok and result.reason == "age_game_off"


@pytest.mark.parametrize("source", ["llm", "admin"])
def test_off_is_refused_for_every_source(bridge, source):
    assert bridge.gate.check_and_run("guess_age", {}, source=source).reason == "age_game_off"


def test_enabled_offers_the_tool_with_no_parameters(robot):
    b = game_bridge(robot)
    assert "guess_age" in offered(b)
    schema = next(t for t in b.registry.schemas(False) if t["function"]["name"] == "guess_age")
    assert schema["function"]["parameters"]["properties"] == {}
    assert len(b.registry.schemas(False)) <= 10


def test_enabled_but_service_missing(robot):
    robot.age_service = False
    b = game_bridge(robot)
    assert b.state.state == State.IDLE
    assert "guess_age" not in offered(b)
    assert any("AGE GAME UNAVAILABLE" in e.message for e in b.log.entries("alert"))
    assert b.gate.check_and_run("guess_age", {}).reason == "age_game_off"


# -- one person, age only -------------------------------------------------------------

def test_two_people_take_no_estimate(robot):
    b = game_bridge(robot)
    robot.people = 2
    robot.age_estimate = AgeEstimate(12, 0.9)
    result = b.gate.check_and_run("guess_age", {}, lang="en")
    assert not result.ok and result.reason == "age_several" and "several people" in result.error
    assert "estimate_age" not in robot.action_names() and said(robot) == []


def test_nobody_visible(robot):
    b = game_bridge(robot)
    robot.people = 0
    assert b.gate.check_and_run("guess_age", {}).reason == "age_nobody"


def test_low_confidence_speaks_no_range(robot):
    b = game_bridge(robot)
    robot.people = 1
    robot.age_estimate = AgeEstimate(12, 0.3)
    result = b.gate.check_and_run("guess_age", {}, lang="en")
    assert not result.ok and result.reason == "age_unsure"
    assert result.error == "I can't see you well enough to guess."
    assert said(robot) == []


def test_refused_while_stopped(robot):
    b = game_bridge(robot)
    robot.people = 1
    robot.age_estimate = AgeEstimate(12, 0.9)
    b.estop("command")
    assert b.gate.check_and_run("guess_age", {}).reason == "stopped"


# -- the number never reaches the LLM ---------------------------------------------------

def test_successful_guess(robot):
    b = game_bridge(robot)
    robot.people = 1
    robot.age_estimate = AgeEstimate(12, 0.7)
    result = b.gate.check_and_run("guess_age", {}, lang="en")
    assert result.as_dict() == {"ok": True, "result": "told the visitor a rough age range"}
    assert said(robot) == ["I'd guess you are between 10 and 15 years old. But I'm only a robot, so I might be wrong!"]


def test_german_range():
    assert "zwischen 10 und 15" in age_line(14.9, "de")
    assert age_line(4.0, "en") == "You look very young to me!"
    assert age_line(81, "de") == "Du siehst für mich sehr erwachsen aus!"
    assert "between 80 and 85" in age_line(80, "en")


def test_log_after_a_guess_in_debug_mode_holds_no_age(robot):
    b = Bridge(make_config(privacy={**GAME, "debug_logging": True}, event_mode=False), robot)
    b.startup(greet=False)
    robot.people = 1
    robot.age_estimate = AgeEstimate(37.4, 0.8)
    assert b.gate.check_and_run("guess_age", {}).ok
    entries = [e.render() for e in b.log.entries()]
    tool_lines = [line for line in entries if "guess_age" in line]
    assert tool_lines and all("ok" in line for line in tool_lines)
    for line in entries:
        assert not re.search(r"\b(35|37|40)\b", line.split(" ", 1)[1]), line


def test_no_number_in_messages_or_history(robot):
    b = game_bridge(robot)
    robot.people = 1
    robot.age_estimate = AgeEstimate(23, 0.9)
    client = ScriptedClient([[call("guess_age", {})], ["Was I close?"], ["Bye!"]])
    loop = ConversationLoop(b, client)
    asyncio.run(loop.run_turn("Guess how old I am!", "en"))
    asyncio.run(loop.run_turn("Thanks", "en"))
    assert any("between 20 and 25" in text for text in said(robot))
    sent = json.dumps(client.requests)
    assert "20" not in sent.replace("2026", "") and "25" not in sent and "23" not in sent
    assert "told the visitor a rough age range" in sent
    assert all("20" not in user + answer and "25" not in user + answer for user, answer in loop.history)


# -- prompt -------------------------------------------------------------------------------

@pytest.mark.parametrize("motors", [True, False])
@pytest.mark.parametrize("lang", ["de", "en"])
def test_prompt_rule_only_with_the_game(lang, motors):
    on = system_prompt(lang, "x", motors, age_game=True)
    off = system_prompt(lang, "x", motors)
    assert "guess_age" in on and "guess_age" not in off


def test_loop_uses_the_rule(robot):
    b = game_bridge(robot)
    client = ScriptedClient([["Hi!"]])
    asyncio.run(ConversationLoop(b, client).run_turn("Hello", "en"))
    assert "guess_age" in client.requests[0]["messages"][0]["content"]


# -- tool budget -----------------------------------------------------------------------------

def test_walking_with_the_game_is_rejected_at_load():
    with pytest.raises(ConfigError, match="walking_enabled"):
        make_config(privacy=GAME, motion={"walking_enabled": True})


def test_walk_on_refuses_and_names_the_game(robot):
    b = game_bridge(robot)
    lines = []
    asyncio.run(Commands(b, "admin", lines.append, None).execute("walk on"))
    assert "age game" in lines[0] and not b.walking


# -- operator visibility -------------------------------------------------------------------

def test_startup_warning_and_status_flag(robot):
    b = game_bridge(robot)
    assert any("AGE GAME ON" in e.message for e in b.log.entries("alert"))
    assert "AGE GAME ON" in Commands(b, "admin", print, None).status_line()


def test_no_flag_without_the_game(bridge):
    assert "AGE GAME" not in Commands(bridge, "admin", print, None).status_line()


@pytest.mark.parametrize("lang,phrase", [("English", "guess your age"), ("German", "dein Alter")])
def test_notice_mentions_the_game(robot, lang, phrase):
    b = Bridge(make_config(privacy=GAME, robot={"url": "tcp://10.0.0.2:9559", "language": lang}), robot)
    b.startup(greet=False)
    robot.clear_actions()
    asyncio.run(Commands(b, "admin", print, None).execute("notice"))
    text = said(robot)[0]
    assert phrase in text and ("not stored" in text or "nicht gespeichert" in text)


# -- the real adapter ------------------------------------------------------------------------

def qi_bridge(double):
    robot = QiRobot(qi_module=double)
    b = Bridge(make_config(privacy=GAME, motion={"motors_enabled": False},
                           robot={"url": "tcp://10.0.0.2:9559", "battery_reliable": False}), robot)
    b.startup(greet=False)
    return b


def test_real_adapter_reads_only_the_age():
    double = QiDouble()
    double.visible_people = [7]
    double.ages[7] = [41.0, 0.8]
    b = qi_bridge(double)
    result = b.gate.check_and_run("guess_age", {}, lang="en")
    assert result.ok
    assert double.called("ALFaceCharacteristics", "analyzeFaceCharacteristics") == [(7,)]
    assert double.called("ALTextToSpeech", "say")[-1][0].startswith("I'd guess you are between 40 and 45")
    reads = [a[0] for a in double.called("ALMemory", "getData")]
    assert "PeoplePerception/Person/7/AgeProperties" in reads
    assert double.violations == []
    b.shutdown()


def test_forbidden_keys_fail_the_double():
    double = QiDouble()
    double.is_open = True
    with pytest.raises(AssertionError):
        double.session.service("ALMemory").getData("PeoplePerception/Person/7/GenderProperties")


def test_real_adapter_with_service_missing():
    double = QiDouble()
    double.missing_services.add("ALFaceCharacteristics")
    b = qi_bridge(double)
    assert not b.age_game and "guess_age" not in offered(b)
    assert any("AGE GAME UNAVAILABLE" in e.message for e in b.log.entries("alert"))
    b.shutdown()


def test_real_adapter_probe_takes_no_estimate():
    double = QiDouble()
    double.visible_people = [7]
    double.ages[7] = [41.0, 0.8]
    b = qi_bridge(double)
    assert b.age_game
    assert double.called("ALFaceCharacteristics", "analyzeFaceCharacteristics") == []
    b.shutdown()
