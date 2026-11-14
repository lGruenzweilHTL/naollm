# event-readiness Specification

## Purpose

Defines `naollm preflight`, which checks the event setup before the showcase and exits non-zero on any failed required check, and `naollm cleanup`, the after-event deletion checklist.

## Requirements

### Requirement: Preflight command
`naollm preflight` SHALL run every check below, print a pass, fail or skip line with a fix hint for each, and exit with a non-zero code when a required check fails. It MUST NOT command a motor, and MUST NOT change any setting on the robot or in the configuration.

#### Scenario: All checks pass
- **WHEN** every required check passes
- **THEN** the exit code is 0 and the summary says the setup is ready

#### Scenario: One required check fails
- **WHEN** `privacy.debug_logging` is `true`
- **THEN** the check fails with a hint naming the key, and the exit code is non-zero

### Requirement: Configuration checks
Preflight SHALL require `event_mode: true`, `privacy.persist_transcripts: false` and `privacy.debug_logging: false`. It MUST fail when `llm.base_url` does not point to the local machine or a private network address, and MUST warn when the age game or the laptop wake engine is on, so the operator can check the signage.

#### Scenario: Cloud endpoint
- **WHEN** `llm.base_url` is `https://api.example.com/v1`
- **THEN** the endpoint check fails and says the signage would have to change

#### Scenario: Age game on
- **WHEN** `privacy.age_game_enabled` is `true`
- **THEN** preflight shows a warning that the signage must announce the game, and the check does not fail

### Requirement: Model and eval checks
Preflight SHALL require that `llm.model` can be loaded, and that the latest eval result file for that model is at most 14 days old, was made with the current case file, and scored at least 90 % in every profile the configuration uses.

#### Scenario: Stale eval
- **WHEN** the latest result for the model is 20 days old
- **THEN** the eval check fails and prints the `naollm eval` command to run

#### Scenario: Case file changed
- **WHEN** the case file hash in the result differs from the current file
- **THEN** the eval check fails and says the cases changed since the last run

### Requirement: Robot checks
When the robot is reachable, preflight SHALL check the NAOqi version, the TTS and ASR languages the configuration needs, the installed behaviors against the catalogue, and that every playable animation has `reviewed_by`. With `motion.motors_enabled: true`, it MUST also require `robot.battery_reliable: true`. When the robot is not reachable, these checks MUST be reported as failed.

#### Scenario: Unreviewed animation
- **WHEN** `animations/victory.anim.yaml` has an empty `reviewed_by`
- **THEN** the check warns that `victory` will be dropped from the tool enums

#### Scenario: Robot off
- **WHEN** the robot does not answer at `robot.url`
- **THEN** the robot checks fail and name the URL

### Requirement: Consent checks
Preflight SHALL fail when the consent registry has records past `delete_after`, or when the robot's learned faces and the registry do not match. It MUST NOT print names in event mode, only counts.

#### Scenario: Overdue record
- **WHEN** one record is past its deletion date
- **THEN** the check fails, shows the count, and names `faces delete` as the fix

### Requirement: Cleanup command
`naollm cleanup` SHALL, after the operator types `DELETE`, forget all faces on the robot, remove every consent record, and check that no audio or transcript file exists under `~/.naollm` or the system temp folder with a `naollm` prefix. It MUST then append one line to `~/.naollm/deletions.log` with the date, the number of faces forgotten and the number of records removed, with no names. It MUST print the manual items: paper consent forms, and the SPEC §12.3 checklist.

#### Scenario: Operator types DELETE
- **WHEN** the operator runs `naollm cleanup` and types `DELETE`
- **THEN** the robot has no learned faces, the registry is empty, and the deletion log gains one line without names

#### Scenario: Operator aborts
- **WHEN** the operator types anything other than `DELETE`
- **THEN** nothing is deleted and the exit code is non-zero

#### Scenario: Leftover audio file
- **WHEN** a file named `naollm-turn.wav` exists in the system temp folder
- **THEN** cleanup reports it as a failed check and asks before deleting it

#### Scenario: Robot unreachable
- **WHEN** the robot does not answer
- **THEN** cleanup does not remove the consent records, and says the faces must be forgotten first
