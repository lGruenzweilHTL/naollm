# Proposal

## Why

The phase 0 connectivity spike needs physical access to the NAO, which is not available yet. Most of the bridge (config, safety gate, tools, conversation loop, admin CLI, keyframe animations) does not depend on how the laptop talks to the robot. Building and testing it now against a fake robot shortens the path from a successful spike to a working phase 1 and 2, and makes the safety rules testable before any motor moves.

## What Changes

- New Python package `naollm` (src layout, `pyproject.toml`, `config.example.yaml`), runnable without a robot.
- Validated YAML configuration (SPEC §15) including the `event_mode` start-up refusal (SPEC §12.4).
- Robot adapter interface split into Motion, Speech, Audio, Events and Perception (SPEC §6.1), plus an in-memory fake used by tests and `--dry-run`. No real `qi` adapter in this change.
- Bridge state machine and safety gate (SPEC §10.1, §10.2), including motion lock, battery and thermal rules, walking toggle and displacement budget.
- The LLM tool set (SPEC §8.2) with schemas generated from config, a uniform `{"ok": ...}` result format, and the v0.2 decisions: reject out-of-range values, soft `stop`, no status colours in `set_eyes`, gesture enum from config.
- Text conversation loop against any OpenAI-compatible endpoint (SPEC §7): tool rounds, speak-before-act, malformed-call retry, timeout handling, in-memory history with idle reset, German and English prompts.
- Admin CLI basics (SPEC §18): `naollm admin`, `naollm run --dry-run` with keyboard text input, `tool`, `tools`, `status`, `estop`, `resume`, `say`, `walk`, `llm`, `reset`, `quit`, `Ctrl+E` hotkey and two-stage `Ctrl+C`.
- Keyframe animation format, validator and Choregraphe "export to Python" importer (SPEC §19), with playback through the gate and the adapter's `play_keyframes`.
- Tool-call evaluation runner with profile-tagged cases (SPEC §14.3).
- Outside OpenSpec and not covered by these specs: `spike/` phase 0 script and findings template for the operator laptop.

Out of scope (blocked on phase 0): real `qi` adapter, robot audio, wake word, STT, `naollm init` / `naollm deploy`, perception and face enrolment.

## Capabilities

### New Capabilities

- `bridge-config`: loading and validating the YAML configuration, defaults, and event-mode privacy refusals.
- `robot-adapter`: the robot access contract the bridge relies on, and the fake robot used without hardware.
- `safety-gate`: bridge states, motion lock and the ordered checks every robot action passes, from the LLM or the operator.
- `llm-tools`: the tools exposed to the LLM, their parameters, results and robot effects.
- `conversation-loop`: one conversation turn from user text to spoken answer, including tool rounds, history and failure handling.
- `admin-cli`: the operator console, its commands, hotkeys and emergency stop.
- `keyframe-animations`: the animation data format, validation rules, import from Choregraphe, and playback.

### Modified Capabilities

None (no existing specs).

## Impact

- New code under `src/naollm/`, tests under `tests/`, animation files under `animations/`.
- New dependencies: `pydantic`, `PyYAML`, `openai`, `prompt_toolkit`, `rich`; dev: `pytest`.
- Python `>=3.10` so the code also runs on the older interpreter the `qi` wheel may require.
- No network ports opened, no robot connection, no audio, transcripts or history written to disk. The only outbound traffic is to the configured LLM endpoint.
