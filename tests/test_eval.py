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
        def __init__(self, cfg, residency=None):
            pass

        async def stream(self, messages, tools):
            await asyncio.sleep(0.15)
            raise KeyboardInterrupt
            yield  # pragma: no cover

    out = io.StringIO()
    monkeypatch.setattr(client_mod, "OpenAIChatClient", Interrupting)
    monkeypatch.setattr(ev, "Progress", lambda total, **kw: Progress(total, stream=out, live=True))
    cases_file = tmp_path / "cases.yaml"
    cases_file.write_text(yaml.safe_dump([{"profile": "event_default", "prompt": "x", "expect": {"no_tool": True}}]))
    args = SimpleNamespace(cases=str(cases_file), profiles=str(PROFILES), verbose=False, no_warmup=True,
                           timeout=None, config=str(ROOT / "config.example.yaml"))
    assert ev.run_eval_cli(args) == 130
    assert out.getvalue().endswith("\r\x1b[K")
    captured = capsys.readouterr()
    assert "eval: 1 case in 1 profile, model gemma4:e4b" in captured.out
    assert "reasoning_effort none, timeout 120 s per request" in captured.out
    assert "eval interrupted after 0 of 1 cases" in captured.err
    assert "partial report: 0 of 1 cases ran" in captured.out


class TracedClient:
    """Streams scripted events with pauses: ("wait", s), ("reasoning"|"text", str), ("tool", ToolCall)."""

    def __init__(self, scripts, finish="stop", usage=None, interrupt_after=None):
        self.scripts = list(scripts)
        self.finish = finish
        self.usage = usage
        self.interrupt_after = interrupt_after
        self.count = 0

    async def stream(self, messages, tools):
        from naollm.llm.client import StreamEvent

        if self.interrupt_after is not None and self.count >= self.interrupt_after:
            raise KeyboardInterrupt
        self.count += 1
        calls = []
        for kind, value in self.scripts.pop(0):
            if kind == "wait":
                await asyncio.sleep(value)
            elif kind == "tool":
                calls.append(value)
                yield StreamEvent("tool", text=value.name)
            else:
                yield StreamEvent(kind, text=value)
        yield StreamEvent("done", calls=calls, finish=self.finish, usage=self.usage)


def _cases_file(tmp_path, cases):
    f = tmp_path / "cases.yaml"
    f.write_text(yaml.safe_dump(cases))
    return f


def test_timings_split_reasoning_text_and_tool(tmp_path):
    cases = [{"profile": "event_default", "lang": "en", "prompt": "Wave!", "expect": {"tool": "play_gesture"}}]
    script = [[("wait", 0.05), ("reasoning", "They want a wave."), ("wait", 0.1), ("text", "Hi!"),
               ("wait", 0.05), ("tool", call("play_gesture", {"name": "wave"}))]]
    client = TracedClient(script, usage={"prompt_tokens": 400, "completion_tokens": 20})
    report = asyncio.run(evaluate(load_cases(_cases_file(tmp_path, cases)), BASE, PROFILES, lambda cfg: client))
    o = report.outcomes["event_default"][0]
    t = o.timing
    assert o.passed and o.reasoning == "They want a wave." and o.reply == "Hi!"
    assert 0.04 < t.first_token < t.first_answer == t.first_text < t.first_tool <= t.total
    assert t.first_answer - t.first_token >= 0.09
    assert o.usage == {"prompt_tokens": 400, "completion_tokens": 20} and o.finish == "stop"


def test_metrics_in_final_report(tmp_path, capsys):
    cases = [{"profile": "event_default", "prompt": f"Frage {i}", "expect": {"no_tool": True}} for i in range(3)]
    script = [[("wait", 0.02), ("text", "Ja.")], [("reasoning", "hm"), ("wait", 0.02)],
              [("wait", 0.02), ("tool", call("dance", {"name": "robot"}))]]
    client = TracedClient(script, finish="length", usage={"prompt_tokens": 100, "completion_tokens": 10})
    report = asyncio.run(evaluate(load_cases(_cases_file(tmp_path, cases)), BASE, PROFILES, lambda cfg: client))
    print_report(report, model="m", reasoning_effort="none")
    out = capsys.readouterr().out
    assert "event_default: 1/3 correct (33%)" in out
    assert "timing over 3 cases, model m, reasoning_effort none" in out
    for row in ("response", "first token", "first answer token (n=2)", "first text (n=1)", "first tool call (n=1)"):
        assert f"  {row}" in out
    assert "tokens: 10 out and 100 in per case on average" in out
    assert "reasoning in 1 of 3, cut off at max_tokens 3, empty replies 1, errors and timeouts 0" in out


