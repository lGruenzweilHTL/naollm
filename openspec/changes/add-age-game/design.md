## Context

`privacy.age_game_enabled` exists in the config (default `false`) and does nothing yet. SPEC §8.6 allows the game only when enabled and announced on signage, triggered only by an explicit request, spoken once as a rough range, never stored and never added to history in numeric form. `add-faces-consent` brings `QiPerception`, the people count and the startup probe pattern. The robot runs NAOqi 2.8.5.11, and `ALFaceCharacteristics` has never been probed on it.

## Goals / Non-Goals

**Goals:**
- A guess that only an explicit request can start, for exactly one visible person.
- The age value never reaches the LLM, the history or a log.
- An operator who can always see that the game is on.

**Non-Goals:**
- Gender, smile, expression or emotion values, in any form.
- Storing or improving estimates.
- Recognising who the visitor is.

## Decisions

**The handler speaks the range, not the LLM.** The tool calls `robot.speech.say` with the range and returns a result without a number. *Alternative:* return the range to the LLM and strip it from history afterwards. Rejected, because the number would still sit in the request, the model could repeat or mangle it, and stripping is easy to get wrong.

**5-year ranges with a floor at 5.** The estimate is rounded down to a multiple of 5 and spoken as "N to N+5". A coarse range is kinder and closer to what the model can know. Estimates under 5 or over 80 are spoken as "very young" or "very grown-up" without a number.

**Exactly one person, confidence ≥ 0.5.** With several people visible, the estimate could belong to a bystander who never asked. The threshold is a constant and is retuned in task 6 if the robot reports confidences on another scale.

**Walking and the game are exclusive.** With motors on, walking on and the game on, 11 tools would be sent, above the limit of 10 (`llm-tools`). *Alternative:* merge `turn` and `step` into one tool. Rejected, because that changes the walking tools for everyone to make room for an optional game.

**Probe at startup.** As in `add-faces-consent`, startup checks that `ALFaceCharacteristics` exists and that `analyzeFaceCharacteristics` is callable. A failed probe hides the tool and raises an alert.

**Emotion side effects (legal blocker).** NAOqi documents `analyzeFaceCharacteristics` as writing age, gender, smile and expression values for the person into `ALMemory` in one call. Our code reads only the age, but the robot would still compute expression values. EU AI Act Art. 5(1)(f) forbids using AI systems that infer emotions in education institutions, and a school showcase is inside a school. Whether computing a value that nobody reads counts as "use" is a legal question this change cannot answer. Task 1 checks on the robot what one call writes. If it writes expression values, the change stops after task 1. It goes on only if the school's data-protection contact clears it in writing, or if a call is found that computes age only.

## Risks / Trade-offs

- [Estimates are wrong for children, and a child is told they look much older] → Coarse ranges, a playful framing in the spoken line, and an eval case for asking again.
- [The LLM calls `guess_age` without a request] → Prompt rule plus eval cases in `age_game_on` that expect no tool for "Wie alt bist du?" and similar questions about the robot.
- [The game is left on by accident] → Default off, a console warning at startup, and a status bar flag.
- [Art. 5 or Art. 50 duties are misjudged] → The legal blocker above. The school clears the plan before an event (SPEC §12), and the signage announces the game.

## Migration Plan

Off by default, so nothing changes for existing configurations. Rollback: set `privacy.age_game_enabled: false`.

## Open Questions

- Does `ALFaceCharacteristics` exist on 2.8.5.11 for NAO 6, and what does one `analyzeFaceCharacteristics` call write to `ALMemory`?
- Who at the school signs off on the game (SPEC §17, question 5)?
