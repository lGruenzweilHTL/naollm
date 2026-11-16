## 1. Prerequisites

- [x] 1.1 Ask the user to confirm archiving `add-offline-core`, then archive it, so `openspec/specs/` has a base for the deltas
- [x] 1.2 Add `robot` and `voice` optional extras to `pyproject.toml`
- [x] 1.3 Add `motion.motors_enabled`, `robot.battery_reliable`, `robot.listen_ip` and `audio.no_speech_timeout_s` to `config.py`, with tests for defaults and the invalid address

## 2. Motors-off mode

- [x] 2.1 Add `Bridge.motors_enabled`, and skip motion steps in `startup`, `shutdown`, `EStop.steps`, `EventWatcher` reactions, `idle_tick` and `before_turn`, with tests on `FakeRobot`
- [x] 2.2 Add the `motors_off` and `perception_off` gate reasons with messages, and hide excluded tools in `Registry`, with tests for every motion tool and source
- [x] 2.3 Add motor-free system prompts in both languages, with tests
- [x] 2.4 Refuse console motion commands with motors off, and show the state in `status` and `tools`, with tests
- [x] 2.5 Hide the battery value in `get_status` and `status`, and log-only battery events, when `robot.battery_reliable` is false, with tests

## 3. Real adapter

- [x] 3.1 Add `WAKE_CANDIDATE` to `EventKind`, and extend `FakeRobot` with wake events and a WAV-backed audio stream
- [x] 3.2 Write `tests/qi_double.py` with per-service method allowlists that fail on any other call
- [x] 3.3 Implement connection, `info`, `QiSpeech` and eye LEDs in `robot/qi_adapter.py`, with tests against the double
- [x] 3.4 Implement `QiMotion` read-only methods, `MotorsDisabled` for all others, and the at-rest check, with tests
- [x] 3.5 Implement event subscriptions and connection events, including one event per press, with tests
- [x] 3.6 Implement the remote audio stream and the wake word engine, with tests for failed start, failed setup and thresholds
- [x] 3.7 Implement `status()` and the 5 s wake-up poll, with tests
- [x] 3.8 Wire the real adapter into `main.py`, refuse `motors_enabled: true`, name the missing extra, and run a full lifecycle test against the double
- [x] 3.9 On the robot, with no motors: run `naollm admin` and check `say`, eye colours, `status`, `behaviors` and all head touches, then record any API differences in `spike/FINDINGS.md`

## 4. Voice turn

- [x] 4.1 Implement `audio/vad.py` with the re-chunker and the three end conditions, with tests on generated audio
- [x] 4.2 Implement `audio/stt.py` with language selection and lazy import, with tests using a stub engine
- [x] 4.3 Implement `audio/listener.py` for triggers, state, eyes, sound, stream and handoff, with tests against `FakeRobot` and a stub engine
- [x] 4.4 Implement wake word pause and resume rules, and the emergency stop and disconnect paths, with tests including a stop during the 0.5 s delay
- [x] 4.5 Add the turn language fallback and the configured-languages startup check, with tests
- [x] 4.6 Add `ears on|off`, `ears test`, `listen --wav` and `reconnect`, with tests
- [x] 4.7 Add a test that no audio file is written and that no buffer stays after a turn
- [ ] 4.8 On the robot, with no motors: run a voice turn from each trigger, tune VAD aggressiveness and the threshold, check `reconnect`, and record latency and results in `spike/FINDINGS.md`

## 5. Init

- [x] 5.1 Implement the steps in `deploy/init.py`: connect, languages, settings with confirmation, profile and animations, with tests for each failure
- [x] 5.2 Implement `deploy/selftest.py` with no motors, with tests for missing touch, weak wake word and skipped checks
- [x] 5.3 Add `naollm init` with `--skip-selftest`, `--only` and `--dry-run`, and run it against the double
- [x] 5.4 Add a test that a written profile round-trips through `load_limits`
- [x] 5.5 On the robot, with no motors: run `naollm init`, and record the profile's `getLimits` shape and results in `spike/FINDINGS.md`

## 6. Configuration and docs

- [x] 6.1 Rewrite `config.example.yaml` for English and motors off, including how to restore German, with a loading test
- [x] 6.2 Ask the user before changing the local `config.yaml`, which is untracked
- [x] 6.3 Update `SPEC.md` for interim English, motors off, `no_speech_timeout_s`, the VAD choice and the dropped `robot_stream.py`
- [x] 6.4 Update `README.md` with real-robot setup and the no-motors warning, and update `CONTINUATION.md`, which still says the spike has not run
- [x] 6.5 Run the full test suite and report the results

## 7. Model residency

- [x] 7.1 Implement `llm/residency.py` with load, unload and status over the Ollama native API, with tests against a stub server
- [x] 7.2 Warm the model at `naollm run` start, release it on every exit, and send `keep_alive` with chat requests while held, with tests for failure and admin mode
- [x] 7.3 Add `model load|unload|status`, with tests for refusal during a turn
- [x] 7.4 Update README, SPEC and CONTINUATION, and run the full test suite

## 8. Tool eval

- [x] 8.1 Show a live progress line with an ETA, and a result line with the reply for each case, in `naollm eval`, with tests for terminal and plain output
- [x] 8.2 Add `any_of` expectations, running the gate at most once per case, with tests
- [x] 8.3 Review the eval cases: ability questions expect no tool, and over-limit requests accept any safe answer
- [x] 8.4 Update README and SPEC, and run the full test suite

## 9. Reasoning and eval metrics

- [x] 9.1 Add `llm.reasoning_effort` with the default `none`, send it with chat requests, and report reasoning, tool-call starts, finish reason and usage from the client, with tests
- [x] 9.2 Load the model before the first eval case, release it at the end, and wait 120 s per request, with `--timeout` and `--no-warmup`, with tests against a stub Ollama
- [x] 9.3 Add the verbose trace, the timing report, the empty-reply rule and the partial report on Ctrl+C, with tests
- [x] 9.4 Update README, SPEC and CONTINUATION, check against the local Ollama, and run the full test suite

## 10. Eval matrix

- [x] 10.1 Add the matrix config with `base` and `runs`, the comparison table, `--run`, and the partial table on Ctrl+C, with tests
- [x] 10.2 Ship `tests/eval/eval_config.yaml` with the candidate models, thinking on and off where supported
- [x] 10.3 Update README and SPEC, and run the full test suite