def test_verbose_streams_reasoning_reply_calls_and_timings(tmp_path):
    import io

    out = io.StringIO()
    cases = [{"profile": "event_default", "lang": "en", "prompt": "Wave!", "expect": {"tool": "play_gesture"}}]
    script = [[("reasoning", "They want "), ("reasoning", "a wave."), ("text", "Hi there!"),
               ("tool", call("play_gesture", {"name": "wave"}))]]
    asyncio.run(evaluate(load_cases(_cases_file(tmp_path, cases)), BASE, PROFILES,
                         lambda cfg: TracedClient(script, usage={"prompt_tokens": 9, "completion_tokens": 3}),
                         Progress(1, stream=out, live=False, verbose=True)))
    lines = out.getvalue().splitlines()
    assert lines[0] == "[1/1] event_default en 'Wave!'"
    assert lines[1] == "    expect {'tool': 'play_gesture'}"
    assert lines[2] == "    thinking: They want a wave."
    assert lines[3] == "    says: Hi there!"
    assert lines[4] == '    tool call: play_gesture {"name": "wave"}'
    assert lines[5].startswith("    PASS -> play_gesture(")
    assert lines[6].startswith("    total ") and "first tool " in lines[6] and "tokens 3 out, 9 in" in lines[6]
    assert "reasoning 17 chars" in lines[6] and lines[6].endswith("finish stop")


def test_interrupt_prints_partial_report_with_finished_cases(tmp_path, monkeypatch, capsys):
    from types import SimpleNamespace

    import naollm.eval as ev
    import naollm.llm.client as client_mod

    script = [[("text", "Ja.")], [("text", "Nein.")]]
    shared = TracedClient(script, interrupt_after=2)
    monkeypatch.setattr(client_mod, "OpenAIChatClient", lambda cfg, residency=None: shared)
    cases = [{"profile": "event_default", "prompt": f"Frage {i}", "expect": {"no_tool": True}} for i in range(4)]
    args = SimpleNamespace(cases=str(_cases_file(tmp_path, cases)), profiles=str(PROFILES), verbose=False,
                           no_warmup=True, timeout=None, config=str(ROOT / "config.example.yaml"))
    assert ev.run_eval_cli(args) == 130
    out = capsys.readouterr().out
    assert "partial report: 2 of 4 cases ran" in out
    assert "event_default: 2/2 correct (100%)" in out
    assert "not judged, the run was interrupted" in out
    assert "timing over 2 cases" in out


def test_eval_uses_long_timeout_and_warms_the_model(tmp_path, monkeypatch, capsys):
    from types import SimpleNamespace

    import naollm.eval as ev
    from llm_stub import StubServer

    cases = [{"profile": "event_default", "prompt": "Wie heißt du?", "expect": {"no_tool": True}}]
    with StubServer([{"content": "Ich bin NAO."}, {"_finish": "stop"},
                     {"_usage": {"prompt_tokens": 50, "completion_tokens": 4}}], ollama=True) as server:
        base = dict(BASE, llm=dict(BASE["llm"], base_url=server.base_url, model="stub"))
        config = tmp_path / "config.yaml"
        config.write_text(yaml.safe_dump(base))
        args = SimpleNamespace(cases=str(_cases_file(tmp_path, cases)), profiles=str(PROFILES), verbose=False,
                               no_warmup=False, timeout=None, config=str(config))
        seen = []
        real = ev.Progress

        class Recording(real):
            def __init__(self, total, **kw):
                super().__init__(total, **kw)
                seen.append(self)

        monkeypatch.setattr(ev, "Progress", Recording)
        assert ev.run_eval_cli(args) == 0
        assert [b["keep_alive"] for b in server.native] == [-1, 0]
        body = server.bodies[0]
    out = capsys.readouterr().out
    assert out.index("model stub loaded in") < out.index("[1/1] PASS") < out.index("model stub unloaded")
    assert body["reasoning_effort"] == "none" and body["keep_alive"] == -1
    assert body["stream_options"] == {"include_usage": True}
    assert "tokens: 4 out and 50 in" in out


def test_warm_up_skipped_when_not_ollama(tmp_path, capsys):
    from types import SimpleNamespace

    import naollm.eval as ev
    from llm_stub import StubServer

    cases = [{"profile": "event_default", "prompt": "Wie heißt du?", "expect": {"no_tool": True}}]
    with StubServer([{"content": "Ich bin NAO."}]) as server:
        base = dict(BASE, llm=dict(BASE["llm"], base_url=server.base_url, model="stub"))
        config = tmp_path / "config.yaml"
        config.write_text(yaml.safe_dump(base))
        args = SimpleNamespace(cases=str(_cases_file(tmp_path, cases)), profiles=str(PROFILES), verbose=False,
                               no_warmup=False, timeout=None, config=str(config))
        assert ev.run_eval_cli(args) == 0
        assert server.native == [] and "keep_alive" not in server.bodies[0]
    out = capsys.readouterr().out
    assert "no warm-up:" in out and "[1/1] PASS" in out


def test_empty_reply_fails_a_no_tool_case(tmp_path):
    cases = [{"profile": "event_default", "prompt": f"Frage {i}", "expect": {"no_tool": True}} for i in range(2)]
    script = [[("reasoning", "lange Gedanken")], [("text", "Ja!")]]
    report = asyncio.run(evaluate(load_cases(_cases_file(tmp_path, cases)), BASE, PROFILES,
                                  lambda cfg: TracedClient(script, finish="length")))
    first, second = report.outcomes["event_default"]
    assert not first.passed and first.detail == "empty reply, cut off at max_tokens"
    assert second.passed
