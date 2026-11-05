## Why

The bridge only runs against the fake robot, and `naollm run` without `--dry-run` exits with an error. The phase 0 spike now shows that Python 3 `qi` can do everything the conversation needs on the real robot, except that the robot has a defective battery and no German. This change lets visitors talk to the real robot in English, with a hard guarantee that the bridge never commands a motor.

## What Changes

- Add a real `qi` robot adapter: session, English TTS, remote audio stream, head touch and other events, on-robot wake word, and eye LEDs through `set_eyes`.
- Add motors-off mode through a new `motion.motors_enabled` key. With the real adapter, this change refuses to start unless the key is `false`, because the adapter contains no code that commands a motor.
- With motors off, the startup, shutdown, emergency stop, idle and event routines skip motion. The gate rejects motion tools for every source, and the LLM is neither offered motion tools nor told it can move.
- Refuse to start unless the robot is at rest and Autonomous Life is `disabled`. `naollm init` can disable Autonomous Life after the operator confirms.
- Add the voice turn: wake word, front head tap or operator key, then robot mic, VAD, `faster-whisper`, LLM and English TTS.
- Add `ears on|off`, `ears test` and `reconnect`, and make `listen` work. Add `listen --wav <file>` in dry-run mode, so a turn can run from a recording.
- Add a motor-free `naollm init`: checks, Autonomous Life setting, robot profile, animation validation and a self-test.
- Add English-only configuration: `config.example.yaml` targets this robot, and `robot.battery_reliable: false` hides the battery value, which this robot reports wrongly. German code stays, and returns through configuration once a language pack is installed.
- Keep the LLM loaded: `naollm run` loads `llm.model` through Ollama with `keep_alive: -1` before it reports ready, and unloads it when the bridge exits. Add `model load`, `model unload` and `model status` console commands.
- Make `gemma4:e4b` the default model in the code and in `config.example.yaml`. The laptop has 15 GiB, and `gemma4:e2b` stays the fallback for smaller machines.
- Show `naollm eval` progress: a live line with a spinner and an ETA, and a result line with the model's reply for each case. Add `any_of` expectations, and fix the cases whose expectations rewarded the wrong behavior: "Can you dance?" now expects no tool, and over-limit walking and turning accept any safe answer.
- Update SPEC.md to record the interim English and motors-off decisions.

Out of scope, and blocked until the battery is replaced: behaviors, gestures, dances, keyframe playback, posture changes, head tracking, `ALAnimatedSpeech`, the `qipkg` deploy and `naollm deploy`. Face recognition is in the separate change `add-faces-consent`.

## Capabilities

### New Capabilities

- `qi-robot-adapter`: the real robot transport over `qi`, including its connection, speech, LEDs, events and audio mapping.
- `motors-off-mode`: the guarantee that the bridge never commands a motor, and how every routine and tool behaves under it.
- `voice-turn`: listening triggers, wake-word handling, end-of-utterance detection, speech to text and the path into the conversation loop.
- `robot-init`: the motor-free `naollm init` command and its self-test.
- `model-residency`: loading the Ollama model at start, keeping it loaded, and releasing it at exit.
- `tool-eval`: how `naollm eval` shows its progress and judges a case.

### Modified Capabilities

These capabilities come from the archived change `add-offline-core`. The `conversation-loop` delta changes the default model, and the other deltas add requirements only.

- `bridge-config`: new keys for motors-off mode, battery reliability, the listen address and speech input, plus English-only configuration.
- `admin-cli`: `ears`, `reconnect`, `listen`, `model` and how motion commands behave with motors off.
- `conversation-loop`: the default model is `gemma4:e4b`.

## Impact

- **Code**: new `robot/qi_adapter.py`, `audio/` and `deploy/` packages. Changes in `bridge.py`, `main.py`, `config.py`, `state`-driven eyes, `robot/events.py`, `robot/adapter.py`, `robot/fake_adapter.py`, `tools/gate.py`, `tools/registry.py`, `tools/handlers.py`, `tools/messages.py`, `llm/prompts.py`, `llm/loop.py`, `admin/commands.py`.
- **Dependencies**: optional extras `robot` (`qi`) and `voice` (`faster-whisper`, `webrtcvad-wheels`). The core package and its tests keep working without them.
- **Configuration**: new optional keys. Existing files stay valid. The only changed code default is `llm.model`, now `gemma4:e4b`. The local `config.yaml` needs the English and motors-off values before a real run.
- **Hardware**: every hardware task needs no motors. Results for 2.8.5.11 are recorded in `spike/FINDINGS.md`.
- **Privacy**: audio leaves the robot only after a trigger, stays in memory and is discarded after transcription (SPEC §12.2).
