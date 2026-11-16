# bridge-config Specification

## Purpose

Defines how the bridge loads, validates and defaults its YAML configuration, and which privacy settings make it refuse to start at an event.
## Requirements
### Requirement: Configuration is loaded from YAML and validated
The bridge SHALL load its configuration from a YAML file given on the command line and SHALL validate every field before connecting to anything. Unknown keys and values of the wrong type MUST be rejected with a message that names the offending key.

#### Scenario: Valid example configuration
- **WHEN** the bridge loads `config.example.yaml` from the repository
- **THEN** loading succeeds and every section from SPEC §15 is available

#### Scenario: Unknown key
- **WHEN** the configuration contains `motion.max_step_meters: 2`
- **THEN** loading fails and the error names `motion.max_step_meters`

#### Scenario: Wrong type
- **WHEN** `llm.temperature` is the string `"warm"`
- **THEN** loading fails and the error names `llm.temperature`

### Requirement: Safe defaults
Every field that is not given SHALL take the default documented in SPEC §15. Walking MUST default to off, the age game MUST default to off, transcript persistence MUST default to off and debug logging MUST default to off.

#### Scenario: Minimal configuration
- **WHEN** the configuration contains only `robot.url`
- **THEN** `motion.walking_enabled` is false, `privacy.age_game_enabled` is false, `privacy.persist_transcripts` is false, `privacy.debug_logging` is false, `llm.max_tool_rounds` is 3 and `conversation.history_turns` is 6

### Requirement: Event mode refuses unsafe privacy settings
When `event_mode` is true, the bridge SHALL refuse to start if `privacy.persist_transcripts` or `privacy.debug_logging` is true, and SHALL say which setting blocks the start.

#### Scenario: Debug logging at the event
- **WHEN** `event_mode` is true and `privacy.debug_logging` is true
- **THEN** start-up fails with a message naming `privacy.debug_logging`

#### Scenario: Development configuration
- **WHEN** `event_mode` is false and `privacy.debug_logging` is true
- **THEN** start-up succeeds

### Requirement: Cloud endpoint warning
If `llm.base_url` does not point to the local machine or a private network address, the bridge SHALL show a persistent warning that conversation data leaves the local network.

#### Scenario: Local Ollama
- **WHEN** `llm.base_url` is `http://localhost:11434/v1`
- **THEN** no cloud warning is shown

#### Scenario: Public endpoint
- **WHEN** `llm.base_url` is `https://api.example.com/v1`
- **THEN** the status output shows a cloud warning for the whole session

### Requirement: Action catalogue entries are well-formed
Each entry in `dances` and `gestures` SHALL map a lowercase name to exactly one target: an installed behavior path, a built-in animation path (`animations/...`), or a keyframe animation reference (`anim:<name>`). Malformed names or targets MUST be rejected at load time.

#### Scenario: Keyframe gesture
- **WHEN** `gestures.victory` is `anim:victory`
- **THEN** loading succeeds and `victory` refers to `animations/victory.anim.yaml`

#### Scenario: Invalid name
- **WHEN** `dances` contains the key `Macarena Dance`
- **THEN** loading fails and the error names the key

### Requirement: Motors-off key
The configuration SHALL accept `motion.motors_enabled` as a boolean with the default `true`.

#### Scenario: Key set
- **WHEN** the file sets `motion.motors_enabled: false`
- **THEN** the loaded configuration reports motors disabled

### Requirement: Battery reliability key
The configuration SHALL accept `robot.battery_reliable` as a boolean with the default `true`. When it is `false`, `get_status` and the status line MUST NOT state a battery value, and battery events MUST only be logged.

#### Scenario: Unreliable battery in status
- **WHEN** `robot.battery_reliable` is `false` and the LLM calls `get_status`
- **THEN** the result contains no battery percentage

#### Scenario: Low reading
- **WHEN** `robot.battery_reliable` is `false` and the robot reports 5 %
- **THEN** the event is logged and the state does not change

### Requirement: Listen address key
The configuration SHALL accept an optional `robot.listen_ip`, and MUST reject a value that is not an IP address.

#### Scenario: Invalid address
- **WHEN** the file sets `robot.listen_ip: laptop`
- **THEN** loading fails with an error naming the key

### Requirement: No-speech timeout key
The configuration SHALL accept `audio.no_speech_timeout_s` as a positive number with the default `5.0`.

#### Scenario: Default value
- **WHEN** the file does not set the key
- **THEN** the value is 5.0

### Requirement: English-only example
`config.example.yaml` SHALL be a working configuration for the current robot: English for robot language, wake language and speech input, an English school name, the wake phrase `Hey NAO`, `audio.wake_threshold: 0.4`, motors off and an unreliable battery. Comments MUST state how to restore German. Code defaults MUST NOT change.

#### Scenario: Example loads
- **WHEN** `config.example.yaml` is loaded
- **THEN** it is valid and `motion.motors_enabled` is `false`

#### Scenario: Existing German file
- **WHEN** a configuration sets only `robot.url`
- **THEN** the language defaults are still German and motors are enabled

### Requirement: Reasoning effort key
The configuration SHALL accept `llm.reasoning_effort` as `none`, `low`, `medium`, `high` or `null`, with the default `none`. The client SHALL send it as `reasoning_effort` with every chat request, and SHALL leave the field out when it is `null`.

#### Scenario: Thinking off by default
- **WHEN** the configuration does not set `llm.reasoning_effort`
- **THEN** every chat request carries `reasoning_effort: "none"`

#### Scenario: Endpoint without the field
- **WHEN** `llm.reasoning_effort` is `null`
- **THEN** chat requests contain no `reasoning_effort` field

