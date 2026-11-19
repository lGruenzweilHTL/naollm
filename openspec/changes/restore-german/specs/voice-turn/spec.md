## MODIFIED Requirements

### Requirement: Speech to text
The bridge SHALL transcribe with `faster-whisper` using `stt.model`. With one entry in `stt.languages`, it MUST force that language. With several, it MUST take the most probable allowed language and use `stt.fallback_language` when unsure. An utterance with less than `stt.min_detect_s` of speech MUST skip language detection: it is transcribed in the language of the current conversation, or in `stt.fallback_language` when there is none. The current conversation language is the language of the last turn, and it ends when the conversation history resets. Transcription MUST NOT block the event loop, and the model MUST load before the bridge reports ready.

#### Scenario: English only
- **WHEN** `stt.languages` is `[en]`
- **THEN** the engine is called with language `en` and the turn runs in English

#### Scenario: Detected language not allowed
- **WHEN** `stt.languages` is `[de, en]` and detection ranks French first and English second
- **THEN** the turn runs in English

#### Scenario: Short reply in a German conversation
- **WHEN** `stt.languages` is `[de, en]`, the last turn was German, and the visitor says only "Ja"
- **THEN** the engine is called once with language `de` and the turn runs in German

#### Scenario: Short reply in an English conversation
- **WHEN** `stt.languages` is `[de, en]`, `stt.fallback_language` is `de`, the last turn was English, and the visitor says only "Yes"
- **THEN** the engine is called once with language `en` and the turn runs in English

#### Scenario: Short first utterance
- **WHEN** an utterance shorter than `stt.min_detect_s` starts a conversation, or comes after the history has reset
- **THEN** it is transcribed in `stt.fallback_language`

#### Scenario: Longer utterance switches the language
- **WHEN** the last turn was German and the visitor speaks English for longer than `stt.min_detect_s`
- **THEN** detection runs and the turn runs in English
