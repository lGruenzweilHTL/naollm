## MODIFIED Requirements

### Requirement: Real adapter never commands a motor
With `motion.motors_enabled: false`, the real adapter SHALL NOT call any NAOqi method that can command a motor or change stiffness. This includes `ALMotion` commands, `ALRobotPosture.goToPosture`, `ALBehaviorManager` start calls, `ALAnimationPlayer`, `ALAnimatedSpeech` and `ALBasicAwareness`. Motion adapter methods that command the robot MUST raise a motors-disabled error without calling NAOqi. The adapter MUST choose its motion implementation once at connect time, from the configuration.

#### Scenario: Forbidden call in the test double
- **WHEN** the full bridge lifecycle runs with motors off against a test double that fails on any NAOqi method outside the read-only allowlist
- **THEN** the run completes with no forbidden call

#### Scenario: Direct motion call
- **WHEN** code calls `go_to_posture("Stand")` on the real adapter with motors off
- **THEN** a motors-disabled error is raised and no NAOqi method is called

## REMOVED Requirements

### Requirement: Real adapter requires motors off
**Reason**: The battery is replaced and the real adapter now implements motion (`qi-motion`).
**Migration**: A real run with `motion.motors_enabled: true` is allowed when `robot.battery_reliable` is `true`; see "Real run with motors needs a reliable battery" in `qi-motion`. Set `motion.motors_enabled: false` to keep the old behavior.
