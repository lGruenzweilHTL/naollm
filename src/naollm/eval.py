"""Tool-call evaluation (SPEC §14.3): does the model pick the right tool?

Each case names a profile; the profile's YAML overlay is merged onto the base
config, because the tool set depends on config (hidden tools are not sent).
"""

from __future__ import annotations

import asyncio
import json
import shutil
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, TextIO

import yaml

from naollm.config import Config, ConfigError, parse_config
from naollm.llm.client import ChatClient, LLMUnavailable
from naollm.llm.prompts import system_prompt

TARGET_ACCURACY = 0.9


@dataclass
class Case:
    profile: str
    prompt: str
    expect: Dict[str, Any]
    lang: str = "de"


@dataclass
class Outcome:
    case: Case
    passed: bool
    detail: str
    reply: str = ""
    seconds: float = 0.0


@dataclass
class Report:
    outcomes: Dict[str, List[Outcome]] = field(default_factory=dict)

    def accuracy(self, profile: str) -> float:
        items = self.outcomes.get(profile, [])
        return sum(o.passed for o in items) / len(items) if items else 0.0

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

    While a case waits for the model, a terminal gets one line that redraws
    itself: spinner, case number, prompt, time spent and an ETA from the mean
    time per case so far. Every finished case prints a result line and the
    model's reply, also when the output is not a terminal.
    """

    SPINNER = "|/-\\"

    def __init__(self, total: int, stream: Optional[TextIO] = None, live: Optional[bool] = None) -> None:
        self.total = total
        self.stream = stream or sys.stdout
        isatty = getattr(self.stream, "isatty", None)
        self.live = bool(isatty and isatty()) if live is None else live
        self.done = 0
        self.spent = 0.0
        self._case: Optional[Case] = None
        self._started = 0.0
        self._task: Optional[asyncio.Task] = None

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

    def start(self, case: Case) -> None:
        self._case = case
        self._started = time.monotonic()
        if self.live:
            self._task = asyncio.get_running_loop().create_task(self._spin())

    async def _spin(self) -> None:
        tick = 0
        while True:
            case = self._case
            elapsed = time.monotonic() - self._started
            line = (f"{self.SPINNER[tick % 4]} {self._counter()} {case.profile} {case.lang} "
                    f"{case.prompt!r}  {elapsed:.1f}s  eta {self.eta()}")
            self.stream.write("\r\x1b[K" + self._clip(line))
            self.stream.flush()
            tick += 1
            await asyncio.sleep(0.1)

    def finish(self, outcome: Outcome) -> None:
        if self._task is not None:
            self._task.cancel()
            self._task = None
            self.stream.write("\r\x1b[K")
        case = outcome.case
        mark = "PASS" if outcome.passed else "FAIL"
        lines = [self._clip(f"{self._counter()} {mark} {case.profile} {case.lang} {case.prompt!r} "
                            f"-> {outcome.detail}  ({outcome.seconds:.1f}s)")]
        if not outcome.passed:
            lines.append(self._clip(f"expected {case.expect}", "    "))
        if outcome.reply.strip():
            lines.append(self._clip(f"says: {outcome.reply.strip()}", "    "))
        self.stream.write("\n".join(lines) + "\n")
        self.stream.flush()
        self.done += 1
        self.spent += outcome.seconds

    def stop(self) -> None:
        """Clears the live line, for an interrupted run."""
        if self._task is not None:
            self._task.cancel()
            self._task = None
            self.stream.write("\r\x1b[K")
            self.stream.flush()


async def evaluate(
    cases: List[Case],
    base: Dict[str, Any],
    profiles_dir: Path,
    client_factory: Callable[[Config], ChatClient],
    progress: Optional[Progress] = None,
) -> Report:
    from naollm.bridge import Bridge
    from naollm.robot.fake_adapter import FakeRobot
    from naollm.state import State

    report = Report()
    for profile in dict.fromkeys(c.profile for c in cases):
        cfg = profile_config(base, profiles_dir, profile)
        targets = [t for t in list(cfg.dances.values()) + list(cfg.gestures.values()) if not t.startswith("anim:")]
        robot = FakeRobot(installed_behaviors=targets, time_scale=0)
        bridge = Bridge(cfg, robot)
        bridge.startup(greet=False)
        client = client_factory(cfg)
        results = report.outcomes.setdefault(profile, [])
        for case in (c for c in cases if c.profile == profile):
            if bridge.state.state != State.IDLE:
                bridge.state.resume()
            robot.posture = "Stand"
            robot.fallen = False
            bridge.displacement_cm = 0
            messages = [
                {"role": "system", "content": system_prompt(case.lang, cfg.conversation.school_name)},
                {"role": "user", "content": case.prompt},
            ]
            calls = []
            reply = []
            if progress is not None:
                progress.start(case)
            started = time.monotonic()
            try:
                async for event in client.stream(messages, bridge.registry.schemas(bridge.walking)):
                    if event.kind == "text":
                        reply.append(event.text)
                    elif event.kind == "done":
                        calls = event.calls
                outcome = _judge(case, calls, bridge)
            except LLMUnavailable as err:
                outcome = Outcome(case, False, f"LLM unavailable: {err}")
            except BaseException:
                if progress is not None:
                    progress.stop()
                raise
            outcome.reply = "".join(reply)
            outcome.seconds = time.monotonic() - started
            results.append(outcome)
            if progress is not None:
                progress.finish(outcome)
        close = getattr(client, "close", None)
        if close is not None:
            await close()
    return report


def _judge(case: Case, calls, bridge) -> Outcome:
    """Passes when the calls meet the expectation, or one of `any_of`.

    The gate runs at most once per case, because running a tool changes the
    fake robot.
    """
    got = ", ".join(f"{c.name}({c.arguments})" for c in calls) or "no tool"
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


def print_report(report: Report, verbose: bool = False) -> None:
    for profile, items in report.outcomes.items():
        ok = sum(o.passed for o in items)
        print(f"{profile}: {ok}/{len(items)} correct ({report.accuracy(profile):.0%})")
        for o in items:
            if verbose or not o.passed:
                mark = "PASS" if o.passed else "FAIL"
                print(f"  {mark} [{o.case.lang}] {o.case.prompt!r}: expected {o.case.expect}, got {o.detail}")
    print(f"target {TARGET_ACCURACY:.0%} per profile: {'met' if report.passed else 'NOT met'}")


def run_eval_cli(args) -> int:
    from naollm.llm.client import OpenAIChatClient

    cases_path = Path(args.cases)
    profiles_dir = Path(args.profiles) if args.profiles else cases_path.parent / "profiles"
    base = yaml.safe_load(Path(args.config).read_text(encoding="utf-8")) or {}
    cases = load_cases(cases_path)
    llm = parse_config(base).llm
    profiles = len(dict.fromkeys(c.profile for c in cases))
    def n(count: int, word: str) -> str:
        return f"{count} {word}" + ("" if count == 1 else "s")

    print(f"eval: {n(len(cases), 'case')} in {n(profiles, 'profile')}, model {llm.model} at {llm.base_url}",
          flush=True)
    progress = Progress(len(cases))
    try:
        report = asyncio.run(evaluate(cases, base, profiles_dir, lambda cfg: OpenAIChatClient(cfg.llm), progress))
    except KeyboardInterrupt:
        print(f"\neval interrupted after {progress.done} of {len(cases)} cases", file=sys.stderr)
        return 130
    print()
    print_report(report, args.verbose)
    return 0 if report.passed else 1
