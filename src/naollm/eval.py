"""Tool-call evaluation (SPEC §14.3): does the model pick the right tool?

Each case names a profile; the profile's YAML overlay is merged onto the base
config, because the tool set depends on config (hidden tools are not sent).
"""

from __future__ import annotations

import asyncio
import json
import shutil
import statistics
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, TextIO

import yaml

from naollm.config import Config, ConfigError, parse_config
from naollm.llm.client import ChatClient, LLMUnavailable, ToolCall
from naollm.llm.prompts import system_prompt

TARGET_ACCURACY = 0.9
EVAL_TIMEOUT_S = 120.0   # per request; a CPU-only laptop needs far more than the bridge's 20 s
STREAMED_MIN_S = 0.5     # shorter output says nothing about the generation rate


@dataclass
class Case:
    profile: str
    prompt: str
    expect: Dict[str, Any]
    lang: str = "de"


@dataclass
class Timing:
    """Seconds from sending the request. None when that part never came."""

    total: float = 0.0
    first_token: Optional[float] = None    # reasoning, text or tool call
    first_answer: Optional[float] = None   # text or tool call, so not reasoning
    first_text: Optional[float] = None
    first_tool: Optional[float] = None


@dataclass
class Outcome:
    case: Case
    passed: bool
    detail: str
    reply: str = ""
    seconds: float = 0.0
    reasoning: str = ""
    calls: List[ToolCall] = field(default_factory=list)
    finish: str = ""
    usage: Optional[Dict[str, int]] = None
    timing: Timing = field(default_factory=Timing)
    error: bool = False


@dataclass
class Report:
    outcomes: Dict[str, List[Outcome]] = field(default_factory=dict)
    planned: int = 0

    def accuracy(self, profile: str) -> float:
        items = self.outcomes.get(profile, [])
        return sum(o.passed for o in items) / len(items) if items else 0.0

    def all(self) -> List[Outcome]:
        return [o for items in self.outcomes.values() for o in items]

    @property
    def passed(self) -> bool:
        return all(self.accuracy(p) >= TARGET_ACCURACY for p in self.outcomes)


