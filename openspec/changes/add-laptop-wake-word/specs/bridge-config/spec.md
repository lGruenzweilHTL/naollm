## ADDED Requirements

### Requirement: Wake engine keys
The configuration SHALL accept `audio.wake_engine` as `robot` or `laptop`, default `robot`, `audio.wake_model` as a file path, and `audio.laptop_wake_threshold` between 0 and 1, default 0.5. With `laptop`, a missing `audio.wake_model` MUST be rejected at load time.

#### Scenario: Default
- **WHEN** a configuration sets only `robot.url`
- **THEN** `audio.wake_engine` is `robot`

#### Scenario: Laptop without a model
- **WHEN** `audio.wake_engine` is `laptop` and `audio.wake_model` is not set
- **THEN** loading fails with a message naming `audio.wake_model`
