## 1. Preconditions and model

- [ ] 1.1 Confirm `add-qi-adapter` is archived; stop here otherwise
- [x] 1.2 Add `openwakeword` to a new optional `wake` extra, following the `dependencies` habit, and record its version
- [ ] 1.3 Train a "Hey NAO" model with openWakeWord's synthetic pipeline, keep it under `~/.naollm/models/`, and commit the training settings and steps to the README

## 2. Configuration

- [x] 2.1 Add `audio.wake_engine`, `audio.wake_model` and `audio.laptop_wake_threshold` with validation, and comments in `config.example.yaml`
- [x] 2.2 Test the defaults and the missing-model rejection

## 3. Laptop detector

- [x] 3.1 Add `audio/wake_laptop.py`: re-framer from 1365 to 1280 samples, openWakeWord model, 2 s buffer cap, clear on trigger, pause and shutdown
- [x] 3.2 Wire it into `audio/listener.py`: start the detection stream when detection is active, stop it on every pause, restart 0.5 s after resume, and open a separate stream for listening
- [x] 3.3 Skip the `ALSpeechRecognition` setup with the laptop engine, and raise the alert for a missing extra or model without falling back
- [x] 3.4 Unit-test every scenario in `laptop-wake-word` and the modified `voice-turn` requirement with the fake robot and a stub model

## 4. Operator and visitor notice

- [x] 4.1 Add the startup warning and the status bar flag
- [x] 4.2 Add the continuous-streaming sentence to `notice` in German and English, and to the README signage text
- [x] 4.3 Show live laptop scores in `ears test`

## 5. Gemma audio experiment (spike, outside OpenSpec)

- [x] 5.1 Find out whether Ollama accepts audio input for `gemma4:e4b`; if not, pick another local runtime and note why
- [x] 5.2 Add a Gemma audio mode to `spike/stt_bench.py` that reports WER and turn latency on `spike/clips/`, next to the Whisper models
- [ ] 5.3 Record the comparison in `spike/FINDINGS.md`, and propose an STT engine change only if Gemma beats Whisper in both languages

## 6. On the robot (no motors needed)

- [ ] 6.1 Measure hits at 1 m in a quiet and a noisy room (target ≥ 8/10), and false triggers over 10 min of background chatter (target < 1), for both engines; record the results in `spike/FINDINGS.md`
- [ ] 6.2 Recommend an engine for the event from those numbers
