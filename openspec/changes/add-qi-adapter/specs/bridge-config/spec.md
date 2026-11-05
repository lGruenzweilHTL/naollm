## ADDED Requirements

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
