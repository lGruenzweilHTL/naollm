## MODIFIED Requirements

### Requirement: Tool set and result format
The bridge SHALL offer the tools `play_gesture`, `dance`, `set_posture`, `look`, `turn`, `step`, `set_eyes`, `get_status`, `who_is_here` and `stop` (SPEC §8.2), and `guess_age` when the age game is enabled (SPEC §8.6). Every tool result MUST be either `{"ok": true, "result": "<short outcome>"}` or `{"ok": false, "error": "<short reason>"}`.

#### Scenario: Successful gesture
- **WHEN** `play_gesture` is called with `wave` and all gate checks pass
- **THEN** the result is `{"ok": true, "result": ...}` and the configured `wave` target is played

#### Scenario: Age game enabled
- **WHEN** `privacy.age_game_enabled` is `true` and the probe succeeded
- **THEN** the tool list contains `guess_age`, with no parameters
