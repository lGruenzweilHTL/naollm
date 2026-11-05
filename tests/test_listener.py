"""The voice turn against the fake robot, with stub speech to text and a simple speech test."""

import asyncio
import builtins
import math
import struct
import threading
import time
import wave
from types import SimpleNamespace

import pytest

from conftest import make_config
from llm_stub import ScriptedClient
from naollm.admin.commands import Commands
from naollm.audio.listener import Listener
from naollm.audio.stt import SpeechToText
from naollm.audio.vad import VoiceUnavailable
from naollm.bridge import Bridge
from naollm.config import STTConfig
from naollm.llm.loop import ConversationLoop
from naollm.robot.adapter import EventKind
from naollm.state import State

RATE = 16000
OFF = {"motors_enabled": False}
AUDIO = {"vad_silence_s": 0.3, "no_speech_timeout_s": 0.3, "max_utterance_s": 1.5}
EN = {"url": "tcp://10.0.0.2:9559", "language": "English"}


def tone(seconds):
    n = int(RATE * seconds)
    return struct.pack(f"<{n}h", *(int(9000 * math.sin(2 * math.pi * 220 * i / RATE)) for i in range(n)))


def energy_test(frame):
    return any(frame)


class StubSTT:
    def __init__(self, text="Hello robot", language="en", error=None, gate=None):
        self.text, self.language, self.error, self.gate = text, language, error, gate
        self.seen = []

    def ensure_loaded(self):
        pass

    def transcribe(self, pcm):
        self.seen.append(len(pcm))
        if self.gate:
            self.gate.wait(5)
        if self.error:
            raise self.error
        return SimpleNamespace(text=self.text, language=self.language)


class World:
    """A bridge, conversation loop and listener on one event loop."""

    def __init__(self, off_robot, stt=None, config=None, script=None, admin=False):
        self.robot = off_robot
        self.config = config or make_config(motion=OFF, audio=AUDIO, robot=EN,
                                            stt={"languages": ["en"], "fallback_language": "en"})
        self.bridge = Bridge(self.config, off_robot)
        self.client = ScriptedClient(script if script is not None else [["Hello there."]])
        self.conversation = None if admin else ConversationLoop(self.bridge, self.client)
        self.stt = None if admin else (stt or StubSTT())
        self.listener = Listener(self.bridge, self.conversation, self.stt, speech_test=energy_test)
        self.listener.wake_resume_s = 0.02
        self.lines = []

        async def confirm(question):
            return "y"

        self.commands = Commands(self.bridge, "admin" if admin else "run", self.lines.append, confirm,
                                 self.conversation, listener=self.listener)

    async def up(self):
        self.bridge.startup(greet=False)
        await self.listener.prepare()
        self.robot.clear_actions()

    def state(self):
        return self.bridge.state.state

    def names(self):
        return self.robot.action_names()


def world_run(off_robot, scenario, **kwargs):
    world = World(off_robot, **kwargs)

    async def go():
        await world.up()
        await scenario(world)
        await asyncio.sleep(0.05)

    asyncio.run(go())
    return world


