# Spec Delta

## Purpose

Defines keyframe animations stored as reviewable data files: their format, the safety validation every animation must pass, import from Choregraphe timelines, and playback through the safety gate.

## ADDED Requirements

### Requirement: Animation file format
Animations SHALL be stored as `animations/<name>.anim.yaml` with `name`, `description`, `version`, `author`, `reviewed_by`, `posture`, `duration_s`, `uses_legs`, `interpolation` (`bezier` or `linear`) and `tracks` mapping joint names to `times` and `angles_deg` lists (SPEC §19.1). The file name MUST match `name`.

#### Scenario: Load example
- **WHEN** a file matching the SPEC §19.1 example is loaded
- **THEN** it loads with five tracks and angles available in degrees and radians

#### Scenario: Name mismatch
- **WHEN** `animations/wave2.anim.yaml` contains `name: victory`
- **THEN** loading fails and names both values

### Requirement: Validation rules
An animation SHALL be valid only if all of these hold (SPEC §19.3): only known NAO joint names and no duplicate tracks; `times` and `angles_deg` have equal length; every angle within the joint limits with a 5° margin; joint speed between keyframes at most the configured fraction (default 60 %) of the joint's maximum speed; times strictly increasing and positive; last time at most 30 s; first and last keyframe of every touched joint within 10° of the declared posture; no leg or hip joints when `uses_legs` is false; `reviewed_by` set when `uses_legs` is true. Every violation MUST be reported with joint name and time.

#### Scenario: Angle out of limits
- **WHEN** `HeadYaw` has a keyframe at 130°
- **THEN** validation fails and the report names `HeadYaw` and the keyframe time

#### Scenario: Leg joint without uses_legs
- **WHEN** `uses_legs` is false and a `LKneePitch` track exists
- **THEN** validation fails and names `LKneePitch`

#### Scenario: Too fast
- **WHEN** `LShoulderPitch` moves 100° in 0.1 s
- **THEN** validation fails with a speed violation for `LShoulderPitch` at that time

#### Scenario: Valid animation
- **WHEN** an arm-only animation stays within limits, speed and posture tolerances
- **THEN** validation passes with no violations

### Requirement: Joint limits source
The validator SHALL take joint limits from the robot profile file when one is given, and otherwise from built-in NAO 6 reference limits, and MUST report which source it used.

#### Scenario: No robot profile
- **WHEN** `naollm anim validate --all` runs without a robot profile
- **THEN** it validates against the built-in limits and says so

### Requirement: Animation CLI
`naollm anim validate [name|--all]` SHALL print violations and exit non-zero when any animation is invalid. `naollm anim list` SHALL list animations with their review status. `naollm anim import <export.py> --name <name>` SHALL convert a Choregraphe "export to Python" timeline into an animation file.

#### Scenario: Validate exit code
- **WHEN** one of three animations is invalid and `naollm anim validate --all` runs
- **THEN** the exit code is non-zero and only the invalid one is reported

### Requirement: Choregraphe import
The importer SHALL read the `names`, `times` and `keys` lists from a Choregraphe Python export without executing the file, convert radians to degrees, and write an animation file with `author: human`, empty `reviewed_by`, and `uses_legs` set from the joints present. The imported file MUST then be validated.

#### Scenario: Import timeline
- **WHEN** a Choregraphe export with `HeadYaw` and `RShoulderPitch` keyframes is imported as `nodding`
- **THEN** `animations/nodding.anim.yaml` contains both tracks in degrees, `uses_legs: false`, and the validation report is printed

#### Scenario: Export is not executed
- **WHEN** the export file contains code other than list assignments
- **THEN** that code does not run and the import uses only the list literals

### Requirement: Only reviewed animations at events
In event mode, animations without `reviewed_by` SHALL be left out of the tool enums at start-up with a warning. Invalid animations MUST be left out in every mode.

#### Scenario: Unreviewed in event mode
- **WHEN** `event_mode` is true and `victory` has an empty `reviewed_by`
- **THEN** `victory` is not offered to the LLM and a warning names it

### Requirement: Playback through the gate
Playing an animation SHALL pass the safety gate like every other motion. Upper-body animations MUST play with the balancer on and both feet fixed, and the balancer MUST be switched off afterwards, also when playback fails or is interrupted.

#### Scenario: Upper-body playback
- **WHEN** a valid arm-only animation is played on the fake robot
- **THEN** the recorded actions are balancer on, keyframe playback, balancer off

#### Scenario: Interrupted playback
- **WHEN** the emergency stop fires during playback
- **THEN** playback ends, the balancer is switched off, and the emergency stop routine completes
