## ADDED Requirements

### Requirement: Motor-free init command
`naollm init --robot <ip-or-hostname>` SHALL run these steps in order: connect, languages, robot settings, robot profile, animations, self-test, summary. It MUST be safe to run again, MUST print a pass or fail line with a fix hint for each step, and MUST NOT command a motor. It MUST exit with a non-zero code when a required step fails. `--skip-selftest`, `--only <step>` and `--dry-run` MUST be supported.

#### Scenario: Forbidden call in the test double
- **WHEN** `naollm init` runs against a test double that fails on any NAOqi method outside the allowlist
- **THEN** the run completes with no forbidden call

#### Scenario: Unsupported version
- **WHEN** the robot reports a NAOqi version other than 2.8.x
- **THEN** init reports the connect step as failed and stops

#### Scenario: Dry run
- **WHEN** init runs with `--dry-run`
- **THEN** it uses the fake robot and writes no file

### Requirement: Language check
Init SHALL check every language the configuration needs against the installed TTS and speech recognition languages. A missing language MUST fail the step and print the Maxtronics store instructions. It MUST NOT try to install a language.

#### Scenario: English configuration
- **WHEN** the configuration needs English and the robot has English for both TTS and ASR
- **THEN** the step passes and German is not reported

#### Scenario: German configuration on this robot
- **WHEN** the configuration needs German and the robot does not have it
- **THEN** the step fails and prints the install instructions

### Requirement: Robot settings need confirmation
Init SHALL leave Autonomous Life unchanged when it is already `disabled`. Otherwise it MUST warn that the effect on the robot is untested, and MUST ask the operator to confirm a safe position before it sets the state. It MUST NOT set a robot name.

#### Scenario: Operator declines
- **WHEN** Autonomous Life is `solitary` and the operator answers no
- **THEN** the state does not change and the step fails

#### Scenario: Robot wakes after the change
- **WHEN** the robot reports it is awake or stiff after Autonomous Life is disabled
- **THEN** the step fails and init stops without further robot commands

### Requirement: Robot profile
Init SHALL write `<robot_profile_dir>/<robot-id>.yaml` with NAOqi version, robot id, languages and joint limits, in the format that `load_limits` reads. Writing the file MUST be atomic.

#### Scenario: Profile round trip
- **WHEN** init writes a profile and `load_limits` reads it
- **THEN** the limits equal the values read from the robot, converted to degrees

### Requirement: Animation validation
Init SHALL validate all animation files against the robot's limits, and MUST NOT play any of them.

#### Scenario: Invalid animation
- **WHEN** an animation exceeds a limit from the profile
- **THEN** the step reports the violation and the animation is not played

### Requirement: Self-test without motors
The interactive self-test SHALL cover spoken sentences in each configured language, eye colour, each head touch sensor, microphone level, a wake word attempt and the battery reading. It MUST state what the operator should do at each step, and MUST mark a skipped check as skipped and never as passed. Its head touch step MUST NOT trigger the emergency stop routine.

#### Scenario: Missing touch event
- **WHEN** the rear sensor reports no event within the time limit
- **THEN** the check fails and names the rear sensor

#### Scenario: Weak wake word
- **WHEN** the wake phrase is heard only below `audio.wake_threshold`
- **THEN** the check reports the confidences, fails and points to `ears test`

#### Scenario: Unreliable battery
- **WHEN** `robot.battery_reliable` is `false`
- **THEN** the battery check is shown as skipped with the reason
