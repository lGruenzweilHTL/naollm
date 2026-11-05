# admin-cli Specification

## Purpose

Defines the operator console that runs in the same process as the bridge: its modes, commands, hotkeys and the emergency stop paths, so the operator can test, steer and stop the robot at any time.

## Requirements

### Requirement: Modes
The command `naollm admin` SHALL start the robot connection and the operator prompt without the LLM. The command `naollm run` SHALL start the conversation loop together with the operator prompt. `--dry-run` SHALL use the fake robot for either mode. Until audio input exists, `naollm run` SHALL read visitor text from the operator prompt via a `chat <text>` command.

#### Scenario: Dry-run conversation
- **WHEN** the operator starts `naollm run --dry-run` and types `chat Kannst du winken?`
- **THEN** the text is sent to the LLM as a visitor turn and the robot actions are printed

#### Scenario: Admin mode has no LLM
- **WHEN** the operator starts `naollm admin --dry-run` and types `chat Hallo`
- **THEN** the console reports that the conversation loop is not available in admin mode

### Requirement: Status bar and event log
The console SHALL show a status line refreshed at least once per second with connection, state, posture, battery, thermal level, walking on/off and LLM on/off, and a scrolling event log with tool calls (marked `llm` or `admin`), gate rejections, sensor events and errors. In event mode the log MUST NOT contain spoken text or visitor text.

#### Scenario: Admin call logged
- **WHEN** the operator runs `tool play_gesture wave`
- **THEN** the event log shows the call with source `admin` and its result

### Requirement: Manual tools use the gate
The `tool <name> [args…]` command SHALL call the same handler and gate as the LLM. Arguments MUST be accepted positionally in schema order or as `key=value`. There MUST be no option to bypass the gate. Manual calls MAY run while the LLM is off.

#### Scenario: Positional arguments
- **WHEN** walking is enabled and the operator types `tool step forward 20`
- **THEN** a 20 cm forward step passes through the gate and runs

#### Scenario: Key-value arguments
- **WHEN** the operator types `tool look direction=left`
- **THEN** the head looks left

#### Scenario: Bad enum
- **WHEN** the operator types `tool dance tango` and `tango` is not configured
- **THEN** the console shows the gate's error and the valid choices, and nothing runs

### Requirement: Commands
The console SHALL provide `help`, `status`, `tools`, `tool`, `say "<text>"`, `posture <Stand|Sit|Crouch>`, `wake`, `rest`, `walk on|off`, `llm on|off`, `reset`, `estop`, `resume`, `listen`, `chat`, `quit`, and tab completion for commands, tool names and enum values.

#### Scenario: Walking toggle
- **WHEN** the operator types `walk on`
- **THEN** walking is enabled for both admin and LLM calls and the status line shows walking ON

#### Scenario: Unknown command
- **WHEN** the operator types `jump`
- **THEN** the console says the command is unknown and suggests `help`

### Requirement: Emergency stop hotkey and command
`Ctrl+E` SHALL trigger the emergency stop at any time, including while text is being typed, without pressing Enter. The `estop` command SHALL trigger the same routine.

#### Scenario: Hotkey while typing
- **WHEN** the operator has typed `tool da` and presses `Ctrl+E`
- **THEN** the emergency stop runs and the state is `STOPPED`

### Requirement: Resume requires confirmation
`resume` SHALL ask for `y` confirmation before leaving `STOPPED` or `FALLEN`. Any other answer MUST keep the current state.

#### Scenario: Resume declined
- **WHEN** the operator types `resume` and answers `n`
- **THEN** the state stays `STOPPED`

### Requirement: Two-stage Ctrl+C and termination signals
The first `Ctrl+C` SHALL run the emergency stop, keep the console running and print "Press Ctrl+C again within 3 s to quit". A second `Ctrl+C` within 3 s SHALL run the safe shutdown and exit. `SIGTERM`, closing the terminal, and an unhandled exception SHALL run the emergency stop, then the safe shutdown, then exit. The console MUST never exit without running the emergency stop first.

#### Scenario: Single Ctrl+C
- **WHEN** the operator presses `Ctrl+C` once
- **THEN** the state is `STOPPED` and the console is still running

#### Scenario: Double Ctrl+C
- **WHEN** the operator presses `Ctrl+C` twice within 3 s
- **THEN** the safe shutdown runs (stop, `Crouch`, rest, disconnect) and the process exits

#### Scenario: SIGTERM
- **WHEN** the process receives `SIGTERM`
- **THEN** the emergency stop and the safe shutdown run before exit

### Requirement: Listen hotkey only on an empty line
`space` SHALL start listening only when the prompt line is empty. With text on the prompt line, `space` MUST insert a space.

#### Scenario: Space in a command
- **WHEN** the operator types `tool` and then presses `space`
- **THEN** a space is inserted and listening does not start

#### Scenario: Space on an empty line
- **WHEN** the prompt line is empty and the operator presses `space`
- **THEN** listening is requested

### Requirement: No network exposure
The console SHALL bind no network ports. It MUST be reachable only from the terminal it runs in.

#### Scenario: No listening sockets
- **WHEN** `naollm admin --dry-run` runs
- **THEN** the process has no listening sockets
