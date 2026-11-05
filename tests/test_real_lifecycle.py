"""The whole bridge against the strict `qi` double: this is the motors-off guarantee."""

import asyncio
import math
import struct
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from conftest import make_config
from llm_stub import ScriptedClient, call
from naollm.admin.commands import Commands
from naollm.audio.listener import Listener
from naollm.bridge import Bridge, RobotNotAtRest
from naollm.llm.loop import ConversationLoop
from naollm.main import main, run_app
from naollm.robot.adapter import MotorsDisabled
from naollm.robot.qi_adapter import QiRobot
from naollm.state import State
from qi_double import QiDouble

ROOT = Path(__file__).resolve().parents[1]
URL = "tcp://10.0.0.135:9559"
RATE = 16000
CONFIG = dict(
    robot={"url": URL, "language": "English", "battery_reliable": False},
    stt={"languages": ["en"], "fallback_language": "en"},
    audio={"wake_language": "English", "wake_words": ["Hey NAO"], "wake_threshold": 0.4,
           "vad_silence_s": 0.2, "no_speech_timeout_s": 0.3, "max_utterance_s": 2.0},
    motion={"motors_enabled": False},
)
CHUNK = 1365 * 2


def tone(seconds):
    n = int(RATE * seconds)
    return struct.pack(f"<{n}h", *(int(9000 * math.sin(2 * math.pi * 220 * i / RATE)) for i in range(n)))


class STT:
    def ensure_loaded(self):
        pass

    def transcribe(self, pcm):
        return SimpleNamespace(text="Can you dance?", language="en")


def send(double, audio):
    for i in range(0, len(audio), CHUNK):
        double.send_audio(audio[i:i + CHUNK])


