## MODIFIED Requirements

### Requirement: English-only example
`config.example.yaml` SHALL be a working configuration for the current robot: German for robot language and wake language, `[de, en]` for speech input with `de` as fallback, a German school name, the wake phrases `Hallo NAO` and `Hey NAO`, and the `audio.wake_threshold` measured on the robot with `ears test`. Comments MUST state how to switch to English only for a robot without German. Code defaults MUST NOT change.

#### Scenario: Example loads
- **WHEN** `config.example.yaml` is loaded
- **THEN** it is valid, `robot.language` is `German` and `stt.languages` is `[de, en]`

#### Scenario: Existing German file
- **WHEN** a configuration sets only `robot.url`
- **THEN** the language defaults are still German
