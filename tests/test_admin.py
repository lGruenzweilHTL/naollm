import asyncio
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput

from conftest import make_config
from llm_stub import ScriptedClient
from naollm.admin.cli import Console, CtrlC
from naollm.admin.commands import CommandError, Commands, completions, parse_line, parse_tool_args
from naollm.llm.loop import ConversationLoop
from naollm.state import State

ROOT = Path(__file__).resolve().parents[1]
NAOLLM = [sys.executable, "-m", "naollm.main"]


# -- parser ------------------------------------------------------------------

def test_positional_args(bridge):
    spec = bridge.registry.get("step")
    assert parse_tool_args(spec, ["forward", "20"]) == {"direction": "forward", "cm": 20}


def test_key_value_args(bridge):
    spec = bridge.registry.get("look")
    assert parse_tool_args(spec, ["direction=left"]) == {"direction": "left"}
    step = bridge.registry.get("step")
    assert parse_tool_args(step, ["cm=10", "back"]) == {"cm": 10, "direction": "back"}


def test_bad_integer(bridge):
    with pytest.raises(CommandError, match="whole number"):
        parse_tool_args(bridge.registry.get("turn"), ["left"])


def test_too_many_args(bridge):
    with pytest.raises(CommandError, match="too many"):
        parse_tool_args(bridge.registry.get("look"), ["left", "right"])


def test_quoted_say():
    assert parse_line('say "Hallo Welt"').args == ["Hallo Welt"]


def test_completion(bridge):
    assert "tool" in completions(bridge, [], "to")
    assert "dance" in completions(bridge, ["tool"], "d")
    assert completions(bridge, ["tool", "dance"], "") == ["macarena", "robot", "name="]
    assert completions(bridge, ["posture"], "S") == ["Stand", "Sit"]


# -- commands ----------------------------------------------------------------

class Harness:
    def __init__(self, bridge, mode="admin", conversation=None, answer="y"):
        self.lines = []
        self.answer = answer

        async def confirm(question):
            self.lines.append(question)
            return self.answer

        self.commands = Commands(bridge, mode, self.lines.append, confirm, conversation)

    def run(self, *lines):
        async def go():
            for line in lines:
                await self.commands.execute(line)
        asyncio.run(go())
        return self.lines


def test_admin_call_logged(bridge):
    Harness(bridge).run("tool play_gesture wave")
    entry = bridge.log.entries("tool")[-1]
    assert entry.source == "admin" and "play_gesture" in entry.message


def test_positional_step_runs(make_bridge, robot):
    b = make_bridge(make_config(motion={"walking_enabled": True}))
    Harness(b).run("tool step forward 20")
    move = next(a for a in robot.actions if a.name == "move_to")
    assert move.args["x_m"] == 0.2


def test_look_key_value(bridge, robot):
    Harness(bridge).run("tool look direction=left")
    assert any(a.name == "set_head" and a.args["yaw_rad"] > 0 for a in robot.actions)


def test_bad_enum_shows_choices(bridge, robot):
    lines = Harness(bridge).run("tool dance tango")
    assert any("refused" in l for l in lines)
    assert any("macarena, robot" in l for l in lines)
    assert "run_behavior" not in robot.action_names()


def test_unknown_command(bridge):
    lines = Harness(bridge).run("jump")
    assert lines == ["unknown command 'jump'. Type 'help' for the list."]


def test_walking_toggle(bridge):
    h = Harness(bridge)
    h.run("walk on")
    assert bridge.walking
    assert "walking ON" in h.commands.status_line()
    assert {"turn", "step"} <= {s["function"]["name"] for s in bridge.registry.schemas(bridge.walking)}


def test_resume_declined(bridge):
    bridge.estop("command")
    Harness(bridge, answer="n").run("resume")
    assert bridge.state.state == State.STOPPED


def test_resume_confirmed(bridge):
    bridge.estop("command")
    Harness(bridge, answer="y").run("resume")
    assert bridge.state.state == State.IDLE


def test_admin_mode_has_no_llm(bridge):
    lines = Harness(bridge, mode="admin").run("chat Hallo")
    assert "not available in admin mode" in lines[-1]


def test_chat_runs_turn(bridge, robot):
    loop = ConversationLoop(bridge, ScriptedClient([["Hallo du!"]]))
    Harness(bridge, mode="run", conversation=loop).run("chat --en Hello")
    assert any(a.name == "say" and a.args["text"] == "Hallo du!" for a in robot.actions)
    assert any(a.name == "set_language" and a.args["language"] == "English" for a in robot.actions)


def test_llm_off_blocks_chat(bridge):
    loop = ConversationLoop(bridge, ScriptedClient([["x"]]))
    lines = Harness(bridge, mode="run", conversation=loop).run("llm off", "chat Hallo")
    assert "LLM is off" in lines[-1]


def test_rest_and_wake(bridge, robot):
    Harness(bridge).run("rest")
    assert bridge.state.state == State.RESTING and not robot.stiff
    Harness(bridge).run("wake")
    assert bridge.state.state == State.IDLE and robot.posture == "Stand"


def test_reset_clears_history_and_budget(bridge):
    loop = ConversationLoop(bridge, ScriptedClient([["x"]]))
    loop.history.append(("a", "b"))
    bridge.displacement_cm = 50
    Harness(bridge, mode="run", conversation=loop).run("reset")
    assert loop.history == [] and bridge.displacement_cm == 0


