"""Tool-call evaluation (SPEC §14.3): does the model pick the right tool?

Each case names a profile; the profile's YAML overlay is merged onto the base
config, because the tool set depends on config (hidden tools are not sent).
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List

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
            cases.append(Case(profile=item["profile"], prompt=item["prompt"],
                              expect=dict(item["expect"]), lang=item.get("lang", "de")))
        except (KeyError, TypeError):
            raise ConfigError(f"{path}: case {i + 1} needs profile, prompt and expect") from None
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


async def evaluate(
    cases: List[Case],
    base: Dict[str, Any],
    profiles_dir: Path,
    client_factory: Callable[[Config], ChatClient],
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
            try:
                async for event in client.stream(messages, bridge.registry.schemas(bridge.walking)):
                    if event.kind == "done":
                        calls = event.calls
            except LLMUnavailable as err:
                results.append(Outcome(case, False, f"LLM unavailable: {err}"))
                continue
            results.append(_judge(case, calls, bridge))
        close = getattr(client, "close", None)
        if close is not None:
            await close()
    return report


def _judge(case: Case, calls, bridge) -> Outcome:
    exp = case.expect
    got = ", ".join(f"{c.name}({c.arguments})" for c in calls) or "no tool"
    if exp.get("no_tool"):
        return Outcome(case, not calls, got)
    if not calls:
        return Outcome(case, False, "no tool")
    first = calls[0]
    if first.name != exp.get("tool"):
        return Outcome(case, False, got)
    if "args" in exp and not _args_match(exp["args"], first.arguments):
        return Outcome(case, False, got)
    if "rejected" in exp:
        result = bridge.gate.check_and_run(first.name, first.arguments, "eval", case.lang)
        if result.ok == bool(exp["rejected"]):
            return Outcome(case, False, f"{got} -> gate ok={result.ok}")
    return Outcome(case, True, got)


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
    report = asyncio.run(evaluate(cases, base, profiles_dir, lambda cfg: OpenAIChatClient(cfg.llm)))
    print_report(report, args.verbose)
    return 0 if report.passed else 1
