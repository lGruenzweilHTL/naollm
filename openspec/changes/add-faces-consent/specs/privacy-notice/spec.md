## ADDED Requirements

### Requirement: Spoken notice
The `notice` command SHALL make the robot speak a short privacy notice in the current language. The notice MUST state that speech is processed locally and not stored, and that faces are recognized only for club members who consented. It MUST NOT run during a turn.

#### Scenario: Idle notice
- **WHEN** the operator types `notice` in IDLE with English as the language
- **THEN** the robot speaks the English notice with no motion

#### Scenario: Notice during a turn
- **WHEN** the operator types `notice` in THINKING
- **THEN** the command refuses

### Requirement: Accurate wording
The notice and the signage text in the README SHALL describe the current setup: only the configured wake phrase, a head tap as a trigger, and no statement that the robot turns its head while motors are off.

#### Scenario: Motors off wording
- **WHEN** the notice is spoken with motors off
- **THEN** it does not say that the robot turns its head
