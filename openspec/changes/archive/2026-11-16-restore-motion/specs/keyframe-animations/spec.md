## ADDED Requirements

### Requirement: Animation preview
`naollm anim preview <name> [--speed <factor>]` SHALL play one validated animation on the robot through the safety gate, with every keyframe time divided by the factor. The factor MUST be greater than 0 and at most 1, and MUST default to 0.5. The command MUST ask the operator to confirm that hands are near the robot before it plays, and MUST refuse with motors off. Preview MUST accept an animation without `reviewed_by`, because preview is how it gets reviewed, and MUST refuse one that fails validation.

#### Scenario: Slow preview
- **WHEN** `naollm anim preview victory --speed 0.5` runs and the operator confirms
- **THEN** the keyframes play with every time doubled, through the gate

#### Scenario: Factor out of range
- **WHEN** the factor is 1.5
- **THEN** the command exits with a non-zero code and nothing plays

#### Scenario: Operator declines
- **WHEN** the operator does not confirm
- **THEN** nothing plays and the exit code is non-zero
