## ADDED Requirements

### Requirement: Showcase package project
The repository SHALL contain a Choregraphe project at `choregraphe/showcase/` with `showcase.pml` and `manifest.xml`, package id `showcase`. Each behavior MUST live in a folder named after its tool enum entry, such as `dance_macarena` or `gesture_bow`, and MUST NOT be set to auto-start.

#### Scenario: Auto-start behavior
- **WHEN** `naollm deploy` finds a behavior in `manifest.xml` with an auto-start trigger
- **THEN** the deploy stops before building and names the behavior

### Requirement: Deploy command
`naollm deploy [--robot <ip-or-hostname>]` SHALL bump the patch part of the `manifest.xml` version, build the package with `qipkg make-package`, install it, and compare the installed behaviors with the configured catalogue. The install MUST copy the package to the `nao` user's home with `scp` in batch mode, connect to NAOqi at the configured `robot.url` when `--robot` is the configured robot and at port 9561 otherwise, remove the previous `showcase` package through `PackageManager.removePkg`, install the copy through `PackageManager.install`, and delete the copy over `ssh` whether the install worked or not. It MUST NOT use `qipkg deploy-package`, which connects to a fixed port 9559 and exits with 0 when the install fails. It MUST print a pass or fail line for each step and exit with a non-zero code when a step fails. `--robot` MUST default to the host in `robot.url`.

#### Scenario: Successful deploy
- **WHEN** `naollm deploy` runs and every configured `showcase/` behavior is installed afterwards
- **THEN** the version in `manifest.xml` is one patch higher and the exit code is 0

#### Scenario: Missing behavior after deploy
- **WHEN** the config maps `robot` to `showcase/dance_robot` and the robot does not list it after the install
- **THEN** the verify step fails and names `showcase/dance_robot`

#### Scenario: Build fails
- **WHEN** `qipkg make-package` exits with an error
- **THEN** nothing is sent to the robot, the version bump is undone and the exit code is non-zero

#### Scenario: Install fails
- **WHEN** the copy fails, the NAOqi connection is refused, or `PackageManager.install` returns `false` or raises
- **THEN** the install step fails and names the cause, the version bump is undone, the verify step does not run and the exit code is non-zero

#### Scenario: Configured port
- **WHEN** `robot.url` is `tcp://10.0.0.251:9561` and `naollm deploy` runs without `--robot`
- **THEN** the install connects to `tcp://10.0.0.251:9561`

### Requirement: Deploy needs motors on
`naollm deploy` SHALL refuse when `motion.motors_enabled` is `false`, because an installed behavior can be started by the robot itself.

#### Scenario: Motors off
- **WHEN** `naollm deploy` runs with `motion.motors_enabled: false`
- **THEN** it exits with a non-zero code before building, and says motors are off

### Requirement: Password-free SSH for deploys
Init SHALL install the laptop's SSH public key on the robot for the `nao` user, using the system `ssh-copy-id`, which asks for the password itself. The bridge MUST NOT read, store or log the robot password. When key login already works, the step MUST pass without asking.

#### Scenario: Key already installed
- **WHEN** `ssh -o BatchMode=yes nao@<robot> true` succeeds
- **THEN** the step passes and `ssh-copy-id` is not run

#### Scenario: No public key on the laptop
- **WHEN** `~/.ssh` contains no public key
- **THEN** the step fails and prints the `ssh-keygen` command to create one
