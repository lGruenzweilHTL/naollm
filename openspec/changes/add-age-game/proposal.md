## Why

SPEC §8.6 describes an optional age-guess game: a visitor asks NAO to guess their age, and NAO says a rough range once. It is a crowd-pleaser, but it is biometric categorisation of visitors who include minors. That brings transparency duties under EU AI Act Art. 50 and GDPR concerns (SPEC §12.1), so it stays off by default, and it needs its own reviewable change with the privacy rules spelled out.

**Depends on `add-faces-consent`** (real perception and people count on the robot). Needs no motors. **Compliance gate:** the school and the club supervisor must clear the game before it is enabled at an event, and the first task checks a legal blocker on the robot (see design.md, "Emotion side effects").

## What Changes

- Add a `guess_age` tool, offered to the LLM only when `privacy.age_game_enabled` is `true`.
- The tool reads only the age estimate for the single visible person from `ALFaceCharacteristics`, never gender, smile or expression. It refuses when nobody or more than one person is visible, or when the confidence is low.
- The bridge speaks the range itself, in 5-year steps, and the LLM gets a result without the number. The number never enters the LLM context, the history or the logs.
- The system prompt gains one rule when the game is on: guess an age only through the tool, and only when the visitor asks for it.
- With the game on, `motion.walking_enabled` and `walk on` are refused, so no more than 10 tools are sent.
- The console shows a persistent "age game on" warning, the status bar shows it, and `notice` mentions the game.
- Eval cases in a new `age_game_on` profile.

Out of scope: gender estimation and emotion or expression recognition, which stay excluded (SPEC §8.5, §12.1). Also out of scope: the consent form and the signage poster, which are paper artifacts.

## Capabilities

### New Capabilities
- `age-game`: when the game is available, how the estimate is taken and spoken, and what never leaves the robot or reaches the LLM.

### Modified Capabilities
- `llm-tools`: the tool set gains `guess_age` when the game is enabled.
- `conversation-loop`: the system prompt rules gain the age-game rule when the game is enabled.

## Impact

- **Code**: `robot/adapter.py` (new `estimate_age`), `robot/qi_adapter.py`, `robot/fake_adapter.py`, `tools/schemas.py`, `tools/registry.py`, `tools/handlers.py`, `tools/messages.py`, `llm/prompts.py`, `config.py`, `admin/commands.py` (`walk`, `notice`, status bar).
- **Eval**: `tests/eval/tool_calls.yaml`, new `tests/eval/profiles/age_game_on.yaml`.
- **Compliance**: signage must announce the game (SPEC §12.3). The school's data-protection sign-off (SPEC §17, question 5) is needed before an event with the game on.
