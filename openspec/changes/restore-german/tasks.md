## 1. Preconditions

- [ ] 1.1 Confirm `add-qi-adapter` is archived, and that the robot lists German for both TTS (`getAvailableLanguages`) and ASR (`getAvailableLanguages` on `ALSpeechRecognition`); stop here otherwise
- [ ] 1.2 Record the install route that worked, the pack version and the voice name in `spike/FINDINGS.md` ("Installing German")

## 2. Configuration and eval

- [x] 2.1 Make `config.example.yaml` bilingual as in the `bridge-config` delta, and keep comments for switching back to English only
- [x] 2.2 Check that every English case in `tests/eval/tool_calls.yaml` has `lang: en` (13 do today), and add English counterparts where a German case has none
- [x] 2.3 Report the score per profile and per language in `naollm eval`, and add a column per language to the matrix table
- [x] 2.4 Update the config and eval tests for the new example and the per-language report

## 3. Short-reply language

- [x] 3.1 Add `stt.min_detect_s` (default 1.2) to the configuration and the example
- [x] 3.2 Keep the language of the last turn in the conversation loop, cleared when the history resets
- [x] 3.3 Skip detection in speech to text for utterances with less than `stt.min_detect_s` of speech, and pass the speech length and the conversation language from the listener
- [x] 3.4 Test the voice-turn delta scenarios with the stub engine and the listener

## 4. On the robot (no motors needed)

- [ ] 4.1 Run `naollm init` and check that the languages step passes for German and English
- [ ] 4.2 Run `ears test` with both phrases, at 1 m, in a quiet and a noisy room; set `audio.wake_threshold` from the results and record them
- [ ] 4.3 Run voice turns in German and in English from each trigger, and check that TTS switches per turn and that short German utterances are not run as English; tune `stt.min_detect_s` from the logged speech lengths
- [ ] 4.4 Run `naollm eval` on the event model with the bilingual configuration, and record the per-language scores

## 5. Docs

- [x] 5.1 Update the SPEC "Interim" section to remove "English only", and use the German signage text from SPEC §12.3 again next to the English one
- [x] 5.2 Update the README language notes
