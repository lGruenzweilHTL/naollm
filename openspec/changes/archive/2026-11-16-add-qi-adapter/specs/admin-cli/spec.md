## ADDED Requirements

### Requirement: Real robot modes
`naollm run` and `naollm admin` without `--dry-run` SHALL use the real adapter. `naollm admin` MUST NOT load the speech model or start listening.

#### Scenario: Admin mode
- **WHEN** `naollm admin` starts against the real robot
- **THEN** the console works, the speech model is not loaded and no wake word triggers a turn

### Requirement: Ears commands
`ears on` and `ears off` SHALL enable or disable wake-word triggering while head tap and operator key keep working. `ears test` MUST show every recognized phrase with its confidence for 30 s without starting a turn, and MUST then restore the previous setting.

#### Scenario: Ears off
- **WHEN** the operator types `ears off` and the wake phrase is spoken
- **THEN** no listening starts, and a front head tap still starts listening

#### Scenario: Ears test
- **WHEN** a phrase scores 0.44 during `ears test`
- **THEN** the console shows the phrase and 0.44, and the state stays IDLE

#### Scenario: Stop during ears test
- **WHEN** the emergency stop fires during `ears test`
- **THEN** the test ends and the wake word stays paused

### Requirement: Listen command
`listen` SHALL start the listening flow. In dry-run mode, `listen --wav <file>` MUST feed a 16 kHz mono 16-bit WAV file through the same flow. The real adapter MUST reject `--wav`.

#### Scenario: Recorded clip
- **WHEN** the operator runs `listen --wav clip.wav` in a dry run
- **THEN** the clip is detected, transcribed and answered through the normal flow

#### Scenario: Wrong format
- **WHEN** the file is not 16 kHz mono 16-bit
- **THEN** the command prints an error and the state stays IDLE

### Requirement: Reconnect command
`reconnect` SHALL close the old session, connect again and restore event subscriptions and the wake word. It MUST repeat the at-rest check, and the state MUST stay blocked until the operator types `resume`.

#### Scenario: Reconnect after a network drop
- **WHEN** the operator types `reconnect` in OFFLINE and the robot is reachable and at rest
- **THEN** events arrive again, the console says to type `resume`, and the state is still OFFLINE

#### Scenario: Robot awake on reconnect
- **WHEN** the robot reports it is awake during `reconnect`
- **THEN** the command fails, the session closes and the state stays OFFLINE

### Requirement: Motion commands with motors off
With motors off, `wake`, `rest`, `posture`, `walk on` and `anim play` SHALL refuse with a message that says motors are off, and MUST NOT call the motion adapter. `status` and `tools` MUST show that motors are off. `behaviors` MUST list installed behaviors without running them.

#### Scenario: Rest command
- **WHEN** the operator types `rest` with motors off
- **THEN** the console says motors are off and the adapter receives no call

#### Scenario: Status line
- **WHEN** the operator types `status` with motors off
- **THEN** the output states that motors are off

### Requirement: Model commands
`model load` SHALL load `llm.model` with `keep_alive: -1`, and `model unload` SHALL unload it with `keep_alive: 0`. `model status` MUST show whether the model is loaded, until when, and whether this process holds it. The commands MUST work in `run` and `admin` modes. `model unload` MUST refuse while a turn is running. A failure MUST print the reason and MUST NOT change the state.

#### Scenario: Unload, then quit
- **WHEN** the operator types `model unload` and then `quit`
- **THEN** one unload request is sent, and the next turn before `quit` would load the model with Ollama's default keep-alive

#### Scenario: Load in admin mode
- **WHEN** the operator types `model load` in `naollm admin`
- **THEN** the model is loaded with `keep_alive: -1`, and `quit` unloads it

#### Scenario: Unload during a turn
- **WHEN** the operator types `model unload` in THINKING
- **THEN** the command refuses and no request is sent
