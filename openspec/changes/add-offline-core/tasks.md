# Tasks

## 1. Project setup

- [x] 1.1 Create `pyproject.toml` (hatchling, `requires-python >=3.10`, deps `pydantic>=2`, `PyYAML`, `openai>=1`, `prompt_toolkit>=3`, `rich`; dev extra `pytest`; script `naollm = naollm.main:main`) and the package skeleton from design.md; verify `pip install -e .[dev]` in `/tmp/naollm-venv` succeeds and `naollm --help` prints usage
- [x] 1.2 Add `tests/conftest.py` with shared fixtures (default config, fake robot) and verify `pytest` collects and passes with zero tests

## 2. Configuration (bridge-config)

- [x] 2.1 Implement the pydantic config model for every SPEC §15 section with defaults and `extra="forbid"`; verify tests for valid example, unknown key and wrong type pass
- [x] 2.2 Validate `dances`/`gestures` names and targets (behavior path, `animations/...`, `anim:<name>`); verify tests for keyframe gesture and invalid name pass
- [x] 2.3 Implement event-mode refusal and the cloud-endpoint check; verify tests for debug logging at event, development config, local Ollama and public endpoint pass
- [x] 2.4 Write `config.example.yaml` matching SPEC v0.2 §15; verify a test loads it successfully

## 3. Robot adapter and fake robot (robot-adapter)

- [x] 3.1 Define the Motion, Speech, Audio, Events, Perception and RobotAdapter protocols plus `RobotStatus`, `RobotInfo`, `RobotEvent` types; verify a test checks the fake satisfies every protocol (`isinstance` with `runtime_checkable`)
- [x] 3.2 Implement the fake robot with settable simulated state, ordered action recording and event injection; verify tests for posture change, low battery, recorded speech/gesture and injected head touch pass
- [x] 3.3 Implement interruptible long actions in the fake (simulated duration, stop ends early, `finished`/`interrupted` result) with a time-scale option for tests; verify the stop-during-dance test passes
- [x] 3.4 Add dry-run printing of fake robot actions; verify the dry-run speech test passes

## 4. State machine, gate and event watcher (safety-gate)

- [x] 4.1 Implement the thread-safe state machine with allowed transitions, `STOPPED`/`FALLEN` from any state and operator resume; verify tests for fall from any state and resume pass
- [x] 4.2 Implement the argument checker for the emitted schema subset and the bilingual reason table; verify tests for unknown tool, out-of-range step (rejected, not clamped) and walking disabled pass
- [x] 4.3 Implement the gate pipeline (ordered checks, motion lock released in `finally`, never raises); verify tests for handler failure, concurrent motion, gesture/status while stopped pass
- [x] 4.4 Implement battery, thermal and displacement-budget rules; verify tests for dance at 25 %, sit at 15 %, warm/hot thermal and budget exhausted pass
- [x] 4.5 Implement `estop(source)` with guarded ordered steps and turn cancellation hook; verify tests for step order and a failing step not skipping the rest pass
- [x] 4.6 Implement the event watcher (head touches, fall, battery < 20 % / < 10 %, thermal, connection loss, 3 min idle crouch); verify the rear-head-touch-during-dance test passes with the event injected from another thread

## 5. LLM tools (llm-tools)

- [x] 5.1 Implement tool schemas and the registry with enums from the catalogue, availability filtering and hidden walking tools; verify tests for schema shape, walking off/on, missing behavior and keyframe gesture pass
- [x] 5.2 Implement handlers for `play_gesture`, `dance`, `set_posture`, `look` (fixed angle table, tracking back after 3 s), `turn`, `step`, `set_eyes` (allowed colours, revert after 5 s), `get_status`, `who_is_here`, soft `stop`; verify tests for successful gesture, reserved colour, colour revert, look left, turn right, soft stop during dance, status and dance finished pass

## 6. Conversation loop (conversation-loop)

