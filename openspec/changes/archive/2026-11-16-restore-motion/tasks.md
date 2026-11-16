## 1. Preconditions

- [x] 1.1 Confirm `add-qi-adapter` is archived and the battery is replaced; stop here otherwise
- [ ] 1.2 With the robot seated at rest, compare the battery reading with the charger LED over one charge cycle, and record the result in `spike/FINDINGS.md`

## 2. Real motion transport

- [x] 2.1 Rename the current `QiMotion` to `QiMotionDisabled`, and let `QiRobot.connect` pick the motion class from `motion.motors_enabled`
- [x] 2.2 Implement `QiMotion` with the SPEC §6.2 mapping and fixed speed constants
- [x] 2.3 Add the 35 s behavior time limit with `stopBehavior`, and reject paths that are not installed
- [x] 2.4 Play keyframes async through `angleInterpolationBezier`, interruptible with `killTasksUsingResources`
- [x] 2.5 Map `say(animated=True)` to `ALAnimatedSpeech` with motors on, and make `stop_speech` stop both services
- [x] 2.6 Implement head tracking with `ALBasicAwareness`, stopped around motion tools and after the emergency stop
- [x] 2.7 Split `tests/qi_double.py` into the motors-off allowlist, a motion allowlist and a protection deny list that applies in both modes

## 3. Bridge lifecycle with motors on

- [x] 3.1 Remove the start refusal for motors on, and add the `robot.battery_reliable` and minimum battery refusals before any motor command
- [x] 3.2 Run the motors-on startup sequence on the real adapter, with `rest` and a non-zero exit on a failed wake or Stand
- [x] 3.3 Make the shutdown, emergency stop, idle crouch, battery, thermal and fall routines run their motion steps on the real adapter
- [x] 3.4 Run the lifecycle tests against `qi_double` in both modes, and add the scenarios from the `qi-motion` spec

## 4. Package deploy

- [x] 4.1 Create the `choregraphe/showcase/` skeleton: `showcase.pml` and `manifest.xml` with package id `showcase`, no behaviors yet
- [x] 4.2 Add `deploy/package.py`: manifest version bump with undo, auto-start check, `qipkg make-package`, install, verify against the catalogue
- [x] 4.3 Add `naollm deploy [--robot]`, refusing with motors off
- [x] 4.4 Unit-test the bump, the undo on a failed build and the verify step with a stubbed `qipkg`
- [x] 4.5 Replace `qipkg deploy-package` with scp, `PackageManager.removePkg` and `install` on the configured NAOqi port and an `ssh rm` of the copy; fail the step and undo the bump on any error; test success, a refused connection, `install` returning `false` and the port

## 5. Init and animation preview

- [x] 5.1 Add the SSH access step with `ssh-copy-id`, and the package deploy step, run only with motors on
- [x] 5.2 Allow `--name` and set the robot name only when it is given
- [x] 5.3 Add the Stand, Crouch and gesture checks to the self-test, behind a clear-area confirmation
- [x] 5.4 Add `naollm anim preview <name> [--speed]` with the factor check, operator confirmation and the gate
- [x] 5.5 Test the new init steps and the preview command against the fake robot

## 6. Docs

- [x] 6.1 Update the README: motor setup, `naollm deploy`, the e-stop limitation, and the robot-on-the-floor rule
- [x] 6.2 Update the SPEC "Interim" section: remove the motors-off part once task 7 has passed, and keep the motors-off setting documented

## 7. On the robot (operator present, robot on the floor on a mat)

- [x] 7.1 Ask the user to confirm the robot is on a mat in a clear area, with an operator within reach, before any motor command
- [x] 7.2 Seated: `wake` and `rest`. Then Stand and Crouch through `posture`
- [x] 7.3 Emergency stop from each trigger while standing, during a gesture and during keyframe playback; record whether `killTasksUsingResources` stops the playback
- [x] 7.4 Deploy one test behavior with `naollm deploy`, run it, and stop it mid-way with the rear head touch
- [x] 7.5 Run `naollm init` with motors on, and record the self-test results and any `ALBasicAwareness` and `ALAnimationPlayer` findings in `spike/FINDINGS.md`
- [x] 7.6 Set `motion.motors_enabled: true` in `config.example.yaml` (the user turned motors on before 7.1 to 7.5 were recorded)
