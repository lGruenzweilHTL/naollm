## ADDED Requirements

### Requirement: Connection
The real adapter SHALL connect to `robot.url` over `qi` and report the NAOqi version, robot id and robot name. When `robot.listen_ip` is set, it MUST listen on that address so the robot can call back for remote audio. Importing `naollm` MUST NOT import `qi`. A missing `qi` module MUST produce an error that names the `robot` extra.

#### Scenario: Successful connection
- **WHEN** the adapter connects to a robot running NAOqi 2.8.5.11
- **THEN** `info()` returns that version and the robot name

#### Scenario: Missing module
- **WHEN** a real run starts and `qi` is not installed
- **THEN** the bridge exits with a non-zero code and a message naming the `robot` extra

### Requirement: Speech and languages
The adapter SHALL speak through `ALTextToSpeech`, stop speech on request, set the TTS language and list installed TTS languages.

#### Scenario: Stop speech
- **WHEN** `stop_speech` runs during a sentence
- **THEN** `ALTextToSpeech.stopAll` is called

#### Scenario: Language not installed
- **WHEN** `set_language("German")` runs on a robot without German
- **THEN** the adapter raises an error and the TTS language does not change

### Requirement: Eye LEDs
The adapter SHALL set the face LEDs from a `#RRGGBB` value through `ALLeds.fadeRGB`.

#### Scenario: Listening colour
- **WHEN** the bridge enters LISTENING
- **THEN** `fadeRGB` receives the face LED group and the colour `0x0000FF`

### Requirement: Remote audio stream
The adapter SHALL receive 16 kHz mono PCM from the front microphone as a remote `ALAudioDevice` client while a stream is active. `start_stream` MUST deliver chunks to the given callback. `stop_stream` MUST unsubscribe and unregister the client, and MUST be safe to call twice.

#### Scenario: Stream delivery
- **WHEN** the robot sends audio buffers during an active stream
- **THEN** the callback receives each buffer as bytes

#### Scenario: Failed start
- **WHEN** `ALAudioDevice.subscribe` raises
- **THEN** the client is unregistered and the error reaches the caller

### Requirement: Event mapping
The adapter SHALL deliver robot events through `subscribe`: front, middle and rear head touch, fall, battery level, hot devices and wake word. A touch MUST produce one event when pressed and none when released. Connection loss and restoration MUST also produce events.

#### Scenario: Head touch press and release
- **WHEN** `MiddleTactilTouched` reports `1.0` and then `0.0`
- **THEN** handlers receive exactly one `MIDDLE_HEAD` event

#### Scenario: Handler error
- **WHEN** a handler raises while processing an event
- **THEN** the error is logged and later events still arrive

#### Scenario: Connection loss
- **WHEN** the `qi` session reports a disconnect
- **THEN** handlers receive `CONNECTION_LOST`

### Requirement: Wake word engine
The adapter SHALL set up `ALSpeechRecognition` word spotting with the configured language and phrases, and pause or resume it on request. It MUST resume the engine after a failed setup. It MUST emit `WAKE_CANDIDATE` with phrase and confidence for each recognized phrase, and `WAKE_WORD` when the confidence reaches the threshold.

#### Scenario: Above threshold
- **WHEN** `WordRecognized` reports `<...> Hey NAO <...>` with confidence 0.44 and the threshold is 0.4
- **THEN** handlers receive `WAKE_CANDIDATE` and `WAKE_WORD`, both with the phrase `Hey NAO`

#### Scenario: Below threshold
- **WHEN** `WordRecognized` reports confidence 0.3 and the threshold is 0.4
- **THEN** handlers receive `WAKE_CANDIDATE` only

#### Scenario: Language rejected
- **WHEN** `setLanguage` raises during setup
- **THEN** the engine is resumed and the error reaches the caller

### Requirement: Status reading
`status()` SHALL report battery, posture, fallen flag, thermal level and connection state using read-only calls.

#### Scenario: Status while connected
- **WHEN** `status()` runs on a seated robot at rest
- **THEN** it returns the values with `moving` false, and no motor command is sent

### Requirement: Read-only behavior list
The adapter SHALL list installed behaviors with `ALBehaviorManager.getInstalledBehaviors`, and joint limits with `ALMotion.getLimits`.

#### Scenario: Joint limits
- **WHEN** `joint_limits()` runs
- **THEN** it returns minimum and maximum angle and maximum velocity for each joint

### Requirement: Perception is unavailable in this change
The adapter SHALL report perception as unavailable. Tracking requests MUST do nothing, and `who_is_here` MUST NOT be offered or run.

#### Scenario: Who is here
- **WHEN** the operator runs `tool who_is_here` on the real adapter
- **THEN** the gate rejects it with reason `perception_off`
