## Why

The showcase is planned in German and English (SPEC §11.1), but the robot has neither German TTS nor German ASR, so `add-qi-adapter` made the example configuration English only. The code paths for German already exist: German prompts, the German wake phrase list, per-turn TTS switching and the `de` fallback in speech to text. This change returns German once a German language pack is installed on the robot, and checks it on the robot before the event.

**Do not apply before German is installed** for both `ALTextToSpeech` and `ALSpeechRecognition`. `spike/FINDINGS.md` ("Installing German") records how far the store route got. **Depends on `add-qi-adapter`** being archived, because this change modifies its `bridge-config` requirement.

## What Changes

- Make `config.example.yaml` bilingual: `robot.language: German`, `stt.languages: [de, en]`, `stt.fallback_language: de`, `audio.wake_language: German`, wake phrases `Hallo NAO` and `Hey NAO`, and a German school name. The threshold is retuned on the robot.
- Keep the comments that say how to go back to English only, for a robot without German.
- Tag the German eval cases so the eval can run them against the bilingual configuration, and require the German cases to reach the same score as the English ones.
- On the robot: install German, run `naollm init` to check both languages, tune the German wake phrases with `ears test`, and run voice turns in both languages.
- Update the SPEC "Interim" section and the signage text to German plus English.
- Keep short replies in the conversation's language. Whisper misdetects very short German utterances such as "Ja" as English and transcribes them as "Yeah". An utterance with less than `stt.min_detect_s` of speech (default 1.2 s) skips detection and is transcribed in the language of the last turn, or in `stt.fallback_language` at the start of a conversation.

Apart from the short-reply rule, no new code paths. Any bug found in the German path during the on-robot checks is fixed within the existing requirements.

## Capabilities

### New Capabilities
None.

### Modified Capabilities
- `bridge-config`: the example configuration targets German and English instead of English only.
- `tool-eval`: German and English cases are reported separately, so a weak language is visible.
- `voice-turn`: a short utterance keeps the language of the current conversation instead of running language detection.

## Impact

- **Files**: `config.example.yaml`, `tests/eval/tool_calls.yaml`, `tests/eval/profiles/`, `SPEC.md`, `README.md`, `spike/FINDINGS.md`.
- **Code**: `eval.py` (per-language report), `audio/stt.py`, `audio/listener.py`, `llm/loop.py` and `config.py` (short-reply language), plus fixes found on the robot, if any.
- **Robot**: a German language pack, installed by hand (SPEC §20.3); the CLI cannot install it.