async def until(predicate, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        await asyncio.sleep(0.005)
    raise AssertionError("condition not reached")


def speak_into(robot, seconds=0.7):
    robot.stream_audio = tone(seconds)


# -- triggers --------------------------------------------------------------

@pytest.mark.parametrize("fire", [
    lambda r: r.inject(EventKind.FRONT_HEAD),
    lambda r: r.hear_wake_phrase("Hey NAO", 0.9),
])
def test_robot_triggers_start_listening(off_robot, fire):
    async def scenario(w):
        w.config.audio.wake_threshold = 0.4
        off_robot.wake_threshold = 0.4
        await until(lambda: off_robot.wake_enabled)
        speak_into(off_robot)
        fire(off_robot)
        await until(lambda: w.state() == State.LISTENING)
        await until(lambda: w.state() == State.IDLE and not w.conversation.busy)
    w = world_run(off_robot, scenario)
    assert w.stt.seen and w.client.requests


def test_operator_key_starts_listening(off_robot):
    async def scenario(w):
        speak_into(off_robot)
        w.bridge.request_listen("key")
        await until(lambda: w.state() == State.LISTENING)
        await until(lambda: w.state() == State.IDLE and not w.conversation.busy)
    assert world_run(off_robot, scenario).client.requests


def test_trigger_while_thinking_is_logged_and_ignored(off_robot):
    async def scenario(w):
        w.bridge.state.transition(State.THINKING)
        assert w.listener.start("wake_word") is None
        assert w.state() == State.THINKING
        assert any("ignored" in e.message for e in w.bridge.log.entries("listen"))
        w.bridge.state.transition(State.IDLE)
    world_run(off_robot, scenario)


def test_trigger_while_stopped_does_nothing(off_robot):
    async def scenario(w):
        w.bridge.estop("command")
        off_robot.clear_actions()
        off_robot.inject(EventKind.FRONT_HEAD)
        await asyncio.sleep(0.03)
        assert w.state() == State.STOPPED
        assert "start_stream" not in w.names()
    world_run(off_robot, scenario)


def test_head_tap_switch_off(off_robot):
    cfg = make_config(motion=OFF, audio={**AUDIO, "front_head_tap": False})
    async def scenario(w):
        off_robot.inject(EventKind.FRONT_HEAD)
        await asyncio.sleep(0.03)
        assert w.state() == State.IDLE
    world_run(off_robot, scenario, config=cfg)


def test_llm_off_ignores_triggers(off_robot):
    async def scenario(w):
        w.bridge.llm_enabled = False
        assert w.listener.start("key") is None
        assert w.state() == State.IDLE
    world_run(off_robot, scenario)


# -- flow ------------------------------------------------------------------

def test_normal_turn_order_and_colours(off_robot):
    async def scenario(w):
        speak_into(off_robot)
        await w.listener.start("touch")
    w = world_run(off_robot, scenario)
    names = w.names()
    assert names.index("play_listen_sound") < names.index("start_stream") < names.index("stop_stream") < names.index("say")
    colours = [a.args["rgb"] for a in off_robot.actions if a.name == "set_eyes"]
    assert colours[:3] == ["#0000FF", "#FFC000", "#FFFFFF"]
    assert w.client.requests[0]["messages"][-1] == {"role": "user", "content": "Hello robot"}
    assert any(a.name == "say" and a.args["text"] == "Hello there." for a in off_robot.actions)
    assert w.state() == State.IDLE


def test_sound_failure_is_logged_and_listening_continues(off_robot):
    off_robot.fail_on["play_listen_sound"] = RuntimeError("no player")
    async def scenario(w):
        speak_into(off_robot)
        await w.listener.start("touch")
    w = world_run(off_robot, scenario)
    assert any("sound" in e.message for e in w.bridge.log.entries("error"))
    assert w.client.requests


def test_failed_stream_returns_to_idle(off_robot):
    off_robot.fail_on["start_stream"] = RuntimeError("busy")
    async def scenario(w):
        await w.listener.start("touch")
    w = world_run(off_robot, scenario)
    assert w.state() == State.IDLE and not w.client.requests
    assert any("audio stream failed" in e.message for e in w.bridge.log.entries("alert"))


def test_nobody_speaks_returns_to_idle(off_robot):
    async def scenario(w):
        await w.listener.start("touch")
    w = world_run(off_robot, scenario)
    assert w.state() == State.IDLE and not w.client.requests and not w.stt.seen
    assert "stop_stream" in w.names()


def test_too_short_is_ignored(off_robot):
    async def scenario(w):
        speak_into(off_robot, 0.2)
        await w.listener.start("touch")
    w = world_run(off_robot, scenario)
    assert w.state() == State.IDLE and not w.stt.seen and not w.client.requests


def test_empty_transcript_is_ignored(off_robot):
    async def scenario(w):
        speak_into(off_robot)
        await w.listener.start("touch")
    w = world_run(off_robot, scenario, stt=StubSTT(text=""))
    assert w.state() == State.IDLE and w.stt.seen and not w.client.requests


def test_stt_failure_returns_to_idle(off_robot):
    async def scenario(w):
        speak_into(off_robot)
        await w.listener.start("touch")
    w = world_run(off_robot, scenario, stt=StubSTT(error=RuntimeError("model gone")))
    assert w.state() == State.IDLE and not w.client.requests
    assert any("model gone" in e.message for e in w.bridge.log.entries("error"))


def test_silent_stream_is_given_up(off_robot, monkeypatch):
    monkeypatch.setattr("naollm.audio.listener.WATCHDOG_MARGIN_S", 0.0)
    off_robot.start_stream = lambda on_chunk: None  # never delivers
    async def scenario(w):
        await w.listener.start("touch")
    w = world_run(off_robot, scenario)
    assert w.state() == State.IDLE
    assert any("no audio" in e.message for e in w.bridge.log.entries("alert"))


def test_transcription_does_not_block_the_event_loop(off_robot):
    gate = threading.Event()
    async def scenario(w):
        speak_into(off_robot)
        task = w.listener.start("touch")
        await until(lambda: w.state() == State.THINKING and w.stt.seen)
        ticks = 0
        for _ in range(5):
            await asyncio.sleep(0.001)
            ticks += 1
        assert ticks == 5
        gate.set()
        await task
    world_run(off_robot, scenario, stt=StubSTT(gate=gate))


def test_turn_language_comes_from_the_transcript(off_robot):
    config = make_config(motion=OFF, audio=AUDIO)  # de, en
    async def scenario(w):
        speak_into(off_robot)
        await w.listener.start("touch")
    w = world_run(off_robot, scenario, config=config, stt=StubSTT(language="en"))
    assert "in English" in w.client.requests[0]["messages"][0]["content"]


# -- wake word rules -------------------------------------------------------

def enabled_values(robot):
    return [a.args["enabled"] for a in robot.actions if a.name == "set_wake_enabled"]


def test_paused_during_a_turn_and_resumed_after(off_robot):
    off_robot.time_scale = 0.1
    async def scenario(w):
        assert off_robot.wake_enabled
        speak_into(off_robot)
        task = w.listener.start("touch")
        await until(lambda: off_robot.stream_active)
        assert not off_robot.wake_enabled
        await task
        await until(lambda: off_robot.wake_enabled)
    world_run(off_robot, scenario)
    assert enabled_values(off_robot) == [False, True]


def test_resume_waits_for_the_delay(off_robot):
    async def scenario(w):
        w.listener.wake_resume_s = 0.2
        w.bridge.state.transition(State.THINKING)
        await until(lambda: not off_robot.wake_enabled)
        w.bridge.state.transition(State.IDLE)
        await asyncio.sleep(0.05)
        assert not off_robot.wake_enabled
        await until(lambda: off_robot.wake_enabled)
    world_run(off_robot, scenario)


def test_stop_during_the_delay_keeps_it_paused(off_robot):
    async def scenario(w):
        w.listener.wake_resume_s = 0.1
        w.bridge.state.transition(State.THINKING)
        await until(lambda: not off_robot.wake_enabled)
        w.bridge.state.transition(State.IDLE)
        w.bridge.estop("command")
        await asyncio.sleep(0.2)
        assert not off_robot.wake_enabled
    world_run(off_robot, scenario)


def test_stays_paused_when_stopped_and_after_ears_off(off_robot):
    async def scenario(w):
        w.bridge.estop("command")
        await until(lambda: not off_robot.wake_enabled)
        w.bridge.resume()
        await until(lambda: off_robot.wake_enabled)
        await w.commands.execute("ears off")
        assert not off_robot.wake_enabled
        w.bridge.state.transition(State.THINKING)
        w.bridge.state.transition(State.IDLE)
        await asyncio.sleep(0.05)
        assert not off_robot.wake_enabled
    world_run(off_robot, scenario)


def test_failed_wake_setup_keeps_tap_working(off_robot):
    off_robot.asr_languages = ["English"]
    config = make_config(motion=OFF, audio=AUDIO)  # German wake language
    async def scenario(w):
        assert any("wake word unavailable" in e.message for e in w.bridge.log.entries("alert"))
        speak_into(off_robot)
        off_robot.inject(EventKind.FRONT_HEAD)
        await until(lambda: w.state() == State.LISTENING)
        await until(lambda: w.state() == State.IDLE and not w.conversation.busy)
    world_run(off_robot, scenario, config=config)


# -- emergency stop and disconnect -----------------------------------------

def test_stop_during_listening(off_robot):
    off_robot.time_scale = 1.0  # real time, so the stream is still running at the stop
    async def scenario(w):
        speak_into(off_robot, 1.0)
        task = w.listener.start("touch")
        await until(lambda: off_robot.stream_active)
        await asyncio.sleep(0.03)
        off_robot.inject(EventKind.REAR_HEAD)
        with pytest.raises(asyncio.CancelledError):
            await task
        assert w.state() == State.STOPPED
        assert not off_robot.stream_active
        assert w.listener._capture is None
    w = world_run(off_robot, scenario)
    assert not w.client.requests and not w.stt.seen


def test_stop_during_transcription_makes_no_llm_call(off_robot):
    gate = threading.Event()
    async def scenario(w):
        speak_into(off_robot)
        task = w.listener.start("touch")
        await until(lambda: w.stt.seen)
        w.bridge.estop("command")
        with pytest.raises(asyncio.CancelledError):
            await task
        gate.set()
        assert w.state() == State.STOPPED
    w = world_run(off_robot, scenario, stt=StubSTT(gate=gate))
    assert not w.client.requests


def test_connection_loss_during_listening(off_robot):
    off_robot.time_scale = 1.0
    async def scenario(w):
        task = w.listener.start("touch")
        await until(lambda: off_robot.stream_active)
        off_robot.inject(EventKind.CONNECTION_LOST)
        with pytest.raises(asyncio.CancelledError):
            await task
        assert w.state() == State.OFFLINE and not off_robot.stream_active
    world_run(off_robot, scenario)


def test_stop_during_start_of_stream_still_stops_it(off_robot):
    release = threading.Event()
    real_start = off_robot.start_stream

    def slow_start(on_chunk):
        release.wait(5)
        real_start(on_chunk)

    off_robot.start_stream = slow_start

    async def scenario(w):
        task = w.listener.start("touch")
        await asyncio.sleep(0.03)
        w.bridge.estop("command")
        await asyncio.sleep(0.01)
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not off_robot.stream_active
    world_run(off_robot, scenario)


# -- privacy ---------------------------------------------------------------

def test_idle_bridge_has_no_stream(off_robot):
    async def scenario(w):
        assert not off_robot.stream_active
        assert "start_stream" not in w.names()
    world_run(off_robot, scenario)


def test_no_audio_is_written_and_no_buffer_stays(off_robot, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    real_open = builtins.open
    written = []

    def spy_open(file, mode="r", *args, **kwargs):
        if any(c in mode for c in "wax+"):
            written.append(str(file))
        return real_open(file, mode, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", spy_open)
    monkeypatch.setattr(wave, "open", lambda *a, **k: pytest.fail("wave.open during a turn"))

    captures = []
    async def scenario(w):
        speak_into(off_robot)
        task = w.listener.start("touch")
        await until(lambda: w.listener._capture is not None)
        captures.append(w.listener._capture)
        await task
    w = world_run(off_robot, scenario)
    assert w.stt.seen and w.stt.seen[0] > 0
    assert w.listener._capture is None
    assert captures[0].detector.audio == b""
    assert written == [] and list(tmp_path.iterdir()) == []


def test_event_mode_log_has_no_transcript(off_robot):
    config = make_config(motion=OFF, audio=AUDIO, event_mode=True)
    async def scenario(w):
        speak_into(off_robot)
        await w.listener.start("touch")
    w = world_run(off_robot, scenario, config=config, stt=StubSTT(text="my secret words"))
    assert not any("secret" in e.render() for e in w.bridge.log.entries())


# -- optional at startup ---------------------------------------------------

def test_missing_extra_alert_and_chat_works(off_robot, monkeypatch):
    def missing():
        raise VoiceUnavailable("listening needs faster_whisper. Install the voice extra: pip install 'naollm[voice]'")
    monkeypatch.setattr("naollm.audio.listener.check_voice_extra", missing)

    async def go():
        config = make_config(motion=OFF, audio=AUDIO)
        bridge = Bridge(config, off_robot)
        client = ScriptedClient([["Hallo."]])
        conversation = ConversationLoop(bridge, client)
        listener = Listener(bridge, conversation, SpeechToText(STTConfig()))
        lines = []
        commands = Commands(bridge, "run", lines.append, None, conversation, listener=listener)
        bridge.startup(greet=False)
        await listener.prepare()
        alerts = [e.message for e in bridge.log.entries("alert")]
        assert any("naollm[voice]" in a for a in alerts)
        assert listener.start("touch") is None
        off_robot.inject(EventKind.FRONT_HEAD)
        await asyncio.sleep(0.02)
        assert bridge.state.state == State.IDLE
        await commands.execute("chat hallo")
        assert client.requests
    asyncio.run(go())


def test_model_load_failure_alerts(off_robot, monkeypatch):
    monkeypatch.setattr("naollm.audio.listener.check_voice_extra", lambda: None)
    monkeypatch.setattr("naollm.audio.listener.webrtc_speech_test", lambda: energy_test)

    class Broken(SpeechToText):
        def ensure_loaded(self):
            raise VoiceUnavailable("cannot load the speech model small: no disk")

    async def go():
        bridge = Bridge(make_config(motion=OFF), off_robot)
        listener = Listener(bridge, ConversationLoop(bridge, ScriptedClient([])), Broken(STTConfig()))
        bridge.startup(greet=False)
        await listener.prepare()
        assert not listener.available
        assert any("no disk" in e.message for e in bridge.log.entries("alert"))
    asyncio.run(go())


def test_admin_mode_loads_nothing_and_never_listens(off_robot):
    async def scenario(w):
        assert not w.listener.available
        assert "configure_wake_words" not in w.names()
        off_robot.inject(EventKind.FRONT_HEAD)
        await asyncio.sleep(0.02)
        assert w.state() == State.IDLE
        assert "start_stream" not in w.names()
        await w.commands.execute("listen")
        assert "start_stream" not in w.names()
        await w.commands.execute("ears on")
        assert "not available" in w.lines[-1]
    world_run(off_robot, scenario, admin=True)


# -- console ---------------------------------------------------------------

def test_ears_off_then_on(off_robot):
    async def scenario(w):
        await w.commands.execute("ears off")
        assert w.lines[-1] == "ears OFF"
        off_robot.wake_enabled = True  # an event already on its way
        off_robot.hear_wake_phrase("Hey NAO", 0.9)
        await asyncio.sleep(0.02)
        assert w.state() == State.IDLE
        speak_into(off_robot)
        off_robot.inject(EventKind.FRONT_HEAD)
        await until(lambda: w.state() == State.LISTENING)
        await until(lambda: w.state() == State.IDLE and not w.conversation.busy)
        off_robot.wake_enabled = False
        await w.commands.execute("ears on")
        assert w.lines[-1] == "ears ON"
        await until(lambda: off_robot.wake_enabled)
    world_run(off_robot, scenario)


def test_ears_test_shows_phrases_and_starts_no_turn(off_robot):
    async def scenario(w):
        off_robot.wake_threshold = 0.4
        task = asyncio.ensure_future(w.listener.ears_test(w.lines.append, seconds=0.2))
        await until(lambda: w.listener._testing and off_robot.wake_enabled)
        off_robot.hear_wake_phrase("Hey NAO", 0.44)
        await asyncio.sleep(0.02)
        assert w.state() == State.IDLE
        await task
    w = world_run(off_robot, scenario)
    assert any("Hey NAO  0.44" in line for line in w.lines)
    assert "start_stream" not in w.names()
    assert any("finished" in line for line in w.lines)


def test_ears_test_restores_ears_off(off_robot):
    async def scenario(w):
        await w.commands.execute("ears off")
        task = asyncio.ensure_future(w.listener.ears_test(w.lines.append, seconds=0.1))
        await until(lambda: off_robot.wake_enabled)
        await task
        await until(lambda: not off_robot.wake_enabled)
        assert w.listener.ears_enabled is False
    world_run(off_robot, scenario)


def test_stop_during_ears_test(off_robot):
    async def scenario(w):
        task = asyncio.ensure_future(w.listener.ears_test(w.lines.append, seconds=5))
        await until(lambda: w.listener._testing and off_robot.wake_enabled)
        w.bridge.estop("command")
        await asyncio.wait_for(task, 1)
        await asyncio.sleep(0.05)
        assert not off_robot.wake_enabled
    w = world_run(off_robot, scenario)
    assert any("ended early" in line for line in w.lines)


def test_ears_test_works_in_admin_mode(off_robot):
    async def scenario(w):
        task = asyncio.ensure_future(w.listener.ears_test(w.lines.append, seconds=0.1))
        await until(lambda: off_robot.wake_enabled)
        await task
        await until(lambda: not off_robot.wake_enabled)
    world_run(off_robot, scenario, admin=True)


def write_wav(path, pcm, rate=RATE, channels=1, width=2):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(width)
        w.setframerate(rate)
        w.writeframes(pcm)


def test_listen_wav_runs_the_normal_flow(off_robot, tmp_path):
    clip = tmp_path / "clip.wav"
    write_wav(clip, tone(0.7))
    async def scenario(w):
        await w.commands.execute(f"listen --wav {clip}")
    w = world_run(off_robot, scenario)
    assert w.stt.seen and w.client.requests
    assert "start_stream" in w.names() and w.state() == State.IDLE


@pytest.mark.parametrize("kwargs", [{"rate": 44100}, {"channels": 2}, {"width": 1}])
def test_listen_wav_wrong_format(off_robot, tmp_path, kwargs):
    clip = tmp_path / "bad.wav"
    write_wav(clip, b"\0\0" * 4000, **kwargs)
    async def scenario(w):
        await w.commands.execute(f"listen --wav {clip}")
        assert "error" in w.lines[-1] and "16 kHz" in w.lines[-1]
        assert w.state() == State.IDLE
    w = world_run(off_robot, scenario)
    assert "start_stream" not in w.names()


def test_listen_wav_missing_file(off_robot, tmp_path):
    async def scenario(w):
        await w.commands.execute(f"listen --wav {tmp_path}/none.wav")
        assert "cannot read" in w.lines[-1]
    world_run(off_robot, scenario)


def test_real_adapter_rejects_wav(off_robot):
    async def scenario(w):
        w.robot.audio = SimpleNamespace(set_wake_enabled=lambda e: None)
        await w.commands.execute("listen --wav x.wav")
        assert "dry run" in w.lines[-1]
    world_run(off_robot, scenario)


def test_reconnect_after_a_network_drop(off_robot):
    async def scenario(w):
        off_robot.inject(EventKind.CONNECTION_LOST)
        assert w.state() == State.OFFLINE
        off_robot.clear_actions()
        await w.commands.execute("reconnect")
        assert w.state() == State.OFFLINE
        assert "resume" in w.lines[-1]
        assert {"disconnect", "connect", "configure_wake_words"} <= set(w.names())
        assert any("restored" in e.message for e in w.bridge.log.entries("alert"))
        speak_into(off_robot)
        off_robot.inject(EventKind.FRONT_HEAD)  # events arrive, but it stays blocked
        await asyncio.sleep(0.02)
        assert "start_stream" not in w.names()
        await w.commands.execute("resume")
        assert w.state() == State.IDLE
        await until(lambda: off_robot.wake_enabled)
    world_run(off_robot, scenario)


def test_reconnect_with_the_robot_awake(off_robot):
    async def scenario(w):
        off_robot.inject(EventKind.CONNECTION_LOST)
        off_robot.stiff = True
        await w.commands.execute("reconnect")
        assert "failed" in w.lines[-1] and "awake" in w.lines[-1]
        assert w.state() == State.OFFLINE and not off_robot.connected
    world_run(off_robot, scenario)


def test_reconnect_failure_is_reported(off_robot):
    async def scenario(w):
        off_robot.inject(EventKind.CONNECTION_LOST)
        off_robot.fail_on["connect"] = RuntimeError("no route")
        await w.commands.execute("reconnect")
        assert "no route" in w.lines[-1]
        assert w.state() == State.OFFLINE
    world_run(off_robot, scenario)


def test_resume_refused_while_disconnected(off_robot):
    async def scenario(w):
        off_robot.inject(EventKind.CONNECTION_LOST)
        await w.commands.execute("resume")
        assert "reconnect" in w.lines[-1]
        assert w.state() == State.OFFLINE
    world_run(off_robot, scenario)
