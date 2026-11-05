# Spec Delta

## Purpose

Guarantees that every robot action, whether requested by the LLM or the operator, passes the same deterministic checks, so the LLM chooses what happens and code decides whether and how far.

## ADDED Requirements

### Requirement: Bridge states
The bridge SHALL track one of the states `OFFLINE`, `RESTING`, `IDLE`, `LISTENING`, `THINKING`, `ACTING`, `STOPPED` and `FALLEN` (SPEC §10.1). `STOPPED` and `FALLEN` MUST be reachable from every state. Leaving `STOPPED` or `FALLEN` MUST require an explicit operator resume.

#### Scenario: Fall from any state
- **WHEN** a fall event arrives while the bridge is `THINKING`
- **THEN** the state becomes `FALLEN`, all motion stops, and an operator alert is shown

#### Scenario: Resume from STOPPED
- **WHEN** the bridge is `STOPPED` and the operator confirms resume
- **THEN** the state becomes `IDLE`

### Requirement: Every action passes one gate
Every tool call, from the LLM or from the operator, SHALL go through the same gate and the same handler. The gate MUST never raise to its caller; it MUST return `{"ok": true, "result": ...}` or `{"ok": false, "error": ...}` with a short reason a child can understand.

#### Scenario: Handler failure
- **WHEN** the robot reports an error while running an allowed gesture
- **THEN** the caller receives `ok: false` with a short error text and the motion lock is released

### Requirement: Ordered checks
The gate SHALL apply these checks in order and stop at the first failure (SPEC §10.2): (1) tool exists and is enabled, (2) arguments match the schema, (3) state allows the action, (4) preconditions (posture, walking toggle, battery thresholds, displacement budget), (5) thermal level is not hot. Out-of-range values MUST be rejected with a reason and MUST NOT be clamped.

#### Scenario: Unknown tool
- **WHEN** a call names the tool `fly`
- **THEN** the result is `ok: false` and no robot action is recorded

#### Scenario: Out-of-range step
- **WHEN** walking is enabled and `step` is called with `cm: 50`
- **THEN** the result is `ok: false`, the error states the 30 cm limit, and no move is recorded

#### Scenario: Walking disabled
- **WHEN** walking is disabled and `turn` is called with `degrees: 45`
- **THEN** the result is `ok: false` and no move is recorded

### Requirement: Single motion lock
Only one motion SHALL run at a time. A second motion request while one runs MUST be rejected with the reason "Ich bewege mich gerade schon." (or its English equivalent). The lock MUST be released when the motion ends, fails or is interrupted.

#### Scenario: Concurrent motion
- **WHEN** a dance is running and the operator calls `tool look left`
- **THEN** the operator call returns `ok: false` with the already-moving reason

### Requirement: Motion refused in blocking states
Motion tools SHALL be refused in `STOPPED`, `FALLEN` and `RESTING`. Read-only tools (`get_status`, `who_is_here`) and `stop` MUST remain allowed in every state.

#### Scenario: Gesture while stopped
- **WHEN** the bridge is `STOPPED` and the LLM calls `play_gesture` with `wave`
- **THEN** the result is `ok: false` and no animation is recorded

#### Scenario: Status while stopped
- **WHEN** the bridge is `STOPPED` and `get_status` is called
- **THEN** the result is `ok: true`

### Requirement: Battery and thermal rules
Below `motion.battery_motion_min_pct` (default 20 %) only `set_posture` to `Sit` or `Crouch`, `stop` and read-only tools SHALL be allowed. `dance` MUST require at least `motion.battery_dance_min_pct` (default 30 %). When the thermal level is `warm`, `dance` and `step` MUST be refused. When it is `hot`, all motion except `stop` and `set_posture` to `Crouch` MUST be refused.

#### Scenario: Dance at 25 % battery
- **WHEN** battery is 25 % and `dance` is called
- **THEN** the result is `ok: false` and mentions the battery

#### Scenario: Sit at 15 % battery
- **WHEN** battery is 15 % and `set_posture` is called with `Sit`
- **THEN** the result is `ok: true`

### Requirement: Displacement budget
The sum of all step distances since the last operator reset SHALL NOT exceed `motion.max_total_displacement_m` (default 1.0 m). A step that would exceed the budget MUST be rejected.

#### Scenario: Budget exhausted
- **WHEN** walking is enabled, 90 cm have been stepped, and `step` is called with `cm: 20`
- **THEN** the result is `ok: false` and mentions the limit

### Requirement: Event watcher reacts without the LLM
Robot events SHALL be handled independently of the conversation (SPEC §10.3). A middle or rear head touch MUST trigger the emergency stop. A fall MUST enter `FALLEN`. Battery below 10 % MUST sit the robot down, announce it and rest. Connection loss MUST stop sending commands and alert the operator.

#### Scenario: Rear head touch during a dance
- **WHEN** a dance runs and a rear head touch event arrives
- **THEN** the dance is interrupted, the robot crouches, eyes turn red and the state is `STOPPED`

### Requirement: Emergency stop routine
The emergency stop SHALL run these steps in order, each guarded so that one failure does not skip the rest: set `STOPPED`, cancel the running LLM turn, stop all behaviors and moves, go to `Crouch`, set eyes red, log `ESTOP` with its source (SPEC §18.3).

#### Scenario: Failing step does not skip the rest
- **WHEN** stopping behaviors raises an error during an emergency stop
- **THEN** the robot still goes to `Crouch`, eyes still turn red, the state is `STOPPED`, and the error is logged
