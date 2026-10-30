import asyncio
from pathlib import Path

import yaml

from llm_stub import ScriptedClient, call
from naollm.eval import evaluate, load_cases, print_report

ROOT = Path(__file__).resolve().parents[1]
BASE = yaml.safe_load((ROOT / "config.example.yaml").read_text())
PROFILES = ROOT / "tests" / "eval" / "profiles"


def test_case_file_is_valid():
    cases = load_cases(ROOT / "tests" / "eval" / "tool_calls.yaml")
    assert len(cases) == 40
    assert {c.profile for c in cases} == {"event_default", "walking_on"}
    assert {c.lang for c in cases} == {"de", "en"}


def test_runner_reports_per_profile(tmp_path, capsys):
    cases_file = tmp_path / "cases.yaml"
    cases_file.write_text(yaml.safe_dump([
        {"profile": "event_default", "prompt": "Wink mal!", "expect": {"tool": "play_gesture", "args": {"name": "wave"}}},
        {"profile": "event_default", "prompt": "Wie alt bin ich?", "expect": {"no_tool": True}},
        {"profile": "walking_on", "prompt": "Lauf zehn Meter", "expect": {"tool": "step", "rejected": True}},
        {"profile": "walking_on", "prompt": "Dreh dich", "expect": {"tool": "turn"}},
    ]))
    scripts = {
        "event_default": [[call("play_gesture", {"name": "wave"})], ["Das weiß ich nicht."]],
        "walking_on": [[call("step", {"direction": "forward", "cm": 1000})], [call("dance", {"name": "macarena"})]],
    }
    seen_tools = {}

    def factory(cfg):
        profile = "walking_on" if cfg.motion.walking_enabled else "event_default"
        client = ScriptedClient(scripts[profile])
        seen_tools[profile] = client
        return client

    report = asyncio.run(evaluate(load_cases(cases_file), BASE, PROFILES, factory))
    assert report.accuracy("event_default") == 1.0
    assert report.accuracy("walking_on") == 0.5
    assert not report.passed
    names = {t["function"]["name"] for t in seen_tools["event_default"].requests[0]["tools"]}
    assert "step" not in names
    names = {t["function"]["name"] for t in seen_tools["walking_on"].requests[0]["tools"]}
    assert "step" in names
    print_report(report)
    out = capsys.readouterr().out
    assert "event_default: 2/2 correct (100%)" in out and "walking_on: 1/2 correct (50%)" in out
    assert "NOT met" in out