- [x] 6.1 Implement the OpenAI-compatible client wrapper (streaming, tool-call delta accumulation for OpenAI and Ollama shapes, timeout); verify tests with a stub endpoint for both delta shapes pass
- [x] 6.2 Implement the German and English system prompts per SPEC §7.3; verify a test checks each prompt contains every required rule and the language instruction
- [x] 6.3 Implement the sentence splitter and the speech queue worker; verify tests for two-sentence streaming and abbreviation/number handling pass
- [x] 6.4 Implement the turn loop (history window, tool rounds with limit and fallback line, speak-before-act, malformed-call retry then no tools, timeout apology, cancellation); verify tests for history window, tool then answer, endless tool calls, text and gesture ordering, bad JSON twice, timeout and emergency stop during a turn pass
- [x] 6.5 Implement in-memory history with idle reset and event-mode log redaction; verify tests for idle reset and event-mode log pass

## 7. Admin CLI (admin-cli)

- [x] 7.1 Implement the command parser (positional and `key=value` tool arguments, quoting for `say`, unknown commands, completion candidates); verify parser unit tests for positional, key-value, bad enum and unknown command pass
- [x] 7.2 Implement command handlers (`help`, `status`, `tools`, `tool`, `say`, `posture`, `wake`, `rest`, `walk`, `llm`, `reset`, `estop`, `resume` with confirmation, `listen`, `chat` with `--de`/`--en`, `quit`); verify tests for admin call logged with source `admin`, walking toggle, resume declined and admin mode without LLM pass
- [x] 7.3 Implement the prompt_toolkit console (status toolbar, log above prompt, `Ctrl+E`, empty-line `space`, tab completion) and the `naollm run` / `naollm admin` entry points with `--dry-run`; verify a pipe-input test for hotkey while typing and both space scenarios passes, and `naollm run --dry-run` starts and quits manually
- [x] 7.4 Implement two-stage `Ctrl+C`, `SIGTERM` and unhandled-exception handling with safe shutdown; verify integration tests for single Ctrl+C, double Ctrl+C, SIGTERM and the no-listening-sockets check pass

## 8. Keyframe animations (keyframe-animations)

- [x] 8.1 Implement the animation file model and loader; verify tests for loading the SPEC §19.1 example and name mismatch pass
- [x] 8.2 Add the built-in NAO 6 joint limits and posture reference tables, and robot profile loading; verify the no-robot-profile test passes
- [x] 8.3 Implement the validator with all SPEC §19.3 rules and violation reports; verify tests for angle out of limits, leg joint without `uses_legs`, too fast and valid animation pass
- [x] 8.4 Implement the Choregraphe importer via `ast` (both key formats, radians to degrees, `uses_legs` detection, no code execution); verify tests for import timeline and export not executed pass
- [x] 8.5 Implement the player through the gate (balancer on/off in `finally`) and event-mode filtering of unreviewed animations; verify tests for upper-body playback, interrupted playback and unreviewed in event mode pass
- [x] 8.6 Add `naollm anim validate|list|import` and a sample `animations/victory.anim.yaml` (left unreviewed: review needs a preview on the real robot); verify the validate exit-code test passes and the sample validates

## 9. Evaluation runner and docs

- [ ] 9.1 Implement `naollm eval` with profile overlays and per-profile accuracy, plus `tests/eval/tool_calls.yaml` (~40 de/en cases) and `profiles/event_default.yaml`, `profiles/walking_on.yaml`; verify a test runs the runner against a stubbed endpoint and reports per-profile accuracy
- [ ] 9.2 Write `README.md` (setup, dry run, admin commands, e-stop limitation from SPEC §18.3, eval usage); verify the documented dry-run commands run as written

## 10. Integration check

- [ ] 10.1 Run the full `pytest` suite and a scripted dry-run session (`chat`, `tool`, `estop`, `resume`, `quit`) against a stub endpoint; verify all tests pass and the session ends with the safe-shutdown sequence in the action record