# -- Ctrl+C state machine ------------------------------------------------------

def test_ctrl_c_once_then_twice():
    clock = [100.0]
    stops, out = [], []
    c = CtrlC(stops.append, out.append, clock=lambda: clock[0])
    assert c.press() is False
    assert stops == ["sigint"] and "within 3 s" in out[-1]
    clock[0] += 2.0
    assert c.press() is True


def test_ctrl_c_window_expires():
    clock = [100.0]
    c = CtrlC(lambda s: None, lambda s: None, clock=lambda: clock[0])
    c.press()
    clock[0] += 5.0
    assert c.press() is False


# -- prompt_toolkit key bindings -------------------------------------------------

def make_console(bridge):
    commands = Commands(bridge, "admin", lambda s: None, None)
    return Console(bridge, commands, interactive=True)


def prompt_with(console, keys):
    async def go():
        with create_pipe_input() as inp:
            session = console.make_session(input=inp, output=DummyOutput())
            inp.send_text(keys)
            return await session.prompt_async("> ")
    return asyncio.run(go())


def test_ctrl_e_while_typing(bridge):
    console = make_console(bridge)
    assert prompt_with(console, "tool da\x05\r") == "tool da"
    assert bridge.state.state == State.STOPPED


def test_space_in_command_inserts_space(bridge):
    heard = []
    bridge.listen_handler = heard.append
    assert prompt_with(make_console(bridge), "tool x\r") == "tool x"
    assert heard == []


def test_space_on_empty_line_listens(bridge):
    heard = []
    bridge.listen_handler = heard.append
    assert prompt_with(make_console(bridge), " \r") == ""
    assert heard == ["key"]


# -- process-level: signals, sockets -----------------------------------------------

@pytest.fixture
def cfg_file(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(
        "robot: {url: 'tcp://10.0.0.2:9559'}\n"
        f"animations_dir: '{tmp_path / 'anims'}'\n"
        "dances: {macarena: showcase/dance_macarena}\n"
        "event_mode: false\n"
    )
    return path


class Proc:
    def __init__(self, cfg_file, mode="admin"):
        env = dict(os.environ, PYTHONUNBUFFERED="1")
        self.p = subprocess.Popen(NAOLLM + [mode, "--dry-run", "--config", str(cfg_file)],
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                  env=env, cwd=ROOT)
        self.out = []
        os.set_blocking(self.p.stdout.fileno(), False)

    def read_until(self, text, timeout=15):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            chunk = self.p.stdout.read()
            if chunk:
                self.out.append(chunk.decode())
            if text in "".join(self.out):
                return True
            time.sleep(0.05)
        return False

    def text(self):
        return "".join(self.out)

    def kill(self):
        if self.p.poll() is None:
            self.p.kill()
        self.p.wait(5)


def test_single_ctrl_c_keeps_running(cfg_file):
    proc = Proc(cfg_file)
    try:
        assert proc.read_until("naollm ready")
        proc.p.send_signal(signal.SIGINT)
        assert proc.read_until("Press Ctrl+C again within 3 s to quit")
        assert "ESTOP (source=sigint)" in proc.text()
        time.sleep(0.3)
        assert proc.p.poll() is None
        proc.p.stdin.write(b"status\n")
        proc.p.stdin.flush()
        assert proc.read_until("STOPPED |")
    finally:
        proc.kill()


def test_double_ctrl_c_shuts_down(cfg_file):
    proc = Proc(cfg_file)
    try:
        assert proc.read_until("naollm ready")
        proc.p.send_signal(signal.SIGINT)
        assert proc.read_until("Press Ctrl+C again")
        proc.p.send_signal(signal.SIGINT)
        assert proc.p.wait(10) == 0
        proc.read_until("shutdown complete", timeout=2)
        text = proc.text()
        assert "rest()" in text and "disconnect()" in text and "shutdown complete" in text
    finally:
        proc.kill()


def test_sigterm_estops_then_shuts_down(cfg_file):
    proc = Proc(cfg_file)
    try:
        assert proc.read_until("naollm ready")
        proc.p.send_signal(signal.SIGTERM)
        assert proc.p.wait(10) == 0
        proc.read_until("shutdown complete", timeout=2)
        text = proc.text()
        assert text.index("ESTOP (source=sigterm)") < text.index("shutdown complete")
    finally:
        proc.kill()


def listening_inodes():
    inodes = set()
    for name in ("/proc/net/tcp", "/proc/net/tcp6", "/proc/net/udp", "/proc/net/udp6"):
        try:
            lines = Path(name).read_text().splitlines()[1:]
        except OSError:
            continue
        for line in lines:
            parts = line.split()
            if name.endswith(("tcp", "tcp6")) and parts[3] != "0A":  # LISTEN
                continue
            inodes.add(parts[9])
    return inodes


@pytest.mark.skipif(not Path("/proc/net/tcp").exists(), reason="needs Linux /proc")
def test_no_listening_sockets(cfg_file):
    proc = Proc(cfg_file)
    try:
        assert proc.read_until("naollm ready")
        fd_dir = Path(f"/proc/{proc.p.pid}/fd")
        socket_inodes = set()
        for fd in fd_dir.iterdir():
            try:
                target = os.readlink(fd)
            except OSError:
                continue
            if target.startswith("socket:["):
                socket_inodes.add(target[8:-1])
        assert not (socket_inodes & listening_inodes())
    finally:
        proc.kill()
