import asyncio
import threading

import pytest

from conftest import make_config
from llm_stub import ScriptedClient, call
from naollm.llm.client import LLMUnavailable
from naollm.llm.loop import ConversationLoop
from naollm.llm.prompts import system_prompt
from naollm.llm.sentences import SentenceSplitter
from naollm.state import State


# -- prompts ---------------------------------------------------------------

@pytest.mark.parametrize("lang,phrases", [
    ("de", ["NAO", "Robotik-Club", "Schule X", "auf Deutsch", "zwei kurzen", "Kinder und Jugendliche",
            "nur, wenn es zum Gespräch passt", "persönlichen Daten", "Alter", "Adresse", "Geschlecht",
            "Stimmung", "Gefühle", "keine Bilder", "tanzen", "Gesten", "Robotik reden",
            "Dafür muss ich erst aufstehen"]),
    ("en", ["NAO", "robotics club", "Schule X", "in English", "two short", "children and teenagers",
            "Only use them when it fits", "personal information", "ages", "addresses", "gender", "mood",
            "emotions", "cannot see images", "dance", "gestures", "talk about robotics",
            "I need to stand up first"]),
])
def test_prompt_contains_every_rule(lang, phrases):
    prompt = system_prompt(lang, "Schule X")
    for phrase in phrases:
        assert phrase in prompt, phrase


# -- sentences -------------------------------------------------------------

def test_two_sentences_streamed():
    s = SentenceSplitter()
    assert s.feed("Hallo! Ich") == ["Hallo!"]
    assert s.feed(" bin NAO.") == []
    assert s.flush() == ["Ich bin NAO."]


@pytest.mark.parametrize("text,expected", [
    ("Das ist z.B. toll. Ja!", ["Das ist z.B. toll.", "Ja!"]),
    ("Am 3. Mai tanze ich. Gut?", ["Am 3. Mai tanze ich.", "Gut?"]),
    ("Pi ist 3.14 ungefähr. Ok.", ["Pi ist 3.14 ungefähr.", "Ok."]),
    ("Wirklich?! Ja.", ["Wirklich?!", "Ja."]),
])
def test_abbreviations_and_numbers(text, expected):
    s = SentenceSplitter()
    assert s.feed(text) + s.flush() == expected


# -- turn loop -------------------------------------------------------------

def spoken(robot):
    return [a.args["text"] for a in robot.actions if a.name == "say"]


def run(loop, text, lang=None, **kw):
    return asyncio.run(loop.run_turn(text, lang, **kw))


def test_request_contents_and_defaults(bridge):
    client = ScriptedClient([["Hallo!"]])
    loop = ConversationLoop(bridge, client)
    run(loop, "Hi")
    req = client.requests[0]
    assert req["messages"][0]["role"] == "system" and "auf Deutsch" in req["messages"][0]["content"]
    assert req["messages"][-1] == {"role": "user", "content": "Hi"}
    assert {t["function"]["name"] for t in req["tools"]} >= {"dance", "play_gesture", "stop"}


def test_history_window(bridge):
    client = ScriptedClient([["Ok."]], repeat_last=True)
    loop = ConversationLoop(bridge, client)
    for i in range(8):
        run(loop, f"frage {i}")
    run(loop, "neu")
    msgs = client.requests[-1]["messages"]
    users = [m["content"] for m in msgs if m["role"] == "user"]
    assert users == [f"frage {i}" for i in range(2, 8)] + ["neu"]


def test_english_turn_uses_english_prompt_and_tts(bridge, robot):
    client = ScriptedClient([["Hello!"]])
    loop = ConversationLoop(bridge, client)
    run(loop, "Hi", "en")
    assert "in English" in client.requests[0]["messages"][0]["content"]
    assert any(a.name == "set_language" and a.args["language"] == "English" for a in robot.actions)


def test_sentences_spoken_while_streaming(bridge, robot):
    gate = asyncio.Event()
    robot.time_scale = 0
    spoken_before_end = []

    async def scenario():
        client = ScriptedClient([["Hallo! ", "Ich bin", gate, " NAO."]])
        loop = ConversationLoop(bridge, client)
        task = asyncio.create_task(loop.run_turn("Hi"))
        for _ in range(100):
            await asyncio.sleep(0.01)
            if spoken(robot):
                break
        spoken_before_end.extend(spoken(robot))
        gate.set()
        await task

    asyncio.run(scenario())
    assert spoken_before_end == ["Hallo!"]
    assert spoken(robot) == ["Hallo!", "Ich bin NAO."]


def test_tool_then_answer(bridge, robot):
    client = ScriptedClient([[call("dance", {"name": "macarena"})], ["Das hat Spaß gemacht!"]])
    loop = ConversationLoop(bridge, client)
    result = run(loop, "Tanz mal!")
    assert result.status == "answered"
    assert result.tool_results == [("dance", {"ok": True, "result": "finished"})]
    second = client.requests[1]["messages"]
    assert second[-1]["role"] == "tool" and '"finished"' in second[-1]["content"]
    assert second[-2]["role"] == "assistant" and second[-2]["content"] is None
    assert spoken(robot) == ["Das hat Spaß gemacht!"]
    assert "run_behavior" in robot.action_names()


