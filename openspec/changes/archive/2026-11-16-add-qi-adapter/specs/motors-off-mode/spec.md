## ADDED Requirements

### Requirement: Real adapter requires motors off
The bridge SHALL refuse to start with the real adapter unless `motion.motors_enabled` is `false`. The refusal MUST state that real motion is not implemented yet. The fake robot MUST keep working with either value.

#### Scenario: Real run with motors enabled
- **WHEN** `naollm run` starts without `--dry-run` and `motion.motors_enabled` is `true`
- **THEN** the bridge exits with a non-zero code before it connects to the robot

#### Scenario: Dry run with motors enabled
- **WHEN** `naollm run --dry-run` starts and `motion.motors_enabled` is `true`
- **THEN** the bridge starts with the fake robot and motion tools work as before

### Requirement: Real adapter never commands a motor
The real adapter SHALL NOT call any NAOqi method that can command a motor or change stiffness. This includes `ALMotion` commands, `ALRobotPosture.goToPosture`, `ALBehaviorManager` start calls, `ALAnimationPlayer`, `ALAnimatedSpeech` and `ALBasicAwareness`. Motion adapter methods that command the robot MUST raise a motors-disabled error without calling NAOqi.

#### Scenario: Forbidden call in the test double
- **WHEN** the full bridge lifecycle runs against a test double that fails on any NAOqi method outside the read-only allowlist
- **THEN** the run completes with no forbidden call

#### Scenario: Direct motion call
- **WHEN** code calls `go_to_posture("Stand")` on the real adapter
- **THEN** a motors-disabled error is raised and no NAOqi method is called

### Requirement: Robot must be at rest to start
With motors off, startup SHALL read the robot state before any other robot action. Startup MUST refuse when the robot is awake, any joint is stiff, or Autonomous Life is not `disabled`. The bridge MUST NOT change Autonomous Life during a normal start.

#### Scenario: Stiff robot
- **WHEN** the robot reports a body stiffness above 0 at startup
- **THEN** the bridge disconnects, exits with a non-zero code and tells the operator to put the robot at rest

#### Scenario: Autonomous Life active
- **WHEN** Autonomous Life reports `solitary` at startup
- **THEN** the bridge refuses to start and names `naollm init` as the fix

### Requirement: Robot waking during a run is reported
With motors off, the bridge SHALL check for a wake-up at least every 5 s while connected. When the robot reports it is awake or stiff, the bridge MUST raise a console alert and enter STOPPED. It MUST NOT send a command to the motors in response.

#### Scenario: Robot wakes up
- **WHEN** the robot reports it is awake during a run
- **THEN** the console shows an alert, the state is STOPPED and no motor command is sent

### Requirement: Routines skip motion
With motors off, the startup, shutdown, emergency stop, idle crouch, battery and thermal routines SHALL skip every motion step. All their other steps MUST still run, and skipped steps MUST NOT be reported as failures.

#### Scenario: Emergency stop
- **WHEN** a rear head touch triggers the emergency stop with motors off
- **THEN** the state is STOPPED, speech stops, the current turn is cancelled, the eyes turn red and the log shows no failed steps

#### Scenario: Shutdown
- **WHEN** the operator types `quit` with motors off
- **THEN** the bridge unsubscribes and disconnects with no crouch or rest step

#### Scenario: Idle timeout
- **WHEN** the bridge is idle past `motion.idle_crouch_s` with motors off
- **THEN** no posture command is issued

### Requirement: Gate rejects motion
With motors off, the gate SHALL reject every motion tool with the reason `motors_off` and a short spoken explanation, whatever the source. LED and read-only tools MUST keep working.

#### Scenario: Admin motion tool
- **WHEN** the operator runs `tool dance macarena` with motors off
- **THEN** the gate returns `ok: false` with reason `motors_off`, and the adapter receives no call

#### Scenario: Eye colour
- **WHEN** the LLM calls `set_eyes` with an allowed colour with motors off
- **THEN** the eyes change

### Requirement: LLM is not offered motion
With motors off, the tools sent to the LLM SHALL exclude `play_gesture`, `dance`, `set_posture`, `look`, `turn`, `step` and `stop`. The system prompt MUST state that the robot cannot move right now, and MUST NOT state that it can dance or make gestures.

#### Scenario: Tool list
- **WHEN** a turn starts with motors off
- **THEN** the request lists only tools outside the excluded set

#### Scenario: Visitor asks for a dance
- **WHEN** the system prompt is built for English with motors off
- **THEN** it says the robot cannot move right now and does not list dancing as an ability

### Requirement: Speech is never animated
With motors off, every spoken line SHALL use `ALTextToSpeech`, including calls that ask for animated speech.

#### Scenario: Animated request
- **WHEN** `say("Hello", animated=True)` runs on the real adapter
- **THEN** `ALTextToSpeech.say` is called and `ALAnimatedSpeech` is not used
