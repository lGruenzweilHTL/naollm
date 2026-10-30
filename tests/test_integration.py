"""End-to-end dry-run session: real CLI process, fake robot, stub LLM endpoint."""

import os
import subprocess
import sys
from pathlib import Path

from llm_stub import StubServer

ROOT = Path(__file__).resolve().parents[1]

SCRIPT = """\
chat Hallo NAO
tool play_gesture wave
tool dance macarena
estop
tool play_gesture wave
resume
y
posture Stand
chat --en Can you dance?
quit
"""


def test_scripted_dry_run_session(tmp_path):
    with StubServer([{"role": "assistant", "content": "Hallo! Ich bin NAO."}]) as server:
        cfg = tmp_path / "config.yaml"
        cfg.write_text(
            "robot: {url: 'tcp://10.0.0.2:9559'}\n"
            f"llm: {{base_url: '{server.base_url}', timeout_s: 5}}\n"
            f"animations_dir: '{ROOT / 'animations'}'\n"
            "dances: {macarena: showcase/dance_macarena}\n"
            "event_mode: false\n"
        )
        env = dict(os.environ, PYTHONUNBUFFERED="1")
        proc = subprocess.run([sys.executable, "-m", "naollm.main", "run", "--dry-run", "--config", str(cfg)],
                              input=SCRIPT, capture_output=True, text=True, timeout=120, env=env, cwd=ROOT)
        bodies = server.bodies
    out = proc.stdout
    assert proc.returncode == 0, out + proc.stderr
    assert out.count("[robot] say(text='Hallo!") == 2
    assert out.count("[robot] say(text='Ich bin NAO.") == 2
    assert "play_gesture: ok, finished" in out
    assert "dance: ok, finished" in out
    assert "ESTOP (source=command)" in out
    assert "play_gesture: refused, Ich wurde angehalten" in out
    assert "resumed: IDLE" in out
    assert len(bodies) == 2 and "in English" in bodies[1]["messages"][0]["content"]
    tail = out[out.index("naollm> quit"):]
    order = ["stop_all_behaviors()", "stop_move()", "go_to_posture(name='Crouch')", "rest()", "disconnect()",
             "shutdown complete"]
    positions = [tail.index(s) for s in order]
    assert positions == sorted(positions)
