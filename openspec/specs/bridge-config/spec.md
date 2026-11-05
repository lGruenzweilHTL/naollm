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
