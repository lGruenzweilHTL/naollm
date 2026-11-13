## ADDED Requirements

### Requirement: Game is off unless enabled
The bridge SHALL offer `guess_age` only when `privacy.age_game_enabled` is `true` and the robot's age estimation probe succeeded at startup. Otherwise the tool MUST NOT be sent to the LLM, and the gate MUST reject it with the reason `age_game_off` for every source.

#### Scenario: Default configuration
- **WHEN** the bridge starts with the default configuration
- **THEN** `guess_age` is not in the tool list sent to the LLM

#### Scenario: Enabled but service missing
- **WHEN** the game is enabled and `ALFaceCharacteristics` is not available
- **THEN** the bridge starts, the tool is not offered, and the console shows an alert

### Requirement: Age only, from one person
The estimate SHALL use only the age value and its confidence for one person. The adapter MUST NOT read, return or log gender, smile or expression values. The tool MUST refuse when the people count is not exactly 1, and when the confidence is below 0.5.

#### Scenario: Two people visible
- **WHEN** a visitor asks for a guess and two people are visible
- **THEN** the tool returns `ok: false` with a reason the robot can say, and no estimate is taken

#### Scenario: Low confidence
- **WHEN** the estimate comes back with confidence 0.3
- **THEN** the robot says it cannot see the visitor well enough, and no range is spoken

#### Scenario: Forbidden keys
- **WHEN** the adapter runs an estimate against a test double that fails on reads of `GenderProperties`, `SmileProperties` or `ExpressionProperties`
- **THEN** the estimate completes with no forbidden read

### Requirement: The number never reaches the LLM
The handler SHALL speak the range itself, in the turn language, as a 5-year range such as "10 to 15". The tool result sent to the LLM MUST contain no number. The estimate MUST NOT be stored, added to history or written to any log, also outside event mode.

#### Scenario: Successful guess
- **WHEN** the estimate is 12 with confidence 0.7
- **THEN** the robot says a range of 10 to 15, and the tool result is `{"ok": true, "result": "told the visitor a rough age range"}`

#### Scenario: Log after a guess
- **WHEN** a guess completes in debug mode
- **THEN** the log names the tool and the outcome, and contains no age value

### Requirement: Game is visible to the operator
While the game is enabled, the console SHALL show a warning at startup, and the status bar MUST show that the age game is on. `notice` MUST say that NAO can guess an age when asked, and that the guess is not stored.

#### Scenario: Notice with the game on
- **WHEN** the operator runs `notice` with the game enabled
- **THEN** the spoken notice mentions the age game and that nothing is stored

### Requirement: Tool budget with the game on
With the game enabled, configuration loading SHALL reject `motion.walking_enabled: true`, and `walk on` MUST refuse with a message naming the age game, so at most 10 tools are sent.

#### Scenario: Walk on with the game enabled
- **WHEN** the operator types `walk on` with the game enabled
- **THEN** the console names the age game and walking stays off
