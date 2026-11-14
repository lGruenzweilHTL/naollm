## ADDED Requirements

### Requirement: Dance cooldown
The gate SHALL reject `dance` with the reason `dance_cooldown` when the previous dance ended less than `motion.dance_cooldown_s` ago (default 120 s, 0 turns the rule off). The rule MUST apply to the LLM and the operator alike, and the result MUST say how many seconds are left. Other motion tools MUST NOT be affected.

#### Scenario: Second dance too soon
- **WHEN** a dance ended 30 s ago and the LLM calls `dance` with the default setting
- **THEN** the gate returns `ok: false` with reason `dance_cooldown` and about 90 s left

#### Scenario: Gesture during cooldown
- **WHEN** a dance ended 30 s ago and the LLM calls `play_gesture` with `wave`
- **THEN** the gesture runs

#### Scenario: Rule off
- **WHEN** `motion.dance_cooldown_s` is 0
- **THEN** a second dance runs right after the first
