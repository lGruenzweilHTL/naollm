# showcase

The Choregraphe project for the robot's custom behaviors (SPEC §9). Package id `showcase`.

- Open `showcase.pml` in Choregraphe and add one behavior per animation. Name its folder after the tool enum entry, for example `dance_macarena` or `gesture_bow`, and map it in `config.yaml` as `showcase/dance_macarena`.
- Each behavior starts and ends in Stand, finishes within 30 s, uses no speech or speech recognition boxes, and has no auto-start trigger. `naollm deploy` refuses a behavior with an auto-start trigger.
- Install with `naollm deploy`. It bumps the patch part of the version in `manifest.xml`, builds with `qipkg make-package`, installs with `qipkg deploy-package`, and checks the installed behaviors against the config. It needs `motion.motors_enabled: true`.

There are no behaviors yet: people author them in Choregraphe.
