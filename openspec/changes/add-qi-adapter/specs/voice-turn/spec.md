## ADDED Requirements

### Requirement: Listening triggers
The wake word, a front head tap and the operator key or `listen` command SHALL all start the same listening flow. Listening MUST start only from IDLE. Triggers in other states MUST be logged and ignored.

#### Scenario: Head tap while idle
- **WHEN** the front head is touched in IDLE
- **THEN** the state becomes LISTENING

#### Scenario: Trigger while thinking
- **WHEN** the wake word fires in THINKING
- **THEN** the state does not change and the trigger is logged

#### Scenario: Trigger while stopped
- **WHEN** the front head is touched in STOPPED
- **THEN** listening does not start

### Requirement: Listening flow
On a trigger, the bridge SHALL pause the wake word, turn the eyes blue, play a short sound and start the audio stream. When the utterance ends, it MUST stop the stream, enter THINKING with yellow eyes, transcribe the audio and start a conversation turn.

#### Scenario: Normal turn
- **WHEN** a visitor says one sentence after a trigger
- **THEN** the stream stops, the transcript goes to the conversation loop and the robot speaks the answer

#### Scenario: Sound fails
- **WHEN** the listening sound raises an error
- **THEN** the error is logged and listening continues

### Requirement: End of utterance
The bridge SHALL end an utterance after `audio.vad_silence_s` of silence following speech, or at `audio.max_utterance_s`. It MUST end the attempt after `audio.no_speech_timeout_s` with no speech. It MUST handle audio chunks of any length.

#### Scenario: Silence after speech
- **WHEN** speech is followed by 0.8 s of silence with the default settings
- **THEN** the utterance ends

#### Scenario: Long speech
- **WHEN** speech continues for 15 s with the default settings
- **THEN** the utterance ends at the limit and is transcribed

#### Scenario: Nobody speaks
- **WHEN** no speech arrives within `audio.no_speech_timeout_s`
- **THEN** the stream stops and the state returns to IDLE with no LLM call

#### Scenario: Robot chunk size
- **WHEN** audio arrives in chunks of 1365 samples
- **THEN** the detector processes it correctly across chunk boundaries

### Requirement: Short or empty input is ignored
The bridge SHALL ignore utterances shorter than 0.5 s and empty transcripts, and MUST return to IDLE with no LLM call.

#### Scenario: Empty transcript
- **WHEN** speech to text returns an empty string
- **THEN** no LLM call is made and the state is IDLE

### Requirement: Speech to text
The bridge SHALL transcribe with `faster-whisper` using `stt.model`. With one entry in `stt.languages`, it MUST force that language. With several, it MUST take the most probable allowed language and use `stt.fallback_language` when unsure. Transcription MUST NOT block the event loop, and the model MUST load before the bridge reports ready.

#### Scenario: English only
- **WHEN** `stt.languages` is `[en]`
- **THEN** the engine is called with language `en` and the turn runs in English

#### Scenario: Detected language not allowed
- **WHEN** `stt.languages` is `[de, en]` and detection ranks French first and English second
- **THEN** the turn runs in English

### Requirement: Turn language needs an installed voice
When a turn's language has no installed TTS voice, the bridge SHALL run the turn in `robot.language` and log a warning. At startup it MUST log a warning for each language in `stt.languages` that has no installed voice. It MUST NOT warn about languages the configuration does not use.

#### Scenario: German text without a German voice
- **WHEN** `chat --de hallo` runs on a robot without German
- **THEN** a warning is logged and the system prompt and TTS use `robot.language`

#### Scenario: English-only startup
- **WHEN** the configuration uses only English on a robot without German
- **THEN** startup logs no language warning

### Requirement: Wake word is paused while the robot is busy
The wake word SHALL be paused while listening, thinking and speaking, and MUST resume 0.5 s after the robot returns to IDLE. It MUST stay paused after `ears off`, in STOPPED and while disconnected.

#### Scenario: Resume after an answer
- **WHEN** the robot finishes speaking and the state is IDLE
- **THEN** the wake word resumes after 0.5 s

#### Scenario: Stop during the delay
- **WHEN** the emergency stop fires within the 0.5 s delay
- **THEN** the wake word stays paused

### Requirement: Emergency stop ends listening
The emergency stop and connection loss SHALL stop the audio stream, discard buffered audio and abandon the turn.

#### Scenario: Stop during listening
- **WHEN** the rear head is touched in LISTENING
- **THEN** the stream stops, the buffer is cleared, the state is STOPPED and no LLM call is made

### Requirement: Audio stays private
Audio SHALL stay in memory, and MUST be discarded after transcription or abandonment. Audio MUST leave the robot only after a trigger. The bridge MUST NOT write audio to disk, and transcripts MUST follow the existing logging rules for `event_mode`.

#### Scenario: Idle bridge
- **WHEN** the bridge is IDLE
- **THEN** no audio stream is active

#### Scenario: After a turn
- **WHEN** a turn has been transcribed
- **THEN** the listener holds no audio buffer and no audio file was written

### Requirement: Listening is optional at startup
When the `voice` extra is missing or the speech model cannot load, the bridge SHALL still start with typed `chat`, and MUST raise a console alert saying listening is unavailable. Triggers MUST then be logged and ignored.

#### Scenario: Missing extra
- **WHEN** `naollm run` starts without `faster-whisper` installed
- **THEN** the console shows an alert naming the `voice` extra, and `chat` works
