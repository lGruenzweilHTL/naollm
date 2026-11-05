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
- Update SPEC.md to record the interim English and motors-off decisions.

Out of scope, and blocked until the battery is replaced: behaviors, gestures, dances, keyframe playback, posture changes, head tracking, `ALAnimatedSpeech`, the `qipkg` deploy and `naollm deploy`. Face recognition is in the separate change `add-faces-consent`.

## Capabilities

### New Capabilities

- `qi-robot-adapter`: the real robot transport over `qi`, including its connection, speech, LEDs, events and audio mapping.
- `motors-off-mode`: the guarantee that the bridge never commands a motor, and how every routine and tool behaves under it.
- `voice-turn`: listening triggers, wake-word handling, end-of-utterance detection, speech to text and the path into the conversation loop.
- `robot-init`: the motor-free `naollm init` command and its self-test.

### Modified Capabilities

These capabilities are defined in the unarchived change `add-offline-core`, so `openspec/specs/` is still empty. Their deltas add requirements only. Archive `add-offline-core` before this change.

- `bridge-config`: new keys for motors-off mode, battery reliability, the listen address and speech input, plus English-only configuration.
- `admin-cli`: `ears`, `reconnect`, `listen` and how motion commands behave with motors off.

## Impact

- **Code**: new `robot/qi_adapter.py`, `audio/` and `deploy/` packages. Changes in `bridge.py`, `main.py`, `config.py`, `state`-driven eyes, `robot/events.py`, `robot/adapter.py`, `robot/fake_adapter.py`, `tools/gate.py`, `tools/registry.py`, `tools/handlers.py`, `tools/messages.py`, `llm/prompts.py`, `llm/loop.py`, `admin/commands.py`.
- **Dependencies**: optional extras `robot` (`qi`) and `voice` (`faster-whisper`, `webrtcvad-wheels`). The core package and its tests keep working without them.
- **Configuration**: new optional keys. Existing files stay valid, and code defaults do not change. The local `config.yaml` needs the English and motors-off values before a real run.
- **Hardware**: every hardware task needs no motors. Results for 2.8.5.11 are recorded in `spike/FINDINGS.md`.
- **Privacy**: audio leaves the robot only after a trigger, stays in memory and is discarded after transcription (SPEC §12.2).
