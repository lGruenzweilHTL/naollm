## Context

The bridge, gate, tools, conversation loop and console exist and run against `FakeRobot`. `main.py` exits with an error unless `--dry-run` is given. The spike on the real robot (NAOqi 2.8.5.11, Python 3.12, `qi` 3.1.5, see `spike/FINDINGS.md`) shows:

- Works: session, English TTS, remote audio at -24.4 dBFS, all three head touch events.
- Weak: English word spotting heard `Hey NAO` twice, at 0.43 and 0.44.
- Missing: German, for TTS and for ASR.
- Must not be used: motors. The battery is defective and reads 100 % regardless.
- Not tested on the robot: `ALLeds`, `ALAudioPlayer`, `ALSpeechRecognition.removeAllContext`, the `getLimits` return shape, session reconnect.

Today's code commands motors in several places outside the tools: `Bridge.startup` and `shutdown`, `EStop.steps`, `EventWatcher._battery` and `_thermal`, `Bridge.idle_tick` and `before_turn`, and the console's `wake` and `rest`.

## Goals / Non-Goals

**Goals:**
- A full English voice turn on the real robot.
- A guarantee, enforced by a test, that no code path can command a motor.
- A motor-free `naollm init`.
- German returning through configuration alone once a pack is installed.

**Non-Goals:**
- Real motion, including behaviors, keyframes and head tracking. A later change adds it after the battery is replaced.
- `qipkg` deploy and `naollm deploy`.
- Face recognition (`add-faces-consent`) and the `ALAudioRecorder` fallback, which the spike showed is not needed.
- Installing German.

## Decisions

### D1: No motor code in the real adapter

`QiMotion` implements only `installed_behaviors` and `joint_limits`. Every commanding method raises `MotorsDisabled` without touching NAOqi. A real run with `motors_enabled: true` is refused. A switch around real motion code was the alternative, but one wrong config value could then wake the robot. With no such code, a bug or config mistake cannot command a motor.

### D2: Bridge skips motion, and the adapter is the backstop

`Bridge.motors_enabled` drives the routine changes: `startup`, `shutdown`, `EStop.steps`, the watcher reactions and idle handling omit motion steps. Relying only on `MotorsDisabled` was the alternative, but each emergency stop would log failed steps, and operators would learn to ignore those. Both layers stay, so the bridge is quiet and the adapter still fails closed.

The gate checks `motors_off` right after argument validation, before state checks, so the reason is stable. `Registry.enabled` hides the motion tools and `stop`. `refresh_catalogue` still reads the behavior list but logs one summary line in place of per-entry warnings.

### D3: A strict test double proves the guarantee

`tests/qi_double.py` provides a fake `qi` session. Each service has an explicit allowlist of methods, and any other call fails the test. `ALAutonomousLife.setState` is allowed only during the init settings step. `ALMotion` allows only `getStiffnesses`, `robotIsWakeUp`, `getLimits` and `moveIsActive`. Tests run the startup, a voice turn, an emergency stop, every console motion command, shutdown and init against it. CI needs neither `qi` nor a robot.

### D4: Fixed at-rest check, no automatic change

Startup checks `robotIsWakeUp`, `getStiffnesses("Body")` and Autonomous Life before anything else, as the spike's `motors_off` does. The spike warns that the effect of disabling Autonomous Life is untested, so only `naollm init` changes it, after confirmation. A 5 s poll detects a later wake-up. The response is STOPPED and an alert, since `rest` would itself be a motor command.

### D5: `qi` session handling follows the spike

One `qi.Application` per process, created with `--qi-url` and optional `--qi-listen-url`, the arguments the spike proved. `reconnect` uses the session's `connect`, and if the first on-robot test shows this cannot work, it falls back to a message asking the operator to restart. `qi` callbacks run on `qi` threads. Audio chunks go to a thread-safe buffer, and triggers reach the event loop through `call_soon_threadsafe`.

### D6: Wake word candidates are events

`WordRecognized` produces `WAKE_CANDIDATE` for every hit, and also `WAKE_WORD` at or above the threshold. `ears test` listens to the candidates, so it needs no special adapter mode. `removeAllContext` runs before `setVocabulary`, to avoid a duplicate-vocabulary error on a second setup, and a failure there is logged only because that call is untested on this robot.

The example threshold is 0.4. The two samples scored 0.43 and 0.44, so 0.5 would reject all of them. Two samples in a quiet setting cannot set the final value, so `ears test` tunes it on site. False triggers cost little here because a turn only needs a visitor to speak.

### D7: `webrtcvad-wheels` for end of utterance

The spec's `webrtcvad` has no Python 3.12 wheels, while the `webrtcvad-wheels` fork does, and needs only 30 ms frames with no model. Silero VAD was the alternative, but it adds a heavier runtime for a frame-by-frame decision. A small re-chunker turns the robot's 1365-sample chunks into 480-sample frames. Fan noise near the mic is the main risk, so aggressiveness is a constant, set after testing on the robot.

