import asyncio
from pathlib import Path

import yaml

from llm_stub import ScriptedClient, call
from naollm.eval import Progress, evaluate, load_cases, print_report

ROOT = Path(__file__).resolve().parents[1]
BASE = yaml.safe_load((ROOT / "config.example.yaml").read_text())
PROFILES = ROOT / "tests" / "eval" / "profiles"


def test_case_file_is_valid():
    cases = load_cases(ROOT / "tests" / "eval" / "tool_calls.yaml")
    assert len(cases) == 41
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


def _run(tmp_path, cases, scripts, progress=None):
    cases_file = tmp_path / "cases.yaml"
    cases_file.write_text(yaml.safe_dump(cases))

    def factory(cfg):
        return ScriptedClient(scripts["walking_on" if cfg.motion.walking_enabled else "event_default"])

    return asyncio.run(evaluate(load_cases(cases_file), BASE, PROFILES, factory, progress))


def test_ability_questions_expect_no_tool():
    cases = load_cases(ROOT / "tests" / "eval" / "tool_calls.yaml")
    by_prompt = {c.prompt: c.expect for c in cases}
    assert by_prompt["Kannst du tanzen?"] == {"no_tool": True}
    assert by_prompt["Can you dance?"] == {"no_tool": True}
    assert by_prompt["Please dance for us!"] == {"tool": "dance"}
    assert not any(c.expect.get("rejected") is True for c in cases)


def test_any_of_accepts_every_safe_answer(tmp_path):
    over = {"any_of": [{"no_tool": True}, {"tool": "step", "args": {"direction": "forward"}}]}
    cases = [{"profile": "walking_on", "prompt": f"Lauf zehn Meter {i}", "expect": over} for i in range(5)]
    scripts = {"walking_on": [
        ["Ich kann nur kleine Schritte machen."],
        [call("step", {"direction": "forward", "cm": 30})],
        [call("step", {"direction": "forward", "cm": 1000})],
        [call("step", {"direction": "back", "cm": 10})],
        [call("dance", {"name": "macarena"})],
    ]}
    report = _run(tmp_path, cases, scripts)
    assert [o.passed for o in report.outcomes["walking_on"]] == [True, True, True, False, False]


def test_gate_runs_once_per_case(tmp_path, monkeypatch):
    from naollm.tools.gate import Gate

    runs = []
    real = Gate.check_and_run

    def counting(self, *args, **kwargs):
        runs.append(args[0])
        return real(self, *args, **kwargs)

    monkeypatch.setattr(Gate, "check_and_run", counting)
    expect = {"any_of": [{"tool": "step", "rejected": True}, {"tool": "step", "rejected": False}]}
    report = _run(tmp_path, [{"profile": "walking_on", "prompt": "Schritt", "expect": expect}],
                  {"walking_on": [[call("step", {"direction": "right", "cm": 10})]]})
    assert report.outcomes["walking_on"][0].passed
    assert runs == ["step"]


def test_bad_any_of_is_refused(tmp_path):
    from naollm.config import ConfigError

    f = tmp_path / "cases.yaml"
    f.write_text(yaml.safe_dump([{"profile": "event_default", "prompt": "x", "expect": {"any_of": []}}]))
    try:
        load_cases(f)
    except ConfigError as err:
        assert "any_of" in str(err)
    else:
        raise AssertionError("empty any_of was accepted")


def test_progress_prints_each_case_with_reply(tmp_path):
    import io

    out = io.StringIO()
    cases = [
        {"profile": "event_default", "lang": "en", "prompt": "Can you dance?", "expect": {"no_tool": True}},
        {"profile": "event_default", "lang": "en", "prompt": "Wave!", "expect": {"tool": "play_gesture"}},
    ]
    scripts = {"event_default": [["Yes, I can dance! ", "Shall I show you?"], ["Hello!"]]}
    report = _run(tmp_path, cases, scripts, Progress(2, stream=out, live=False))
    lines = out.getvalue().splitlines()
    assert lines[0].startswith("[1/2] PASS event_default en 'Can you dance?' -> no tool")
    assert lines[1] == "    says: Yes, I can dance! Shall I show you?"
    assert lines[2].startswith("[2/2] FAIL event_default en 'Wave!' -> no tool")
    assert lines[3] == "    expected {'tool': 'play_gesture'}"
    assert lines[4] == "    says: Hello!"
    assert "\r" not in out.getvalue()
    assert report.outcomes["event_default"][0].reply == "Yes, I can dance! Shall I show you?"


def test_live_progress_shows_spinner_and_eta(tmp_path):
    import io

    out = io.StringIO()
    gate = asyncio.Event()

    async def release():
        await asyncio.sleep(0.25)
        gate.set()

    cases = [{"profile": "event_default", "prompt": f"Frage {i}", "expect": {"no_tool": True}} for i in range(2)]
    progress = Progress(2, stream=out, live=True)

    async def main():
        task = asyncio.create_task(release())
        cases_file = tmp_path / "cases.yaml"
        cases_file.write_text(yaml.safe_dump(cases))
        report = await evaluate(load_cases(cases_file), BASE, PROFILES,
                                lambda cfg: ScriptedClient([["Hm."], [gate, "Ja."]]), progress)
        await task
        return report

    asyncio.run(main())
    text = out.getvalue()
    assert "\r\x1b[K" in text and "[2/2] event_default de 'Frage 1'" in text
    assert "eta --:--" not in text.split("[2/2] event_default")[1]
    assert "eta 0:00" in text
    assert text.endswith("    says: Ja.\n")
    assert progress.done == 2 and progress.eta() == "0:00"


def test_progress_eta_uses_mean_case_time():
    p = Progress(10, live=False)
    assert p.eta() == "--:--"
    p.done, p.spent = 2, 30.0
    assert p.eta() == "2:00"


def test_interrupt_clears_live_line_and_exits_130(tmp_path, monkeypatch, capsys):
    import io
    from types import SimpleNamespace

    import naollm.eval as ev
    import naollm.llm.client as client_mod

    class Interrupting:
        def __init__(self, cfg):
            pass

        async def stream(self, messages, tools):
            await asyncio.sleep(0.15)
            raise KeyboardInterrupt
            yield  # pragma: no cover

    out = io.StringIO()
    monkeypatch.setattr(client_mod, "OpenAIChatClient", Interrupting)
    monkeypatch.setattr(ev, "Progress", lambda total: Progress(total, stream=out, live=True))
    cases_file = tmp_path / "cases.yaml"
    cases_file.write_text(yaml.safe_dump([{"profile": "event_default", "prompt": "x", "expect": {"no_tool": True}}]))
    args = SimpleNamespace(cases=str(cases_file), profiles=str(PROFILES), verbose=False,
                           config=str(ROOT / "config.example.yaml"))
    assert ev.run_eval_cli(args) == 130
    assert out.getvalue().endswith("\r\x1b[K")
    captured = capsys.readouterr()
    assert "eval: 1 case in 1 profile, model gemma4:e4b" in captured.out
    assert "eval interrupted after 0 of 1 cases" in captured.err
