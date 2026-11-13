## MODIFIED Requirements

### Requirement: Audio stays private
Audio SHALL stay in memory, and MUST be discarded after transcription or abandonment. With the robot wake engine, audio MUST leave the robot only after a trigger. With the laptop wake engine, audio MUST leave the robot before a trigger only while detection is active, under the rules of `laptop-wake-word`. The bridge MUST NOT write audio to disk, and transcripts MUST follow the existing logging rules for `event_mode`.

#### Scenario: Idle bridge
- **WHEN** the bridge is IDLE with the robot wake engine
- **THEN** no audio stream is active

#### Scenario: Idle bridge with the laptop engine
- **WHEN** the bridge is IDLE with the laptop wake engine and ears on
- **THEN** only the detection stream is active, and it holds at most 2 s of audio

#### Scenario: After a turn
- **WHEN** a turn has been transcribed
- **THEN** the listener holds no audio buffer and no audio file was written
