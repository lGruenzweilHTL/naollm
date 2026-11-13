## Why

On-robot word spotting heard `Hey NAO` at only 0.43 and 0.44 in the spike, below the planned 0.5 threshold, and phase 3 needs ≥ 8/10 hits at 1 m in a noisy room with < 1 false trigger per 10 min (SPEC §16). SPEC §11.2 names the fallback: stream the robot's microphone to the laptop all the time and spot a custom "Hey NAO" model there with openWakeWord. That changes the privacy promise, because audio then leaves the robot before any trigger, so it needs its own change with the privacy rules and signage made explicit.

The second §11.2 item, sending audio straight to Gemma 4 instead of Whisper, is only an experiment. It runs in `spike/` and adds no requirements. A later change adds an STT engine only if the experiment beats Whisper on our clips in both languages.

**Depends on `add-qi-adapter`** being archived, because this change modifies its `voice-turn` and `bridge-config` capabilities. Needs no motors.

## What Changes

- Add `audio.wake_engine: robot | laptop`, default `robot`. With `laptop`, the bridge streams the front microphone while IDLE and runs an openWakeWord model on the laptop.
- Add `audio.wake_model` (path to the trained model file) and `audio.laptop_wake_threshold`.
- Keep the existing pause rules: no detection while listening, thinking, speaking or moving, and none after `ears off`, in STOPPED or while disconnected. The stream stops whenever detection is paused.
- Keep at most 2 s of audio in memory for detection, never write it to disk, and never pass it to speech to text.
- With the laptop engine, the console warns at startup and the status bar shows that audio streams continuously. `notice` and the signage text say so.
- `ears test` shows laptop scores when the laptop engine is active.
- Document how to train the "Hey NAO" model offline with openWakeWord's synthetic-speech pipeline, so no visitor or club member has to be recorded.
- Experiment, outside OpenSpec: add Gemma 4 audio input to `spike/stt_bench.py` and compare it with Whisper on the recorded clips.

## Capabilities

### New Capabilities
- `laptop-wake-word`: the continuous stream, laptop-side detection, its pause rules and its privacy duties.

### Modified Capabilities
- `voice-turn`: "Audio stays private" allows audio to leave the robot before a trigger when the laptop engine is configured.
- `bridge-config`: new `audio.wake_engine`, `audio.wake_model` and `audio.laptop_wake_threshold` keys.

## Impact

- **Code**: `audio/listener.py`, new `audio/wake_laptop.py`, `config.py`, `bridge.py`, `admin/commands.py` (`ears test`, status bar, `notice`).
- **Dependencies**: `openwakeword` and its ONNX runtime, in a new optional `wake` extra.
- **Network**: about 32 KB/s of audio from robot to laptop while IDLE, on the club's own router.
- **Compliance**: signage and the spoken notice must say that audio is streamed all the time to the laptop and discarded.
- **Spike**: `spike/stt_bench.py` gains a Gemma audio mode.
