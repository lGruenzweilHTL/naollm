## ADDED Requirements

### Requirement: Real run with motors needs a reliable battery
With the real adapter and `motion.motors_enabled: true`, the bridge SHALL refuse to start unless `robot.battery_reliable` is `true`. After connecting, it MUST also refuse when the battery reads below `motion.battery_motion_min_pct`. Both refusals MUST happen before any motor command and MUST name the setting or value that caused them.

#### Scenario: Unreliable battery with motors on
- **WHEN** `naollm run` starts without `--dry-run`, `motion.motors_enabled` is `true` and `robot.battery_reliable` is `false`
- **THEN** the bridge exits with a non-zero code before it connects, and the message names `robot.battery_reliable`

#### Scenario: Low battery at start
- **WHEN** the real robot reports 15 % and `motion.battery_motion_min_pct` is 20
- **THEN** the bridge disconnects and exits with a non-zero code, and no motor command was sent

### Requirement: Motion mapping
With motors on, the real adapter SHALL map motion methods to NAOqi as in SPEC §6.2: `ALBehaviorManager` for behaviors, `ALAnimationPlayer` for built-in animations, `ALMotion.angleInterpolationBezier` for keyframes, `ALMotion.wbEnable` and `wbFootState` for the balancer, `ALRobotPosture.goToPosture` for postures, `ALMotion.moveTo` and `stopMove` for steps and turns, `ALMotion.setAngles` for the head, and `ALMotion.wakeUp` and `rest`. Speeds and fractions MUST be fixed constants in the adapter.

#### Scenario: Posture change
- **WHEN** the gate runs `set_posture(Sit)` on the real robot with motors on
- **THEN** `ALRobotPosture.goToPosture` is called with `Sit` and the adapter's fixed speed

#### Scenario: Head look
- **WHEN** the gate runs `look(left)`
- **THEN** `ALMotion.setAngles` receives `HeadYaw` and `HeadPitch` with the angles from the fixed table and the fixed head speed

### Requirement: Behaviors have a time limit
`run_behavior` SHALL block until the behavior ends, and MUST stop it with `stopBehavior` when it runs longer than 35 s. A behavior path MUST come from the configured catalogue and be installed on the robot.

#### Scenario: Behavior runs too long
- **WHEN** an installed behavior is still running after 35 s
- **THEN** the adapter stops it and returns a failed outcome with the reason `timeout`

#### Scenario: Behavior not installed
- **WHEN** `run_behavior` receives a path the robot does not list as installed
- **THEN** no behavior starts and the outcome names the missing path

### Requirement: Robot protections stay on
The real adapter SHALL NOT disable the NAOqi fall manager, external collision protection or smart stiffness. It MUST NOT set joint stiffness directly, except through `wakeUp`, `rest` and the posture and motion calls above.

#### Scenario: Forbidden protection call
- **WHEN** the full bridge lifecycle with motors on runs against a test double that fails on `setFallManagerEnabled`, `setExternalCollisionProtectionEnabled`, `setSmartStiffnessEnabled` and `setStiffnesses`
- **THEN** the run completes with no forbidden call

### Requirement: Startup with motors on
With motors on, startup SHALL connect, check the battery precondition, check that Autonomous Life is `disabled`, then call `wakeUp`, go to Stand, enable head tracking, compare the installed behaviors with the catalogue, subscribe to events, set the eyes white and speak the ready line. A failed `wakeUp` or Stand MUST stop startup, call `rest` and exit with a non-zero code. The bridge MUST NOT change Autonomous Life itself.

#### Scenario: Normal start
- **WHEN** the robot is seated at rest, the battery is reliable and above the minimum, and Autonomous Life is `disabled`
- **THEN** the robot wakes, stands, tracks heads, and the state is IDLE

#### Scenario: Stand fails
- **WHEN** `goToPosture("Stand")` returns false at startup
- **THEN** the adapter calls `rest`, the bridge exits with a non-zero code and the console names the failed step

#### Scenario: Missing behavior
- **WHEN** the catalogue lists `showcase/dance_robot` and the robot does not have it installed
- **THEN** startup logs a warning and `robot` is not in the `dance` enum

### Requirement: Shutdown with motors on
With motors on, shutdown SHALL stop behaviors and motion, go to Crouch, call `rest`, unsubscribe and disconnect. Each step MUST run even when an earlier step fails. This MUST also run on the second Ctrl+C, on SIGTERM and on an unhandled exception.

#### Scenario: Crouch fails at shutdown
- **WHEN** `goToPosture("Crouch")` raises during `quit`
- **THEN** the error is logged, `rest` is still called, and the bridge disconnects

### Requirement: Emergency stop with motors on
With motors on, the emergency stop SHALL call `stopAllBehaviors`, `stopMove` and `stopAll` on speech, then go to Crouch, with each step wrapped so one failure cannot skip the rest. It MUST interrupt keyframe playback.

#### Scenario: Stop during a dance
- **WHEN** the rear head is touched while a dance runs on the real robot
- **THEN** the behavior stops, the robot crouches, the eyes turn red and the state is STOPPED

#### Scenario: Stop during keyframes
- **WHEN** the operator presses `Ctrl+E` during keyframe playback
- **THEN** `ALMotion.killTasksUsingResources` stops the playback, the balancer is switched off and the robot crouches

### Requirement: Fall handling on the real robot
When the robot reports `robotHasFallen`, the bridge SHALL enter FALLEN, stop behaviors and motion, and raise an operator alert. It MUST NOT send a posture command until the operator resumes.

#### Scenario: Robot falls
- **WHEN** `robotHasFallen` fires during a step
- **THEN** the state is FALLEN, no posture command follows, and the console shows an alert
