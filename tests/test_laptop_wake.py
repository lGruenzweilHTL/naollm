"""The laptop wake engine against the fake robot with a stub model (laptop-wake-word, voice-turn)."""

import asyncio
import sys
import time
from collections import deque
from types import SimpleNamespace

import pytest

from conftest import make_config
from llm_stub import ScriptedClient
from naollm.admin.commands import Commands
from naollm.audio.listener import Listener
from naollm.audio.wake_laptop import (
    FRAME_BYTES,
    MAX_BUFFER_SAMPLES,
    MODEL_BUFFER_SAMPLES,
    LaptopWake,
    OpenWakeWordModel,
    WakeUnavailable,
)
from naollm.bridge import Bridge
from naollm.config import ConfigError, parse_config
from naollm.llm.loop import ConversationLoop
from naollm.state import State

LOUD = b"\xff\x7f" * (FRAME_BYTES // 2)     # the stub model scores this frame 0.7
QUIET = b"\x01\x00" * (FRAME_BYTES // 2)
ROBOT_CHUNK = 1365 * 2


class StubModel:
    def __init__(self):
        self.samples = 0
        self.resets = 0
        self.scored = 0

    def score(self, frame):
        self.scored += 1
        self.samples = min(self.samples + len(frame) // 2, MODEL_BUFFER_SAMPLES)
        return 0.7 if frame[:2] == b"\xff\x7f" else 0.05

    def reset(self):
        self.resets += 1
        self.samples = 0

    def buffered_samples(self):
        return self.samples


def energy_test(frame):
    return any(frame)


class STT:
    def __init__(self):
        self.seen = []

    def ensure_loaded(self):
        pass

    def transcribe(self, pcm):
        self.seen.append(pcm)
        return SimpleNamespace(text="Hello robot", language="en")


def laptop_config(**audio):
    return make_config(motion={"motors_enabled": False}, robot={"url": "tcp://10.0.0.2:9559", "language": "English"},
                       stt={"languages": ["en"], "fallback_language": "en"},
                       audio={"wake_engine": "laptop", "wake_model": "/models/hey_nao.onnx",
                              "laptop_wake_threshold": 0.5, "vad_silence_s": 0.3, "no_speech_timeout_s": 0.3,
                              "max_utterance_s": 1.5, **audio})


class World:
    def __init__(self, robot, config=None, loader=None, script=None):
        self.robot = robot
        self.model = StubModel()
        self.bridge = Bridge(config or laptop_config(), robot)
        self.conversation = ConversationLoop(self.bridge, ScriptedClient(script or [["Hello there."]] * 5))
        self.stt = STT()
        self.listener = Listener(self.bridge, self.conversation, self.stt, speech_test=energy_test,
                                 wake_model_loader=loader or (lambda path: self.model))
        self.listener.wake_resume_s = 0.02
        self.lines = []

        async def confirm(question):
            return "y"

        self.commands = Commands(self.bridge, "run", self.lines.append, confirm, self.conversation,
                                 listener=self.listener)

    async def up(self):
        self.bridge.startup(greet=False)
        await self.listener.prepare()

    def state(self):
        return self.bridge.state.state


async def until(predicate, timeout=3.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return
        await asyncio.sleep(0.005)
    raise AssertionError("condition not reached")


def run(coro):
    asyncio.run(coro)


# -- config ------------------------------------------------------------------

def test_default_engine_is_the_robot():
    assert parse_config({"robot": {"url": "tcp://10.0.0.2:9559"}}).audio.wake_engine == "robot"
    assert parse_config({"robot": {"url": "tcp://10.0.0.2:9559"}}).audio.laptop_wake_threshold == 0.5


def test_laptop_without_a_model_is_rejected():
    with pytest.raises(ConfigError, match=r"audio\.wake_model"):
        parse_config({"robot": {"url": "x"}, "audio": {"wake_engine": "laptop"}})


def test_threshold_range():
    with pytest.raises(ConfigError, match="laptop_wake_threshold"):
        parse_config({"robot": {"url": "x"}, "audio": {"laptop_wake_threshold": 1.5}})


# -- detection -------------------------------------------------------------------

def test_wake_phrase_starts_listening(off_robot):
    w = World(off_robot)

    async def go():
        await w.up()
        await until(lambda: off_robot.stream_active)
        w.listener.laptop.feed(LOUD)
        await until(lambda: "play_listen_sound" in off_robot.action_names())
        assert w.state() in (State.LISTENING, State.THINKING, State.IDLE)
        await until(lambda: w.state() == State.IDLE and not w.conversation.busy)

    run(go())
    assert any("laptop wake word 0.70" in e.message for e in w.bridge.log.entries("sensor"))


def test_robot_engine_is_not_set_up(off_robot):
    w = World(off_robot)

    async def go():
        await w.up()
        await until(lambda: off_robot.stream_active)

    run(go())
    names = off_robot.action_names()
    assert "configure_wake_words" not in names and "set_wake_enabled" not in names


def test_idle_holds_only_the_detection_stream_and_at_most_2_s(off_robot):
    w = World(off_robot)

    async def go():
        await w.up()
        await until(lambda: off_robot.stream_active)
        for _ in range(100):  # about 4 s of robot chunks
            w.listener.laptop.feed(b"\x01\x00" * (ROBOT_CHUNK // 2))
        assert w.listener.laptop.buffered_samples() <= MAX_BUFFER_SAMPLES
        assert off_robot.action_names().count("start_stream") == 1
        assert w.state() == State.IDLE

    run(go())


def test_robot_speaking_stops_the_stream_until_after_idle(off_robot):
    w = World(off_robot, script=[["A fairly long answer so that speaking takes a moment."]])
    seen = []

    async def go():
        await w.up()
        await until(lambda: off_robot.stream_active)
        task = w.conversation.start_turn("hello", "en")
        await until(lambda: w.state() == State.THINKING or w.conversation.busy)
        await until(lambda: "say" in off_robot.action_names())
        seen.append(off_robot.stream_active)
        await task
        await until(lambda: off_robot.stream_active)

    run(go())
    assert seen == [False]
    names = off_robot.action_names()
    assert names.index("stop_stream") < names.index("say")


def test_ears_off_stops_the_stream_until_ears_on(off_robot):
    w = World(off_robot)

    async def go():
        await w.up()
        await until(lambda: off_robot.stream_active)
        await w.commands.execute("ears off")
        assert not off_robot.stream_active
        await asyncio.sleep(0.1)
        assert not off_robot.stream_active
        await w.commands.execute("ears on")
        await until(lambda: off_robot.stream_active)

    run(go())


def test_trigger_clears_the_buffer_and_listening_has_its_own_stream(off_robot):
    w = World(off_robot)

    async def go():
        await w.up()
        await until(lambda: off_robot.stream_active)
        w.listener.laptop.feed(QUIET + QUIET)
        w.listener.laptop.feed(LOUD)
        assert w.listener.laptop.buffered_samples() == 0
        await until(lambda: off_robot.action_names().count("start_stream") >= 2)
        await until(lambda: w.state() == State.IDLE and not w.conversation.busy)

    run(go())
    names = off_robot.action_names()
    first = names.index("start_stream")
    second = names.index("start_stream", first + 1)
    assert "stop_stream" in names[first:second]  # the detection stream ended before listening began
    assert "play_listen_sound" in names[first:second]


def test_late_chunk_after_a_pause_is_dropped():
    hits = []
    model = StubModel()
    wake = LaptopWake(model, 0.5, hits.append)
    wake.feed(LOUD)
    assert hits == [] and model.scored == 0  # not started
    wake.start()
    wake.feed(LOUD)
    wake.feed(LOUD)  # inside the refractory time: one phrase triggers once
    assert hits == [0.7]
    wake.stop()
    wake.feed(LOUD)
    assert hits == [0.7]


def test_reframer_turns_robot_chunks_into_80_ms_frames():
    model = StubModel()
    wake = LaptopWake(model, 0.99, lambda score: None)
    wake.start()
    wake.feed(b"\0" * ROBOT_CHUNK)
    assert model.scored == 1 and wake.buffered_samples() == 85 + model.samples
    wake.feed(b"\0" * ROBOT_CHUNK)
    assert model.scored == 2 and wake.buffered_samples() == 170 + model.samples


# -- the real model wrapper ----------------------------------------------------------

def test_missing_model_file_names_the_path(tmp_path):
    with pytest.raises(WakeUnavailable, match="hey_nao.onnx"):
        OpenWakeWordModel(str(tmp_path / "hey_nao.onnx"))


def test_missing_extra_names_it(tmp_path, monkeypatch):
    model = tmp_path / "hey_nao.onnx"
    model.write_bytes(b"onnx")
    monkeypatch.setitem(sys.modules, "openwakeword", None)
    monkeypatch.setitem(sys.modules, "openwakeword.model", None)
    with pytest.raises(WakeUnavailable, match=r"naollm\[wake\]"):
        OpenWakeWordModel(str(model))


def test_openwakeword_raw_buffer_is_capped_at_2_s():
    wrapper = OpenWakeWordModel.__new__(OpenWakeWordModel)
    pre = SimpleNamespace(raw_data_buffer=deque(maxlen=160000), raw_data_remainder=[])
    wrapper._model = SimpleNamespace(preprocessor=pre, reset=lambda: pre.raw_data_buffer.clear())
    wrapper._cap()
    pre.raw_data_buffer.extend([0] * 80000)
    assert wrapper.buffered_samples() == MODEL_BUFFER_SAMPLES < MAX_BUFFER_SAMPLES
    wrapper.reset()
    pre.raw_data_buffer.extend([0] * 80000)
    assert wrapper.buffered_samples() == MODEL_BUFFER_SAMPLES


def test_missing_model_keeps_head_tap_and_does_not_fall_back(off_robot, tmp_path):
    missing = str(tmp_path / "nope.onnx")
    w = World(off_robot, config=laptop_config(wake_model=missing), loader=OpenWakeWordModel)

    async def go():
        await w.up()
        assert not off_robot.stream_active
        w.bridge.request_listen("touch")
        await until(lambda: "play_listen_sound" in off_robot.action_names())
        await until(lambda: w.state() == State.IDLE and not w.conversation.busy)

    run(go())
    alerts = [e.message for e in w.bridge.log.entries("alert")]
    assert any(missing in a and "robot engine is not used" in a for a in alerts)
    assert "configure_wake_words" not in off_robot.action_names()


# -- operator and visitor notice -------------------------------------------------------

def test_startup_warning_and_status_flag(off_robot):
    w = World(off_robot)
    run(w.up())
    assert any("streams audio to this laptop all the time" in e.message for e in w.bridge.log.entries("alert"))
    assert "wake LAPTOP, audio streams" in w.commands.status_line()


def test_robot_engine_has_no_flag(off_robot):
    bridge = Bridge(make_config(motion={"motors_enabled": False}), off_robot)
    commands = Commands(bridge, "admin", print, None)
    assert "LAPTOP" not in commands.status_line()


@pytest.mark.parametrize("lang,phrase", [("English", "all the time"), ("German", "die ganze Zeit")])
def test_notice_says_audio_streams_all_the_time(off_robot, lang, phrase):
    w = World(off_robot, config=laptop_config(**{}) if lang == "English" else make_config(
        motion={"motors_enabled": False},
        audio={"wake_engine": "laptop", "wake_model": "/m.onnx"}))

    async def go():
        await w.up()
        off_robot.clear_actions()
        await w.commands.execute("notice")

    run(go())
    said = [a.args["text"] for a in off_robot.actions if a.name == "say"]
    assert said and phrase in said[0] and ("discarded" in said[0] or "verworfen" in said[0])


def test_ears_test_shows_scores_per_second_and_the_peak(off_robot):
    w = World(off_robot)

    async def go():
        await w.up()
        await until(lambda: off_robot.stream_active)

        async def speak():
            await asyncio.sleep(0.3)
            w.listener.laptop.feed(LOUD)
            await asyncio.sleep(0.1)
            w.listener.laptop.feed(QUIET)

        speaker = asyncio.ensure_future(speak())
        await w.listener.ears_test(w.lines.append, seconds=2.0)
        await speaker

    run(go())
    scores = [line for line in w.lines if " s  " in line]
    assert len(scores) == 2
    assert any("0.70" in line and "above threshold" in line for line in scores)
    assert w.lines[-1].startswith("ears test finished: highest score 0.70")
    assert w.state() == State.IDLE and "play_listen_sound" not in off_robot.action_names()