async def until(predicate, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return
        await asyncio.sleep(0.005)
    raise AssertionError("condition not reached")


def build(double, script):
    robot = QiRobot(qi_module=double)
    bridge = Bridge(make_config(**CONFIG), robot)
    client = ScriptedClient(script)
    conversation = ConversationLoop(bridge, client)
    listener = Listener(bridge, conversation, STT(), speech_test=lambda frame: any(frame))
    listener.wake_resume_s = 0.02
    lines = []

    async def confirm(question):
        return "y"

    commands = Commands(bridge, "run", lines.append, confirm, conversation, listener=listener)
    return SimpleNamespace(robot=robot, bridge=bridge, client=client, listener=listener, commands=commands, lines=lines,
                           conversation=conversation)


def test_full_lifecycle_commands_no_motor():
    double = QiDouble()
    world = build(double, [
        ["I can't dance right now."],
        [call("set_eyes", {"color": "purple"}), call("dance", {"name": "macarena"})],
        ["Done."],
    ])
    b = world.bridge

    async def go():
        b.startup(greet=False)
        await world.listener.prepare()
        b.greet()
        assert b.state.state == State.IDLE
        assert double.tts_language == "English"
        await until(lambda: double.called("ALSpeechRecognition", "subscribe"))

        # a voice turn from a head tap
        double.fire("FrontTactilTouched", 1.0)
        double.fire("FrontTactilTouched", 0.0)
        await until(lambda: double.registered)
        send(double, tone(0.8) + b"\0" * RATE)
        await until(lambda: world.client.requests and b.state.state == State.IDLE and not world.conversation.busy)
        assert double.registered == {}
        assert double.called("ALTextToSpeech", "say")[-1] == ("I can't dance right now.",)
        offered = {t["function"]["name"] for t in world.client.requests[0]["tools"]}
        assert offered == {"set_eyes", "get_status", "who_is_here"}

        # a typed turn in which the model still tries to move
        await world.commands.execute("chat dance please")

        # a voice turn from the wake word
        await until(lambda: not world.conversation.busy)
        await asyncio.sleep(0.05)
        double.fire("WordRecognized", ["<...> Hey NAO <...>", 0.44])
        await until(lambda: b.state.state == State.LISTENING)
        await until(lambda: double.registered)
        await until(lambda: b.state.state == State.IDLE and not world.conversation.busy)  # nobody spoke

        # every console motion command, and read-only ones
        for line in ("wake", "rest", "posture Stand", "walk on", "anim play victory", "tool dance macarena",
                     "tool play_gesture wave", "tool set_posture Stand", "tool look left", "tool turn 10",
                     "tool step forward 10", "tool stop", "tool who_is_here", "tool get_status", "tool set_eyes cyan",
                     "status", "tools", "behaviors"):
            await world.commands.execute(line)
        assert not any(line.startswith("error") for line in world.lines), world.lines

        # emergency stop from the head, and from the middle sensor while listening
        double.fire("RearTactilTouched", 1.0)
        assert b.state.state == State.STOPPED
        assert double.called("ALLeds", "fadeRGB")[-1] == ("FaceLeds", 0xFF0000, 0.2)
        await world.commands.execute("resume")
        assert b.state.state == State.IDLE

        double.fire("FrontTactilTouched", 0.0)
        double.fire("FrontTactilTouched", 1.0)
        await until(lambda: double.registered)
        double.fire("MiddleTactilTouched", 1.0)
        await until(lambda: not double.registered)
        assert b.state.state == State.STOPPED
        await world.commands.execute("resume")

        # the network drops and comes back
        double.drop_connection()
        await until(lambda: b.state.state == State.OFFLINE)
        await world.commands.execute("reconnect")
        assert b.state.state == State.OFFLINE
        double.fire("RearTactilTouched", 0.0)
        await world.commands.execute("resume")
        assert b.state.state == State.IDLE

        # the poll sees a wake-up, without answering it with a motor command
        double.awake = True
        b.rest_tick()
        assert b.state.state == State.STOPPED
        double.awake = False

        await world.commands.execute("quit")
        b.shutdown()

    asyncio.run(go())
    assert double.violations == []
    assert not double.is_open
    assert b.state.state == State.OFFLINE
    assert not any(line.startswith("error") for line in world.lines), world.lines
    assert b.log.entries("error") == []
    assert sum("refused, motors are off" in line for line in world.lines) >= 5
    assert any("who_is_here: ok" in line for line in world.lines)
    assert double.called("ALFaceDetection", "setTrackingEnabled")[0] == (False,)


def test_missing_face_service_keeps_the_bridge_running():
    double = QiDouble()
    double.fail[("ALFaceDetection", "setTrackingEnabled")] = RuntimeError("service not found")
    world = build(double, [])
    b = world.bridge
    b.startup(greet=False)
    assert b.state.state == State.IDLE
    assert any("PERCEPTION UNAVAILABLE" in e.message for e in b.log.entries("alert"))
    assert "who_is_here" not in {t["function"]["name"] for t in b.registry.schemas(False)}
    assert b.gate.check_and_run("who_is_here", {}, source="admin").reason == "perception_off"
    b.shutdown()


def test_stiff_robot_refuses_and_commands_nothing():
    double = QiDouble()
    double.stiffness = [0.0, 0.3]
    world = build(double, [])
    with pytest.raises(RobotNotAtRest, match="stiff"):
        world.bridge.startup()
    assert not double.is_open
    robot_commands = [c for c in double.calls if c[0] in ("ALTextToSpeech", "ALLeds", "ALSpeechRecognition")]
    assert robot_commands == []
    assert double.violations == []


def test_life_active_refuses_and_is_not_changed():
    double = QiDouble()
    double.life = "solitary"
    world = build(double, [])
    with pytest.raises(RobotNotAtRest, match="naollm init"):
        world.bridge.startup()
    assert double.called("ALAutonomousLife", "setState") == []
    assert double.life == "solitary"


def test_direct_motion_call_raises():
    double = QiDouble()
    robot = QiRobot(qi_module=double)
    robot.connect(URL)
    before = len(double.calls)
    with pytest.raises(MotorsDisabled):
        robot.motion.go_to_posture("Stand")
    assert len(double.calls) == before


# -- main ------------------------------------------------------------------

def write_config(tmp_path, motors="false"):
    path = tmp_path / "c.yaml"
    path.write_text(f"robot: {{url: '{URL}', language: English}}\n"
                    f"motion: {{motors_enabled: {motors}}}\n"
                    f"stt: {{languages: [en], fallback_language: en}}\n"
                    f"audio: {{wake_language: English, wake_words: ['Hey NAO']}}\n"
                    f"animations_dir: '{tmp_path}/none'\n"
                    f"robot_profile_dir: '{tmp_path}/robots'\n"
                    "event_mode: false\n")
    return path


def test_real_run_with_motors_enabled_exits_before_connecting(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr("naollm.robot.qi_adapter.QiRobot.connect",
                        lambda *a: pytest.fail("connected with motors enabled"))
    assert main(["run", "--config", str(write_config(tmp_path, "true"))]) == 2
    assert "real motion is not implemented yet" in capsys.readouterr().err


def test_missing_qi_names_the_extra(tmp_path, capsys, monkeypatch):
    monkeypatch.setitem(sys.modules, "qi", None)
    assert main(["admin", "--config", str(write_config(tmp_path))]) == 2
    assert "naollm[robot]" in capsys.readouterr().err


def test_unreachable_robot_exits_non_zero(tmp_path, capsys):
    double = QiDouble()
    double.connect_error = "no route to host"
    from naollm.config import load_config
    config = load_config(write_config(tmp_path))
    code = asyncio.run(run_app(config, "admin", False, robot=QiRobot(qi_module=double)))
    assert code == 2
    assert "no route to host" in capsys.readouterr().err


def test_stiff_robot_through_main(tmp_path, capsys):
    double = QiDouble()
    double.stiffness = [1.0]
    from naollm.config import load_config
    config = load_config(write_config(tmp_path))
    code = asyncio.run(run_app(config, "admin", False, robot=QiRobot(qi_module=double)))
    assert code == 2
    err = capsys.readouterr().err
    assert "awake or stiff" in err
    assert not double.is_open and double.violations == []


def test_admin_session_through_main(tmp_path, capfd, monkeypatch):
    """Real wiring, piped console: starts, reads, refuses a motion command and quits."""
    from naollm.config import load_config

    double = QiDouble()
    read, write = __import__("os").pipe()
    __import__("os").write(write, b"status\nrest\ntool dance macarena\nquit\n")
    __import__("os").close(write)
    monkeypatch.setattr(sys, "stdin", __import__("os").fdopen(read, "r"))
    config = load_config(write_config(tmp_path))
    code = asyncio.run(run_app(config, "admin", False, interactive=False, robot=QiRobot(qi_module=double)))
    out = capfd.readouterr().out
    assert code == 0, out
    assert "motors OFF" in out and "rest: refused, motors are off" in out and "shutdown complete" in out
    assert double.violations == []
    assert not double.is_open
    assert double.called("ALSpeechRecognition", "subscribe") == []  # admin mode does not listen
