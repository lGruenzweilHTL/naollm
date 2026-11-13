## Context

Today the wake word runs on the robot with `ALSpeechRecognition`, and the laptop gets audio only after a trigger (`voice-turn`, "Audio stays private"). The spike heard `Hey NAO` at 0.43 and 0.44. `QiAudio.start_stream` already delivers 16 kHz mono PCM from the front microphone in chunks of 1365 samples, and `audio/vad.py` already handles chunks of any length. `spike/record_clips.py` and `spike/stt_bench.py` exist and work on recorded clips in `spike/clips/`.

## Goals / Non-Goals

**Goals:**
- A wake word that meets the phase 3 numbers, chosen by configuration.
- The same listening flow, pause rules and emergency stop behavior as the robot engine.
- The changed privacy promise visible to the operator and to visitors.

**Non-Goals:**
- Replacing Whisper. The Gemma audio experiment produces numbers, not code in `src/`.
- Running both engines at once.
- Recording people to train the model.

## Decisions

**openWakeWord with a custom model.** It runs on CPU with ONNX, works on 16 kHz frames of 80 ms, and can be trained from synthetic speech. *Alternatives:* Porcupine (needs an account key and a cloud console to train), and Vosk keyword spotting (a full ASR model, heavier and weaker on a single phrase). The bundled pretrained models are licensed for non-commercial use only; we train our own "Hey NAO" model, so that does not apply.

**Training from synthetic speech.** openWakeWord's training notebook generates positive samples with Piper TTS voices and uses public negative data. Nobody has to be recorded. The README holds the steps, and the model file is kept outside git under `~/.naollm/models/`, with its path in the config. *Alternative:* commit the model. Rejected, because it is a binary that changes with every retraining; the training settings are committed instead.

**Two streams, never one.** The detection stream stops on every pause, and the listening flow opens its own stream after a trigger. *Alternative:* keep one stream and hand it over. Rejected, because a hand-over makes it easy for the wake phrase or pre-trigger audio to end up in the transcript, and the 2 s rule is easier to check with separate buffers.

**Chunk adapter.** The robot sends 1365-sample chunks and openWakeWord wants 1280-sample frames. A small re-framer with at most one frame of carry-over sits between them, like the one in `audio/vad.py`.

**No silent fallback.** If the laptop model cannot load, the bridge does not switch to the robot engine by itself, because the operator chose the engine and the signage matches that choice. Head tap and the operator key keep working.

**Gemma audio stays in the spike.** `stt_bench.py` gains a mode that sends each clip to an endpoint that accepts audio input, scored with the same WER and turn latency as Whisper. Whether Ollama accepts audio for `gemma4:e4b` is not known; if it does not, the experiment uses another local runtime and says so in the findings.

## Risks / Trade-offs

- [Audio leaves the robot all the time] → Off by default, console and status bar warnings, notice and signage text, 2 s buffer, no disk writes.
- [False triggers from the robot's own voice] → The stream stops while speaking and for 0.5 s after, as with the robot engine.
- [Wi-Fi load from a constant stream] → About 32 KB/s on the club's own router, laptop on Ethernet (SPEC §3).
- [Synthetic training data does not match children's voices or hall noise] → Tune the threshold on site with `ears test`, and measure against the phase 3 numbers before the event.

## Migration Plan

Default stays `robot`, so nothing changes until an operator sets `audio.wake_engine: laptop`. Rollback: set it back to `robot`.

## Open Questions

- Does Ollama accept audio input for `gemma4:e4b`, and if not, which local runtime should the experiment use?
- Can the robot's own `ALSpeechRecognition` be left fully unsubscribed without side effects on NAOqi 2.8.5.11?
