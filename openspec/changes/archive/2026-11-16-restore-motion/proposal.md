## Why

The robot's battery is defective, so `add-qi-adapter` ships a real adapter that cannot command a motor. Dances, gestures, postures, head tracking, animated speech and package deploys are SPEC goals 1, 2, 5 and 6, and the fake robot already runs them through the safety gate. This change brings them to the real robot once a working battery is fitted, and keeps motors-off mode as a supported setting for any later hardware fault.

**Do not apply before the battery is replaced** and the new battery reports a believable charge. **Depends on `add-qi-adapter`** being archived, because this change modifies its `motors-off-mode` and `robot-init` capabilities.

## What Changes

- Implement the real motion methods over `qi`: behaviors, built-in animations, keyframe playback with the balancer, postures, `moveTo`, head angles, `wakeUp` and `rest`, with fixed speeds and NAOqi's own fall manager left on.
- Allow `motion.motors_enabled: true` with the real adapter. A real run with motors on refuses to start unless `robot.battery_reliable` is `true` and the battery is above `motion.battery_motion_min_pct`.
- Run the full startup, shutdown, emergency stop, idle crouch, battery and thermal routines from SPEC §6.3, §6.4, §10.3 and §18.3 on the real robot.
- Speak with `ALAnimatedSpeech` when motors are on, and add head tracking with `ALBasicAwareness`.
- Offer motion tools to the LLM again and use the full system prompt, from the existing motors-on code path.
- Add `naollm deploy`, which bumps the `manifest.xml` version, builds the package with `qipkg make-package` and installs it through the robot's `PackageManager` on the configured NAOqi port.
- Add a `choregraphe/showcase/` project skeleton (package id `showcase`). The behaviors in it are authored by people in Choregraphe.
- Extend `naollm init` with the motor steps when motors are on: SSH key install, package deploy, `--name`, and the Stand, Crouch and gesture checks in the self-test.
- Add `naollm anim preview <name> [--speed 0.5]`, which plays a keyframe animation slowly after operator confirmation.
- Keep motors-off mode working unchanged when `motion.motors_enabled` is `false`.

Out of scope: walking demos beyond the existing `turn` and `step` limits, new dances (people author them), and German (`restore-german`).

## Capabilities

### New Capabilities
- `qi-motion`: the real motion transport over `qi`, its startup and shutdown sequence with motors on, and the battery precondition for a real run.
- `package-deploy`: building and installing the Choregraphe package, `naollm deploy`, and password-free SSH access for deploys.

### Modified Capabilities
- `motors-off-mode`: the real adapter no longer always requires motors off. The guarantee that no motor is commanded now applies only when `motion.motors_enabled` is `false`.
- `robot-init`: init runs the motor steps when motors are on, and may set the robot name.
- `qi-robot-adapter`: adds animated speech and head tracking with motors on.
- `keyframe-animations`: the animation CLI gains `anim preview`.

## Impact

- **Code**: `robot/qi_adapter.py` (new `QiMotion`, the current class becomes `QiMotionDisabled`), `bridge.py` (real startup and shutdown with motors), `main.py` (`deploy`, `anim preview`), new `deploy/package.py`, `deploy/init.py`, `deploy/selftest.py`, `animations/player.py`, `tests/qi_double.py` (motion allowlist per mode).
- **Files**: new `choregraphe/showcase/manifest.xml` and `showcase.pml`.
- **Config**: `config.example.yaml` keeps `motors_enabled: false` until the operator has run the on-robot motion checklist.
- **Tools on the laptop**: `qipkg` (already in `.venv`), `ssh` and `ssh-copy-id`.
- **Hardware**: needs a working battery, the robot on the floor in the marked area, and an operator within reach for every on-robot task.