def deep_merge(base: Dict[str, Any], overlay: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(base)
    for k, v in overlay.items():
        out[k] = deep_merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def load_cases(path: str | Path) -> List[Case]:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or []
    cases = []
    for i, item in enumerate(data):
        try:
            case = Case(profile=item["profile"], prompt=item["prompt"],
                        expect=dict(item["expect"]), lang=item.get("lang", "de"))
        except (KeyError, TypeError, ValueError):
            raise ConfigError(f"{path}: case {i + 1} needs profile, prompt and expect") from None
        if "any_of" in case.expect:
            options = case.expect["any_of"]
            if not isinstance(options, list) or not options or not all(isinstance(o, dict) for o in options):
                raise ConfigError(f"{path}: case {i + 1}: any_of needs a list of expectations")
        cases.append(case)
    return cases


def profile_config(base: Dict[str, Any], profiles_dir: Path, name: str) -> Config:
    path = profiles_dir / f"{name}.yaml"
    try:
        overlay = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except OSError:
        raise ConfigError(f"profile '{name}' not found at {path}") from None
    return parse_config(deep_merge(base, overlay))


def _args_match(expected: Dict[str, Any], raw: str) -> bool:
    try:
        actual = json.loads(raw or "{}")
    except ValueError:
        return False
    return isinstance(actual, dict) and all(actual.get(k) == v for k, v in expected.items())


class Progress:
    """Shows the run as it happens.

    On a terminal, a redrawn line shows a spinner, the case number, the prompt,
    the current phase, the time spent and an ETA from the mean time per case so
    far. Every finished case prints a result line and the model's reply, also
    when the output is not a terminal.

    With verbose, each case instead streams its reasoning and answer as they
    arrive, then prints its tool calls and timings. The spinner only covers the
    wait for the first token.
    """

    SPINNER = "|/-\\"
    DIM, RESET = "\x1b[2m", "\x1b[0m"

    def __init__(self, total: int, stream: Optional[TextIO] = None, live: Optional[bool] = None,
                 verbose: bool = False) -> None:
        self.total = total
        self.stream = stream or sys.stdout
        isatty = getattr(self.stream, "isatty", None)
        self.live = bool(isatty and isatty()) if live is None else live
        self.verbose = verbose
        self.done = 0
        self.spent = 0.0
        self._case: Optional[Case] = None
        self._started = 0.0
        self._phase = "waiting"
        self._chars = 0
        self._label = ""      # verbose: the section being streamed
        self._mid_line = False
        self._task: Optional[asyncio.Task] = None

    # --- formatting ---

    def _width(self) -> int:
        return max(40, shutil.get_terminal_size((100, 24)).columns - 1)

    def _clip(self, text: str, indent: str = "") -> str:
        width = self._width() - len(indent)
        text = " ".join(text.split())
        return indent + (text if len(text) <= width else text[: width - 3] + "...")

    def eta(self) -> str:
        if not self.done:
            return "--:--"
        left = round(self.spent / self.done * (self.total - self.done))
        return f"{left // 60}:{left % 60:02d}"

    def _counter(self) -> str:
        return f"[{self.done + 1:>{len(str(self.total))}}/{self.total}]"

    def _write(self, text: str) -> None:
        self.stream.write(text)
        self.stream.flush()

    def _dim(self, text: str) -> str:
        return f"{self.DIM}{text}{self.RESET}" if self.live else text

    # --- spinner ---

    def spin(self, label: Callable[[], str]) -> None:
        """Starts a redrawn status line; `label` gives its text."""
        if self.live and self._task is None:
            self._task = asyncio.get_running_loop().create_task(self._spin(label))

    async def _spin(self, label: Callable[[], str]) -> None:
        tick = 0
        while True:
            self._write("\r\x1b[K" + self._clip(f"{self.SPINNER[tick % 4]} {label()}"))
            tick += 1
            await asyncio.sleep(0.1)

    def _unspin(self) -> None:
        if self._task is not None:
            self._task.cancel()
            self._task = None
            self._write("\r\x1b[K")

    def _case_label(self) -> str:
        case = self._case
        elapsed = time.monotonic() - self._started
        phase = self._phase if self._phase == "waiting" else f"{self._phase} {self._chars} chars"
        return (f"{self._counter()} {case.profile} {case.lang} {case.prompt!r}  {phase}  "
                f"{elapsed:.1f}s  eta {self.eta()}")

    # --- loading ---

    def note(self, text: str) -> None:
        self._unspin()
        self._write(text + "\n")

    # --- cases ---

    def start(self, case: Case) -> None:
        self._case = case
        self._started = time.monotonic()
        self._phase, self._chars, self._label, self._mid_line = "waiting", 0, "", False
        if self.verbose:
            self._write(f"{self._counter()} {case.profile} {case.lang} {case.prompt!r}\n"
                        f"    expect {case.expect}\n")
        self.spin(self._case_label)

    def event(self, kind: str, text: str) -> None:
        """One streamed piece: reasoning, text or a tool call's name."""
        phase = {"reasoning": "thinking", "text": "answering", "tool": "calling a tool"}.get(kind)
        if phase is None:
            return
        if phase != self._phase:
            self._phase, self._chars = phase, 0
        self._chars += len(text)
        if not self.verbose:
            return
        self._unspin()
        if kind == "tool":
            return   # the full call, with its arguments, is printed when the case ends
        label = "thinking" if kind == "reasoning" else "says"
        if label != self._label:
            self._write(("\n" if self._mid_line else "") + f"    {label}: ")
            self._label = label
        self._write(self._dim(text) if kind == "reasoning" else text)
        self._mid_line = not text.endswith("\n")

    def finish(self, outcome: Outcome) -> None:
        self._unspin()
        if self._mid_line:
            self._write("\n")
            self._mid_line = False
        case = outcome.case
        mark = "PASS" if outcome.passed else "FAIL"
        if self.verbose:
            lines = [f"    tool call: {c.name} {c.arguments}" for c in outcome.calls]
            lines.append(f"    {mark} -> {outcome.detail}")
            lines.append(f"    {_timing_line(outcome)}")
            self._write("\n".join(lines) + "\n\n")
        else:
            lines = [self._clip(f"{self._counter()} {mark} {case.profile} {case.lang} {case.prompt!r} "
                                f"-> {outcome.detail}  ({outcome.seconds:.1f}s)")]
            if not outcome.passed:
                lines.append(self._clip(f"expected {case.expect}", "    "))
            if outcome.reply.strip():
                lines.append(self._clip(f"says: {outcome.reply.strip()}", "    "))
            self._write("\n".join(lines) + "\n")
        self.done += 1
        self.spent += outcome.seconds

    def stop(self) -> None:
        """Clears the live line, for an interrupted run."""
        self._unspin()
        if self._mid_line:
            self._write("\n")
            self._mid_line = False


def _fmt(seconds: Optional[float]) -> str:
    return "-" if seconds is None else f"{seconds:.1f}s"


def _timing_line(o: Outcome) -> str:
    t = o.timing
    parts = [f"total {_fmt(t.total)}", f"first token {_fmt(t.first_token)}",
             f"first answer {_fmt(t.first_answer)}", f"first tool {_fmt(t.first_tool)}"]
    if o.usage:
        parts.append(f"tokens {o.usage.get('completion_tokens', 0)} out, {o.usage.get('prompt_tokens', 0)} in")
    if o.reasoning:
        parts.append(f"reasoning {len(o.reasoning)} chars")
    parts.append(f"finish {o.finish or '-'}")
    return ", ".join(parts)


async def evaluate(
    cases: List[Case],
    base: Dict[str, Any],
    profiles_dir: Path,
    client_factory: Callable[[Config], ChatClient],
    progress: Optional[Progress] = None,
    report: Optional[Report] = None,
) -> Report:
    """Runs the cases. A given `report` fills as cases finish, so it holds a partial result on Ctrl+C."""
    from naollm.bridge import Bridge
    from naollm.robot.fake_adapter import FakeRobot
    from naollm.state import State

    report = report if report is not None else Report()
    report.planned = len(cases)
    for profile in dict.fromkeys(c.profile for c in cases):
        cfg = profile_config(base, profiles_dir, profile)
        targets = [t for t in list(cfg.dances.values()) + list(cfg.gestures.values()) if not t.startswith("anim:")]
        robot = FakeRobot(installed_behaviors=targets, time_scale=0)
        bridge = Bridge(cfg, robot)
        bridge.startup(greet=False)
        client = client_factory(cfg)
        results = report.outcomes.setdefault(profile, [])
        try:
            for case in (c for c in cases if c.profile == profile):
                if bridge.state.state != State.IDLE:
                    bridge.state.resume()
                robot.posture = "Stand"
                robot.fallen = False
                bridge.displacement_cm = 0
                outcome = await _run_case(case, cfg, bridge, client, progress)
                results.append(outcome)
                if progress is not None:
                    progress.finish(outcome)
        except BaseException:
            if progress is not None:
                progress.stop()
            raise
        finally:
            close = getattr(client, "close", None)
            if close is not None:
                try:
                    await close()
                except Exception:
                    pass
    return report


async def _run_case(case: Case, cfg: Config, bridge, client: ChatClient, progress: Optional[Progress]) -> Outcome:
    messages = [
        {"role": "system", "content": system_prompt(case.lang, cfg.conversation.school_name)},
        {"role": "user", "content": case.prompt},
    ]
    timing = Timing()
    reasoning: List[str] = []
    reply: List[str] = []
    calls: List[ToolCall] = []
    finish, usage = "", None
    if progress is not None:
        progress.start(case)
    started = time.monotonic()

    now = 0.0

    def mark(attr: str) -> None:
        if getattr(timing, attr) is None:
            setattr(timing, attr, now)

    try:
        async for event in client.stream(messages, bridge.registry.schemas(bridge.walking)):
            now = time.monotonic() - started   # one clock reading per event, so its marks agree
            if event.kind in ("reasoning", "text", "tool"):
                mark("first_token")
            if event.kind in ("text", "tool"):
                mark("first_answer")
            if event.kind == "reasoning":
                reasoning.append(event.text)
            elif event.kind == "text":
                mark("first_text")
                reply.append(event.text)
            elif event.kind == "tool":
                mark("first_tool")
            elif event.kind == "done":
                calls, finish, usage = event.calls, event.finish, event.usage
                if calls:   # a client that sends no "tool" events still counts a call when it ends
                    mark("first_tool")
                    mark("first_answer")
                    mark("first_token")
            if progress is not None:
                progress.event(event.kind, event.text)
        outcome = _judge(case, calls, bridge, "".join(reply), finish)
    except LLMUnavailable as err:
        outcome = Outcome(case, False, f"LLM unavailable: {err}", error=True)
    timing.total = time.monotonic() - started
    outcome.reply, outcome.reasoning = "".join(reply), "".join(reasoning)
    outcome.calls, outcome.finish, outcome.usage = calls, finish, usage
    outcome.timing, outcome.seconds = timing, timing.total
    return outcome


def _judge(case: Case, calls, bridge, reply: str = "", finish: str = "") -> Outcome:
    """Passes when the calls meet the expectation, or one of `any_of`.

    The gate runs at most once per case, because running a tool changes the
    fake robot. No tool and no text is an empty reply, which never passes:
    the robot would say nothing.
    """
    got = ", ".join(f"{c.name}({c.arguments})" for c in calls) or "no tool"
    if not calls and not reply.strip():
        cut = ", cut off at max_tokens" if finish == "length" else ""
        return Outcome(case, False, f"empty reply{cut}")
    gate: List[Any] = []

    def gate_ok() -> bool:
        if not gate:
            first = calls[0]
            gate.append(bridge.gate.check_and_run(first.name, first.arguments, "eval", case.lang).ok)
        return gate[0]

    options = case.expect["any_of"] if "any_of" in case.expect else [case.expect]
    details = []
    for exp in options:
        ok, detail = _meets(exp, calls, got, gate_ok)
        if ok:
            return Outcome(case, True, got)
        details.append(detail)
    return Outcome(case, False, details[0] if len(options) == 1 else got)


def _meets(exp: Dict[str, Any], calls, got: str, gate_ok: Callable[[], bool]):
    if exp.get("no_tool"):
        return not calls, got
    if not calls:
        return False, "no tool"
    first = calls[0]
    if first.name != exp.get("tool"):
        return False, got
    if "args" in exp and not _args_match(exp["args"], first.arguments):
        return False, got
    if "rejected" in exp and gate_ok() == bool(exp["rejected"]):
        return False, f"{got} -> gate ok={gate_ok()}"
    return True, got


def _p90(values: List[float]) -> float:
    ordered = sorted(values)
    return ordered[max(0, -(-len(ordered) * 9 // 10) - 1)]   # nearest rank


def _stats_row(name: str, values: List[float], total: int) -> str:
    label = name if len(values) == total else f"{name} (n={len(values)})"
    if not values:
        return f"  {label:<26} {'-':>7}"
    return (f"  {label:<26} {statistics.mean(values):>6.1f}s {statistics.median(values):>6.1f}s "
            f"{_p90(values):>6.1f}s {max(values):>6.1f}s")


def print_metrics(outcomes: List[Outcome], model: str = "", reasoning_effort: Optional[str] = "") -> None:
    if not outcomes:
        return
    n = len(outcomes)
    timed = [o for o in outcomes if not o.error]
    head = f"timing over {n} case" + ("" if n == 1 else "s")
    if model:
        head += f", model {model}, reasoning_effort {reasoning_effort if reasoning_effort is not None else 'unset'}"
    print(head)
    print(f"  {'':<26} {'mean':>7} {'median':>7} {'p90':>7} {'max':>7}")
    print(_stats_row("response", [o.timing.total for o in timed], len(timed)))
    rows = (("first token", "first_token"), ("first answer token", "first_answer"),
            ("first text", "first_text"), ("first tool call", "first_tool"))
    for name, attr in rows:
        values = [getattr(o.timing, attr) for o in timed if getattr(o.timing, attr) is not None]
        print(_stats_row(name, values, len(timed)))
    used = [o for o in timed if o.usage]
    if used:
        out_tokens = [o.usage.get("completion_tokens", 0) for o in used]
        in_tokens = [o.usage.get("prompt_tokens", 0) for o in used]
        print(f"  tokens: {statistics.mean(out_tokens):.0f} out and {statistics.mean(in_tokens):.0f} in "
              f"per case on average")
        rate = _generation_rate(used)
        if rate is not None:
            print(f"  generation: {rate[0]:.1f} tokens/s, from the {rate[1]} cases that streamed "
                  f"for {STREAMED_MIN_S:g} s or more")
    counts = _counts(outcomes)
    print(f"  reasoning in {counts['reasoning']} of {len(timed)}, cut off at max_tokens {counts['cut']}, "
          f"empty replies {counts['empty']}, errors and timeouts {counts['errors']}")


def _generation_rate(outcomes: List[Outcome]):
    """(tokens per second, cases used), or None.

    Ollama sends a tool call in one piece, so only output that streamed for a
    while shows a rate.
    """
    streamed = [o for o in outcomes if o.usage and not o.error and o.timing.first_token is not None
                and o.timing.total - o.timing.first_token >= STREAMED_MIN_S]
    if not streamed:
        return None
    rate = (sum(o.usage.get("completion_tokens", 0) for o in streamed)
            / sum(o.timing.total - o.timing.first_token for o in streamed))
    return rate, len(streamed)


def _counts(outcomes: List[Outcome]) -> Dict[str, int]:
    timed = [o for o in outcomes if not o.error]
    return {
        "reasoning": sum(1 for o in timed if o.reasoning),
        "cut": sum(1 for o in timed if o.finish == "length"),
        "empty": sum(1 for o in timed if not o.reply.strip() and not o.calls),
        "errors": len(outcomes) - len(timed),
    }


def print_report(report: Report, verbose: bool = False, partial: bool = False,
                 model: str = "", reasoning_effort: Optional[str] = "") -> None:
    if partial:
        print(f"partial report: {len(report.all())} of {report.planned} cases ran")
    for profile, items in report.outcomes.items():
        if not items:
            continue
        ok = sum(o.passed for o in items)
        print(f"{profile}: {ok}/{len(items)} correct ({report.accuracy(profile):.0%})")
        for o in items:
            if verbose or not o.passed:
                mark = "PASS" if o.passed else "FAIL"
                print(f"  {mark} [{o.case.lang}] {o.case.prompt!r}: expected {o.case.expect}, got {o.detail}")
    if partial:
        print(f"target {TARGET_ACCURACY:.0%} per profile: not judged, the run was interrupted")
    else:
        print(f"target {TARGET_ACCURACY:.0%} per profile: {'met' if report.passed else 'NOT met'}")
    if report.all():
        print()
        print_metrics(report.all(), model, reasoning_effort)


async def _warm_up(llm, progress: Progress):
    """Loads the model before the first case. Returns the residency to release, or None."""
    from naollm.llm.residency import ModelResidency, ModelResidencyError, in_daemon_thread

    residency = ModelResidency(llm)
    try:
        status = await in_daemon_thread(residency.status)
    except ModelResidencyError as err:
        progress.note(f"no warm-up: {err}. The first case also pays the model's load time")
        return None
    if status.loaded:
        progress.note(f"model {llm.model} is already loaded")
        return None
    started = time.monotonic()
    progress.spin(lambda: f"loading model {llm.model}  {time.monotonic() - started:.0f}s")
    try:
        await in_daemon_thread(residency.load)
    except ModelResidencyError as err:
        progress.note(f"warm-up failed: {err}. The first case also pays the model's load time")
        return None
    progress.note(f"model {llm.model} loaded in {time.monotonic() - started:.1f}s")
    return residency


@dataclass
class RunResult:
    """One model and setting of an eval run."""

    label: str
    report: Report
    interrupted: bool = False
    wall: float = 0.0


def run_label(llm) -> str:
    effort = llm.reasoning_effort
    state = "unset" if effort is None else "off" if effort == "none" else f"on ({effort})"
    return f"{llm.model}, reasoning {state}"


def load_eval_config(path: str | Path):
    """Reads an eval config. Returns [(label, config dict)], one entry per run.

    A plain config is one run. A matrix config holds `base` (a config) and
    `runs` (a list of `llm` settings, each merged onto base.llm, with an
    optional `label`).
    """
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ConfigError(f"{path}: top level must be a mapping")
    if "runs" not in data:
        return [(run_label(parse_config(data).llm), data)]
    unknown = sorted(set(data) - {"base", "runs"})
    if unknown:
        raise ConfigError(f"{path}: a matrix config holds only base and runs, not {', '.join(unknown)}")
    base, runs = data.get("base") or {}, data["runs"]
    if not isinstance(runs, list) or not runs or not all(isinstance(r, dict) for r in runs):
        raise ConfigError(f"{path}: runs needs a list of llm settings")
    out = []
    for i, run in enumerate(runs):
        run = dict(run)
        label = run.pop("label", None)
        merged = deep_merge(base, {"llm": run})
        try:
            llm = parse_config(merged).llm
        except ConfigError as err:
            raise ConfigError(f"{path}: run {i + 1}: {err}") from None
        out.append((str(label) if label else run_label(llm), merged))
    return out


def _plural(count: int, word: str) -> str:
    return f"{count} {word}" + ("" if count == 1 else "s")


def print_comparison(results: List[RunResult]) -> None:
    """One row per run: accuracy per profile, median timings and problem counts."""
    profiles = list(dict.fromkeys(p for r in results for p in r.report.outcomes))
    header = (["run"] + profiles + ["target", "first answer", "first text", "response", "tokens/s",
                                    "reasoning", "cut off", "empty", "errors", "wall"])
    rows = []
    for r in results:
        outcomes = r.report.all()
        timed = [o for o in outcomes if not o.error]
        row = [r.label + (" (partial)" if r.interrupted else "")]
        for p in profiles:
            items = r.report.outcomes.get(p, [])
            row.append(f"{sum(o.passed for o in items)}/{len(items)} {r.report.accuracy(p):.0%}" if items else "-")
        row.append("-" if r.interrupted else "met" if r.report.passed else "NOT met")
        for attr in ("first_answer", "first_text", "total"):
            values = [getattr(o.timing, attr) for o in timed if getattr(o.timing, attr) is not None]
            row.append(f"{statistics.median(values):.1f}s" if values else "-")
        rate = _generation_rate(outcomes)
        row.append(f"{rate[0]:.1f}" if rate else "-")
        counts = _counts(outcomes)
        row += [str(counts[k]) for k in ("reasoning", "cut", "empty", "errors")]
        row.append(f"{r.wall:.0f}s")
        rows.append(row)
    widths = [max(len(row[i]) for row in [header] + rows) for i in range(len(header))]
    print(f"comparison of {_plural(len(results), 'run')}; times are medians, target {TARGET_ACCURACY:.0%} per profile")
    for row in [header] + rows:
        cells = [c.ljust(w) if i == 0 else c.rjust(w) for i, (c, w) in enumerate(zip(row, widths))]
        print("  " + "  ".join(cells).rstrip())


def run_eval_cli(args) -> int:
    cases_path = Path(args.cases)
    profiles_dir = Path(args.profiles) if args.profiles else cases_path.parent / "profiles"
    cases = load_cases(cases_path)
    runs = load_eval_config(args.config)
    wanted = getattr(args, "run", None)
    if wanted:
        runs = [(label, cfg) for label, cfg in runs if any(w in label for w in wanted)]
        if not runs:
            print(f"no run matches {', '.join(wanted)}", file=sys.stderr)
            return 2
    if len(runs) == 1:
        result = _run_once(runs[0][1], cases, profiles_dir, args)
        if result.interrupted:
            return 130
        return 0 if result.report.passed else 1
    print(f"eval matrix: {_plural(len(runs), 'run')} from {args.config}", flush=True)
    results: List[RunResult] = []
    for i, (label, cfg) in enumerate(runs, 1):
        print(f"\n=== run {i}/{len(runs)}: {label}", flush=True)
        result = _run_once(cfg, cases, profiles_dir, args, label)
        results.append(result)
        if result.interrupted:
            print(f"matrix stopped: {_plural(len(runs) - i, 'run')} not started", file=sys.stderr)
            break
    print()
    print_comparison(results)
    if results[-1].interrupted:
        return 130
    return 0 if all(r.report.passed for r in results) else 1


def _run_once(base: Dict[str, Any], cases: List[Case], profiles_dir: Path, args,
              label: str = "") -> RunResult:
    from naollm.llm.client import OpenAIChatClient

    llm = parse_config(base).llm
    timeout = getattr(args, "timeout", None) or EVAL_TIMEOUT_S
    verbose = bool(getattr(args, "verbose", False))
    profiles = len(dict.fromkeys(c.profile for c in cases))

    print(f"eval: {_plural(len(cases), 'case')} in {_plural(profiles, 'profile')}, model {llm.model} "
          f"at {llm.base_url}, "
          f"reasoning_effort {llm.reasoning_effort if llm.reasoning_effort is not None else 'unset'}, "
          f"timeout {timeout:g} s per request, max_tokens {llm.max_tokens}", flush=True)
    progress = Progress(len(cases), verbose=verbose)
    report = Report(planned=len(cases))
    held: List[Any] = []

    def factory(cfg: Config) -> ChatClient:
        return OpenAIChatClient(cfg.llm.model_copy(update={"timeout_s": timeout}), held[0] if held else None)

    async def main() -> None:
        residency = None if getattr(args, "no_warmup", False) else await _warm_up(llm, progress)
        if residency is not None:
            held.append(residency)
        await evaluate(cases, base, profiles_dir, factory, progress, report)

    interrupted = False
    started = time.monotonic()
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        interrupted = True
        progress.stop()
        print(f"\neval interrupted after {progress.done} of {len(cases)} cases", file=sys.stderr)
    finally:
        if held and held[0].held:
            try:
                held[0].unload()
                print(f"model {llm.model} unloaded")
            except Exception as err:   # report and go on; the eval result matters more
                print(f"model unload failed: {err}", file=sys.stderr)
    wall = time.monotonic() - started
    print()
    print_report(report, verbose=False, partial=interrupted, model=llm.model,
                 reasoning_effort=llm.reasoning_effort)
    return RunResult(label or run_label(llm), report, interrupted, wall)
