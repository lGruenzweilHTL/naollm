## Context

`add-qi-adapter` built the real adapter with a hard guarantee: `QiMotion` raises `MotorsDisabled` for every command, and `tests/qi_double.py` fails on any NAOqi call outside a read-only allowlist. The bridge, the gate, the tools, the keyframe player and the motors-on system prompt all exist and are tested against the fake robot. What is missing is the transport for motion, the motors-on startup and shutdown on the real robot, `naollm deploy`, the Choregraphe project and the motor steps of `naollm init`.

Facts from `spike/FINDINGS.md`: NAOqi 2.8.5.11, Python 3.12 with `qi` 3.1.5, `qipkg` from qibuild 3.18 in `.venv`. No behavior, `ALAnimatedSpeech`, `wakeUp` or deploy has ever run on this robot. The old battery reported 100 % whatever its charge.

## Goals / Non-Goals

**Goals:**
- Real motion that passes the same gate as today, with fixed speeds and NAOqi's protections left on.
- A real run with motors on only when the battery reading can be trusted.
- Motors-off mode stays a supported setting with its current guarantee.
- One-command package deploy, and motor steps in `naollm init`.

**Non-Goals:**
- Authoring dances. People build them in Choregraphe.
- New walking features beyond `turn` and `step`.
- Python 2 shim or ROS 2 transport.

## Decisions

**Two motion classes, chosen at connect.** `QiMotionDisabled` is today's class, renamed. `QiMotion` is the real one. `QiRobot.connect` picks one from `motion.motors_enabled`, and the choice cannot change during a session. *Alternative:* one class with a flag checked in each method. Rejected, because one missed check sends a motor command, while a class that has no motor code cannot.

**`qi_double` gets two allowlists.** The motors-off allowlist stays exactly as it is. A second allowlist covers the motion mapping, and a deny list holds the protection calls (`setFallManagerEnabled`, `setExternalCollisionProtectionEnabled`, `setSmartStiffnessEnabled`, `setStiffnesses`). This deny list applies in both modes. The lifecycle tests then run once per mode.

**Battery trust is a start condition, not a gate rule.** A real run with motors on needs `robot.battery_reliable: true`. The gate's battery thresholds stay as they are. *Alternative:* allow motors with an unreliable battery and turn off the battery rules. Rejected, because the battery rules are the only guard against a brown-out fall mid-dance.

**Autonomous Life stays the operator's job.** Startup with motors on still refuses unless Autonomous Life is `disabled`, and only `naollm init` changes it. This keeps one rule for both modes.

**Interrupting keyframes.** `angleInterpolationBezier` is started with `_async=True`, and the future is kept. The emergency stop calls `ALMotion.killTasksUsingResources` on the animation's joints, then waits on the future. *Alternative:* `ALMotion.killAll`. Rejected, because it also kills tasks the posture call needs straight afterwards. Verify on the robot in task 7.

**Behavior time limit.** `runBehavior` runs async, and the adapter waits up to 35 s, which is the 30 s rule of SPEC §9 plus margin. On timeout it calls `stopBehavior` with that path.

**SSH with `ssh-copy-id`.** The system tool asks for the password on the terminal, so the bridge never handles it. *Alternative:* `paramiko` with a password prompt in the CLI. Rejected, because it adds a dependency and puts the password in our process.

**Deploy bumps the version before building, and undoes the bump on failure.** The manifest is edited with `xml.etree`, keeping the file's other content. *Alternative:* bump after a successful install. Rejected, because `qipkg` reads the version from the manifest while it builds.

**Own install instead of `qipkg deploy-package`.** On NAOqi 2.8.7 the robot listens on port 9561. `qipkg deploy-package` connects to a hardcoded `tcp://<host>:9559` for the `PackageManager` call, so the install failed with `Connect error: 111: Connection refused`, and `qipkg` still exited with 0. The deploy now does the same steps itself: `scp -o BatchMode=yes` to `nao@<host>:`, then over the configured NAOqi URL `PackageManager.removePkg("showcase")` and `PackageManager.install("/home/nao/<pkg>")`, which must return `true`, then `ssh rm -f` of the copy. Install and verify share one `qi` connection, because `qi.Application` exists once per process. `qipkg make-package` still builds the package. *Alternatives:* patch the port in the installed `qipkg` (rejected: lost on every reinstall, and the exit code still hides a failed install); call `qipkg deploy-package` and rely on the verify step (rejected: a stale package with the same behaviors passes the verify, and the bump stays).

**Animated speech only with motors on.** `say(animated=True)` maps to `ALAnimatedSpeech` only in `QiMotion` mode. Speech-before-motion already holds in the loop, so gestures from speech and motion tools never overlap.

## Risks / Trade-offs

- [First motion on a robot whose motors have not moved for months] → Task 7 is a staged checklist: wake and rest seated, then Stand, then one gesture, each with an operator's hands near the robot and on a mat.
- [New battery also reads wrongly] → The operator compares the reading with the charger LED before setting `battery_reliable: true`; this is a checklist step.
- [`killTasksUsingResources` does not stop Bezier playback cleanly] → Fall back to `ALMotion.stopMove` plus `killAll`, followed by Crouch, and record the result in the findings.
- [A deployed behavior is set to auto-start and runs on boot] → The deploy refuses behaviors with an auto-start trigger.
- [Motors-on code paths have only fake-robot coverage today] → The lifecycle tests run against `qi_double` in both modes.

## Migration Plan

1. Fit the new battery, check its reading, and set `robot.battery_reliable: true`.
2. Apply this change with `motion.motors_enabled: false` still in the config, and run the tests.
3. Run the on-robot checklist (task 7) with motors on.
4. Only after that, set `motors_enabled: true` in `config.example.yaml` and remove the "Motors off" part of the SPEC "Interim" section.

Rollback: set `motion.motors_enabled: false`. Motors-off mode is unchanged.

## Open Questions

- Does `ALBasicAwareness` on 2.8.5.11 accept a head-only tracking mode without moving the body?
- Which built-in `ALAnimationPlayer` paths exist on this robot image for the default gestures?
