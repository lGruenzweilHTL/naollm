## ADDED Requirements

### Requirement: Laptop engine streams while idle
With `audio.wake_engine: laptop`, the bridge SHALL stream the front microphone from the robot whenever wake detection is active, and run the model from `audio.wake_model` on it. A score at or above `audio.laptop_wake_threshold` MUST start the same listening flow as the robot's wake word. The robot's `ALSpeechRecognition` MUST NOT be set up in this mode.

#### Scenario: Wake phrase spotted
- **WHEN** the model scores 0.7 on a frame and the threshold is 0.5 in IDLE
- **THEN** the state becomes LISTENING and the listening sound plays

#### Scenario: Robot engine not used
- **WHEN** the bridge starts with the laptop engine
- **THEN** `ALSpeechRecognition` is not subscribed

### Requirement: Detection pauses stop the stream
Whenever wake detection is paused under the existing rules, the detection stream SHALL stop, and MUST restart 0.5 s after detection resumes. The listening flow MUST use its own stream after a trigger.

#### Scenario: Robot speaking
- **WHEN** the robot starts speaking an answer
- **THEN** the detection stream is stopped and no audio arrives on the laptop until 0.5 s after IDLE

#### Scenario: Ears off
- **WHEN** the operator types `ears off`
- **THEN** the detection stream stops and does not restart until `ears on`

### Requirement: Detection audio is short-lived
The detector SHALL keep at most 2 s of audio in memory, and MUST discard it on a trigger, a pause and shutdown. Detection audio MUST NOT be written to disk, logged or passed to speech to text.

#### Scenario: Trigger
- **WHEN** the wake phrase is spotted
- **THEN** the detection buffer is cleared, and the transcript of the following turn does not contain the wake phrase audio

### Requirement: Missing model or extra
When the `wake` extra is missing or the model file cannot load, the bridge SHALL still start with head tap and the operator key, and MUST raise a console alert naming the cause. It MUST NOT fall back to the robot engine on its own.

#### Scenario: Model file missing
- **WHEN** `audio.wake_model` points to a file that does not exist
- **THEN** the bridge starts, the alert names the path, and head tap still starts listening

### Requirement: Continuous streaming is announced
With the laptop engine, the console SHALL warn at startup that audio streams to the laptop all the time, the status bar MUST show it, and `notice` MUST say that the robot's microphone sends audio to the laptop all the time, where it is checked for the wake phrase and discarded.

#### Scenario: Notice with the laptop engine
- **WHEN** the operator runs `notice` with the laptop engine
- **THEN** the spoken notice says that audio is streamed all the time and discarded

### Requirement: Ears test with the laptop engine
`ears test` SHALL show the live laptop model score once per second for 30 s when the laptop engine is active, and the highest score seen.

#### Scenario: Tuning
- **WHEN** the operator runs `ears test` with the laptop engine and says the phrase twice
- **THEN** the console shows the scores over time and the two peaks
