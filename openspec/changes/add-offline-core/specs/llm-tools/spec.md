# Spec Delta

## Purpose

Defines the tools the LLM can call to move the robot or read its state, the parameters it may choose from, and the results it gets back, sized for small local models.

## ADDED Requirements

### Requirement: Tool set and result format
The bridge SHALL offer the tools `play_gesture`, `dance`, `set_posture`, `look`, `turn`, `step`, `set_eyes`, `get_status`, `who_is_here` and `stop` (SPEC §8.2). Every tool result MUST be either `{"ok": true, "result": "<short outcome>"}` or `{"ok": false, "error": "<short reason>"}`.

#### Scenario: Successful gesture
- **WHEN** `play_gesture` is called with `wave` and all gate checks pass
- **THEN** the result is `{"ok": true, "result": ...}` and the configured `wave` target is played

### Requirement: Small-model friendly schemas
Tool schemas sent to the LLM SHALL use enums wherever a choice is finite, SHALL have a one-line description, SHALL have no nested objects, SHALL forbid additional properties, and at most 10 tools MUST be sent per request.

#### Scenario: Schema shape
- **WHEN** the tool schemas are generated from the default configuration
- **THEN** every schema has `additionalProperties: false`, no parameter is an object, and no more than 10 tools are present

### Requirement: Disabled tools are hidden
Tools disabled by configuration SHALL NOT be sent to the LLM at all. With walking disabled, `turn` and `step` MUST be absent from the schemas.

#### Scenario: Walking off
- **WHEN** `motion.walking_enabled` is false
- **THEN** the schemas sent to the LLM do not contain `turn` or `step`

#### Scenario: Walking toggled on by operator
- **WHEN** the operator enables walking
- **THEN** the next LLM request contains `turn` and `step`

### Requirement: Enums generated from the catalogue
The `name` enums of `play_gesture` and `dance` SHALL be generated from the `gestures` and `dances` configuration. Entries whose target is not available MUST be left out: behaviors not installed on the robot, and keyframe animations that fail validation or, in event mode, lack a reviewer.

#### Scenario: Missing behavior
- **WHEN** `dances.robot` points to a behavior that is not installed
- **THEN** `robot` is not in the `dance` enum and a warning is shown at start-up

#### Scenario: Keyframe gesture
- **WHEN** `gestures.victory` is `anim:victory` and the animation is valid and reviewed
- **THEN** `victory` is in the `play_gesture` enum

### Requirement: Eye colours exclude status colours
`set_eyes` SHALL accept only `white`, `purple`, `orange`, `pink` and `cyan`. Status colours (blue listening, yellow thinking, red emergency stop, green enrolment) MUST NOT be selectable by the LLM. After 5 s the eyes MUST return to the colour of the current state.

#### Scenario: Reserved colour requested
- **WHEN** `set_eyes` is called with `red`
- **THEN** the result is `ok: false` and the eye colour does not change

#### Scenario: Colour reverts
- **WHEN** `set_eyes` is called with `purple` while `IDLE`
- **THEN** the eyes are purple and return to white after 5 s

### Requirement: Look uses fixed angles
`look` SHALL accept `left`, `right`, `up`, `down` and `at_person`, map each to a fixed head angle pair, and return to person tracking after 3 s.

#### Scenario: Look left
- **WHEN** `look` is called with `left`
- **THEN** the head moves to the fixed left angle and tracking resumes after 3 s

### Requirement: Walking tools
`turn` SHALL accept integer degrees from −90 to 90 and `step` SHALL accept a direction (`forward`, `back`, `left`, `right`) and integer centimetres from 5 to 30. Both MUST require `Stand` posture and the walking toggle, and MUST NOT run while another motion runs.

#### Scenario: Turn right
- **WHEN** walking is enabled, the robot stands, and `turn` is called with `degrees: -45`
- **THEN** a rotation of −45° is sent to the robot

### Requirement: Soft stop
The LLM `stop` tool SHALL stop all behaviors and moves, keep the current posture and leave the bridge in `IDLE`. It MUST NOT enter `STOPPED`.

#### Scenario: Visitor says stop during a dance
- **WHEN** a dance runs and `stop` is called
- **THEN** the dance is interrupted, no posture change is sent, and the state is `IDLE`

### Requirement: Read-only tools
`get_status` SHALL return battery percent, posture and a thermal level of `ok`, `warm` or `hot`. `who_is_here` SHALL return only a person count and the names of enrolled members who gave consent.

#### Scenario: Status
- **WHEN** `get_status` is called with battery 64 %, posture `Stand` and normal temperature
- **THEN** the result contains 64, `Stand` and `ok`

### Requirement: Long tools block until finished
`dance` and gestures SHALL run until finished before the result is returned, and the result MUST say they finished or were interrupted.

#### Scenario: Dance finished
- **WHEN** `dance` is called with `macarena` and runs to the end
- **THEN** the result is `{"ok": true, "result": "finished"}`
