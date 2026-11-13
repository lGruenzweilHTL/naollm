## MODIFIED Requirements

### Requirement: Motor-free init command
`naollm init --robot <ip-or-hostname>` SHALL run these steps in order: connect, languages, robot settings, robot profile, animations, self-test, summary. With motors on, it MUST also run SSH access after connect and package deploy after robot settings. It MUST be safe to run again, MUST print a pass or fail line with a fix hint for each step, and MUST exit with a non-zero code when a required step fails. With motors off, it MUST NOT command a motor and MUST skip SSH access and package deploy. `--skip-selftest`, `--only <step>` and `--dry-run` MUST be supported.

#### Scenario: Forbidden call in the test double
- **WHEN** `naollm init` runs with motors off against a test double that fails on any NAOqi method outside the allowlist
- **THEN** the run completes with no forbidden call

#### Scenario: Unsupported version
- **WHEN** the robot reports a NAOqi version other than 2.8.x
- **THEN** init reports the connect step as failed and stops

#### Scenario: Dry run
- **WHEN** init runs with `--dry-run`
- **THEN** it uses the fake robot and writes no file

#### Scenario: Motors on
- **WHEN** init runs with `motion.motors_enabled: true`
- **THEN** the step list includes SSH access and package deploy, in that order

### Requirement: Robot settings need confirmation
Init SHALL leave Autonomous Life unchanged when it is already `disabled`. Otherwise it MUST warn about the effect on the robot, and MUST ask the operator to confirm a safe position before it sets the state. It MUST set the robot name only when `--name` is given.

#### Scenario: Operator declines
- **WHEN** Autonomous Life is `solitary` and the operator answers no
- **THEN** the state does not change and the step fails

#### Scenario: Robot wakes after the change
- **WHEN** motors are off and the robot reports it is awake or stiff after Autonomous Life is disabled
- **THEN** the step fails and init stops without further robot commands

#### Scenario: Name given
- **WHEN** init runs with `--name Robbie`
- **THEN** the robot name is set to `Robbie` and the summary shows it

## ADDED Requirements

### Requirement: Self-test with motors
With motors on, the self-test SHALL also run Stand, then Crouch, then one configured gesture, after the operator confirms that the robot is on the floor and the area is clear. A failed posture MUST call `rest` and fail the check. The head touch step MUST check that a middle or rear touch would fire the emergency stop, without running it.

#### Scenario: Area not confirmed
- **WHEN** the operator does not confirm a clear area
- **THEN** the motion checks are marked skipped and no motor command is sent

#### Scenario: Gesture check
- **WHEN** the operator confirms and the robot stands
- **THEN** the self-test plays the first configured gesture and then crouches