`audio.no_speech_timeout_s` is an addition to SPEC §11.1. Without it, an accidental trigger would keep the mic open for the full 15 s.

### D8: Listener owns the flow

A new `audio/listener.py` sets `bridge.listen_handler`. It owns pause and resume of the wake word, the stream, the VAD and the handoff to `ConversationLoop.run_turn`. `run_turn` already accepts LISTENING as a start state. Registering a canceller through `register_turn` during listening means the emergency stop already ends it. The loop itself changes little, so typed `chat` and voice follow the same path.

SPEC §5 lists `audio/robot_stream.py`, but SPEC §6.1 keeps transport code inside `naollm.robot`, so the stream goes in `qi_adapter.py` and that file is dropped.

### D9: English comes from configuration

`config.example.yaml` becomes English and motors-off. Code defaults stay German, so existing tests and configs do not change. The startup check now uses the languages the configuration actually needs. `system_prompt` takes a `motors` flag and gets motor-free variants in both languages. When a turn's language has no installed voice, the turn uses `robot.language`, because German speech through an English voice is unintelligible.

### D10: Optional extras

`pip install naollm[robot,voice]` adds `qi`, `faster-whisper` and `webrtcvad-wheels`. Core tests stay fast and need no native wheels. The speech model loads once at startup, on a worker thread, using CPU with `int8`.

### D11: Init reuses the adapter

`deploy/init.py` and `deploy/selftest.py` use the same adapter and allowlist. The self-test reads touch events directly, not through `EventWatcher`, so touching the middle sensor during the test does not trigger the emergency stop. The SPEC's SSH, package deploy, Stand and gesture steps and `--name` are left out, since they need motors, `qipkg` or an untested robot setting.

### D12: The model stays loaded while the bridge runs

Ollama unloads a model 5 minutes after its last request, so the first turn after a quiet spell at the booth paid the load time. `naollm run` now loads `llm.model` with `POST /api/generate` and `keep_alive: -1` before it reports ready, in parallel with the speech model, and every exit path in `run_app` unloads it with `keep_alive: 0`. The native API root is `llm.base_url` without its `/v1`. While the bridge holds the model, chat requests also carry `keep_alive: -1`. On Ollama 0.40.14 a pinned model stays loaded through plain `/v1` requests as well, so this is a second guard, not the only one.

Only a model this process loaded is released at exit, so a `naollm run` does not unload a model that somebody else pinned. `model unload` is the explicit way to release any model. `naollm admin` loads nothing at start, because it has no LLM, but its `model` commands work, so the operator can warm the model before switching to `run`.

A failed load raises an alert and the bridge starts anyway, because an endpoint that is not Ollama has no native API and still answers chats. Setting `OLLAMA_KEEP_ALIVE` on the service was the alternative, but it pins every model forever and lives outside this repository. The cost of `-1`: if the laptop process dies without its `finally` block, the model stays in RAM until `model unload`, `ollama stop <model>` or an Ollama restart.

## Risks / Trade-offs

- **Wake word is unreliable** → Head tap and operator key work without it, and all three touch sensors passed. `ears test` tunes the threshold.
- **Fan noise confuses VAD, or Whisper is weak on robot audio** → Test on recorded robot clips with `listen --wav` first. `audio.max_utterance_s` and the no-speech timeout bound the damage.
- **CPU Whisper is slow on this laptop** → The `stt.model` setting can drop to `base`. The SPEC latency target is checked on the robot, and any miss is reported, not hidden.
- **The robot's own voice triggers listening** → The wake word stays paused during turns and for 0.5 s after.
- **`ALAutonomousLife` changes after a reboot** → The at-rest check blocks startup. It does not protect against a wake-up between runs, so the operator keeps the robot in a safe seated position.
- **Untested NAOqi calls fail** → Non-critical ones are caught and logged. The first on-robot task checks them before the feature work depends on them.
- **Middle and rear head touch stop the conversation** → This is intended (SPEC §10.3), and `resume` clears it. With motors off, the safety value is lower, but the behavior stays the same for the later motion change.
- **Battery has no warning** → The value is hidden since it is wrong. If the mains supply fails, the robot loses power, and the bridge reports a connection loss.

## Migration Plan

1. Archive `add-offline-core`, so the delta specs have a base.
2. Install the extras and update the local `config.yaml` from the example.
3. Run `naollm init`, then `naollm run`.

To roll back, use `--dry-run` or revert. Nothing is stored on the robot apart from the Autonomous Life state.

To restore German after installing a pack: change the language keys, add `Hallo NAO`, re-tune the threshold in German and run `naollm init` again.

## Open Questions

- Which VAD aggressiveness suits the fan noise? Set it on the robot.
- Does the session reconnect without a new `qi.Application`? Decided by the on-robot test in task 4.8.
- What threshold works in a noisy room at 1 m (SPEC §16: 8 of 10 tries)? Measured with `ears test`.
