"""Keeping the Ollama model loaded: native API calls, warm-up, release and console commands."""

import asyncio
import os
import subprocess
import sys
from pathlib import Path

import pytest

from llm_stub import StubServer
from naollm.admin.commands import Commands
from naollm.config import LLMConfig
from naollm.llm.client import OpenAIChatClient
from naollm.llm.residency import ModelResidency, ModelResidencyError, in_daemon_thread, native_root
from naollm.state import State

ROOT = Path(__file__).resolve().parents[1]


def residency(server, model="stub"):
    return ModelResidency(LLMConfig(base_url=server.base_url, model=model))


@pytest.mark.parametrize("url,root", [("http://localhost:11434/v1", "http://localhost:11434"),
                                      ("http://localhost:11434/v1/", "http://localhost:11434"),
                                      ("http://10.0.0.5:8080", "http://10.0.0.5:8080")])
def test_native_root(url, root):
    assert native_root(url) == root


def test_load_unload_status():
    with StubServer([], ollama=True) as server:
        r = residency(server)
        assert r.status().loaded is False
        r.load()
        assert server.native == [{"model": "stub", "keep_alive": -1}]
        assert r.held and r.status().loaded
        r.unload()
        assert server.native[-1] == {"model": "stub", "keep_alive": 0}
        assert not r.held and not r.status().loaded


def test_not_ollama_is_reported():
    with StubServer([]) as server:
        r = residency(server)
        with pytest.raises(ModelResidencyError, match="404.*Ollama"):
            r.load()
        assert not r.held


def test_unreachable_is_reported():
    r = ModelResidency(LLMConfig(base_url="http://127.0.0.1:9/v1"))
    with pytest.raises(ModelResidencyError, match="cannot reach"):
        r.status()


def test_daemon_thread_returns_and_raises():
    async def go():
        assert await in_daemon_thread(lambda: 7) == 7
        with pytest.raises(ValueError):
            await in_daemon_thread(lambda: (_ for _ in ()).throw(ValueError("x")))
    asyncio.run(go())


def chat_once(server, held):
    r = residency(server)
    r.held = held
    client = OpenAIChatClient(LLMConfig(base_url=server.base_url, model="stub", timeout_s=5), r)

    async def go():
        async for _ in client.stream([{"role": "user", "content": "hi"}], None):
            pass
        await client.close()
    asyncio.run(go())
    return server.bodies[-1]


def test_chat_keeps_a_held_model_loaded():
    with StubServer([{"role": "assistant", "content": "Hi."}]) as server:
        assert chat_once(server, held=True)["keep_alive"] == -1
        assert "keep_alive" not in chat_once(server, held=False)


# -- console ---------------------------------------------------------------

def commands_for(bridge, server, conversation=None):
    lines = []
    return Commands(bridge, "run", lines.append, None, conversation, model=residency(server)), lines


def run(commands, *lines):
    async def go():
        for line in lines:
            await commands.execute(line)
    asyncio.run(go())


def test_model_commands(bridge):
    with StubServer([], ollama=True) as server:
        commands, lines = commands_for(bridge, server)
        run(commands, "model status", "model load", "model status", "model unload", "model status")
    assert "not loaded; held by this bridge: no" in lines[0]
    assert "stays loaded" in lines[2]
    assert "loaded, expires" in lines[3] and "held by this bridge: yes" in lines[3]
    assert "unloaded" in lines[4] and "not loaded" in lines[5]
    assert [b["keep_alive"] for b in server.native] == [-1, 0]


def test_unload_refused_during_a_turn(bridge):
    with StubServer([], ollama=True) as server:
        commands, lines = commands_for(bridge, server)
        bridge.state.transition(State.THINKING)
        run(commands, "model unload")
    assert "refused" in lines[-1] and server.native == []


def test_failure_prints_the_reason(bridge):
    with StubServer([]) as server:
        commands, lines = commands_for(bridge, server)
        run(commands, "model load")
    assert "failed" in lines[-1] and "404" in lines[-1]
    assert bridge.state.state == State.IDLE


def test_usage_and_missing_control(bridge):
    lines = []
    commands = Commands(bridge, "admin", lines.append, None)
    run(commands, "model", "model status")
    assert "usage" in lines[0] and "not available" in lines[1]


# -- process level: warm at start, release at exit -------------------------

def session(tmp_path, server, mode, script):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "robot: {url: 'tcp://10.0.0.2:9559'}\n"
        f"llm: {{base_url: '{server.base_url}', model: stub, timeout_s: 5}}\n"
        f"animations_dir: '{tmp_path}/none'\n"
        "event_mode: false\n"
    )
    env = dict(os.environ, PYTHONUNBUFFERED="1")
    return subprocess.run([sys.executable, "-m", "naollm.main", mode, "--dry-run", "--config", str(cfg)],
                          input=script, capture_output=True, text=True, timeout=120, env=env, cwd=ROOT)


def test_run_warms_before_ready_and_releases_at_quit(tmp_path):
    with StubServer([{"role": "assistant", "content": "Hello."}], ollama=True) as server:
        proc = session(tmp_path, server, "run", "chat --en hi\nquit\n")
    out = proc.stdout
    assert proc.returncode == 0, out + proc.stderr
    assert [b["keep_alive"] for b in server.native] == [-1, 0]
    assert out.index("model stub loaded") < out.index("naollm ready")
    assert out.index("shutdown complete") < out.index("model stub unloaded")
    assert server.bodies[0]["keep_alive"] == -1


def test_release_after_an_end_of_input(tmp_path):
    with StubServer([], ollama=True) as server:
        proc = session(tmp_path, server, "run", "")
    assert proc.returncode == 0
    assert [b["keep_alive"] for b in server.native] == [-1, 0]


def test_failed_warm_up_alerts_and_starts(tmp_path):
    with StubServer([{"role": "assistant", "content": "Hello."}]) as server:
        proc = session(tmp_path, server, "run", "chat --en hi\nquit\n")
    out = proc.stdout
    assert proc.returncode == 0, out + proc.stderr
    assert "model warm-up failed" in out and "naollm ready" in out
    assert "unloaded" not in out
    assert "keep_alive" not in server.bodies[0]


def test_unload_command_then_quit_sends_one_unload(tmp_path):
    with StubServer([], ollama=True) as server:
        proc = session(tmp_path, server, "run", "model unload\nquit\n")
    assert proc.returncode == 0
    assert [b["keep_alive"] for b in server.native] == [-1, 0]


def test_admin_loads_nothing_at_start(tmp_path):
    with StubServer([], ollama=True) as server:
        proc = session(tmp_path, server, "admin", "quit\n")
    assert proc.returncode == 0 and server.native == []


def test_admin_load_is_released_at_quit(tmp_path):
    with StubServer([], ollama=True) as server:
        proc = session(tmp_path, server, "admin", "model load\nquit\n")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert [b["keep_alive"] for b in server.native] == [-1, 0]