def test_tool_only_response_adds_no_speech(bridge, robot):
    client = ScriptedClient([[call("set_eyes", {"color": "pink"})], []])
    loop = ConversationLoop(bridge, client)
    run(loop, "Augen pink")
    assert spoken(robot) == []


def test_endless_tool_calls(bridge, robot):
    client = ScriptedClient([[call("play_gesture", {"name": "nod"})]], repeat_last=True)
    loop = ConversationLoop(bridge, client)
    result = run(loop, "nick")
    assert len(client.requests) == 3
    assert result.status == "fallback"
    assert spoken(robot)[-1] == "Da bin ich durcheinander gekommen."


def test_speak_before_acting(bridge, robot):
    client = ScriptedClient([["Schau mal!", call("play_gesture", {"name": "wave"})], ["Hallo!"]])
    loop = ConversationLoop(bridge, client)
    run(loop, "Wink mal")
    names = robot.action_names()
    assert names.index("say") < names.index("play_animation")


def test_bad_json_twice_drops_tools(bridge, robot):
    client = ScriptedClient([
        [call("dance", '{"name": ')],
        [call("dance", "{oops")],
        ["Ich tanze lieber später."],
    ])
    loop = ConversationLoop(bridge, client)
    result = run(loop, "Tanz")
    assert client.requests[0]["tools"] and client.requests[1]["tools"]
    assert client.requests[2]["tools"] is None
    assert result.status == "answered" and spoken(robot) == ["Ich tanze lieber später."]
    assert "run_behavior" not in robot.action_names()


def test_single_malformed_call_keeps_tools(bridge):
    client = ScriptedClient([[call("fly", {})], [call("play_gesture", {"name": "wave"})], ["Fertig."]])
    loop = ConversationLoop(bridge, client)
    run(loop, "Flieg")
    assert client.requests[2]["tools"] is not None


def test_timeout_apologises(bridge, robot):
    client = ScriptedClient([[LLMUnavailable("timeout")]])
    loop = ConversationLoop(bridge, client)
    result = run(loop, "Hallo")
    assert result.status == "apology"
    assert spoken(robot) == ["Entschuldigung, mir fällt gerade keine Antwort ein."]
    assert bridge.state.state == State.IDLE


def test_estop_during_turn_cancels_pending_tool(bridge, robot):
    hold = asyncio.Event()
    robot.time_scale = 0

    async def scenario():
        client = ScriptedClient([["Ich tanze gleich! ", hold, "Los!", call("dance", {"name": "macarena"})]])
        loop = ConversationLoop(bridge, client)
        task = loop.start_turn("Tanz")
        for _ in range(100):
            await asyncio.sleep(0.01)
            if spoken(robot):
                break
        t = threading.Thread(target=bridge.estop, args=("hotkey",))
        t.start()
        t.join()
        hold.set()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())
    assert spoken(robot) == ["Ich tanze gleich!"]
    assert "run_behavior" not in robot.action_names()
    assert bridge.state.state == State.STOPPED


def test_turn_refused_while_stopped(bridge):
    bridge.estop("command")
    client = ScriptedClient([["Hallo"]])
    result = run(ConversationLoop(bridge, client), "Hallo")
    assert result.status == "refused" and client.requests == []


def test_idle_reset(bridge):
    client = ScriptedClient([["Ok."]], repeat_last=True)
    loop = ConversationLoop(bridge, client)
    run(loop, "eins", now=1000.0)
    run(loop, "zwei", now=1061.0)
    users = [m["content"] for m in client.requests[-1]["messages"] if m["role"] == "user"]
    assert users == ["zwei"]


def test_history_kept_within_idle_window(bridge):
    client = ScriptedClient([["Ok."]], repeat_last=True)
    loop = ConversationLoop(bridge, client)
    run(loop, "eins", now=1000.0)
    run(loop, "zwei", now=1030.0)
    users = [m["content"] for m in client.requests[-1]["messages"] if m["role"] == "user"]
    assert users == ["eins", "zwei"]


def test_event_mode_log_has_no_text(make_bridge, robot):
    b = make_bridge(make_config(event_mode=True))
    client = ScriptedClient([[call("play_gesture", {"name": "wave"})], ["Geheimnis Antwort."]])
    run(ConversationLoop(b, client), "Mein Geheimnis")
    rendered = "\n".join(e.render() for e in b.log.entries())
    assert "Geheimnis" not in rendered
    assert "play_gesture" in rendered and "ms=" in rendered


def test_debug_log_keeps_text_outside_event_mode(bridge):
    client = ScriptedClient([["Antwort."]])
    run(ConversationLoop(bridge, client), "Frage")
    rendered = "\n".join(e.render() for e in bridge.log.entries())
    assert "Frage" in rendered and "Antwort." in rendered
