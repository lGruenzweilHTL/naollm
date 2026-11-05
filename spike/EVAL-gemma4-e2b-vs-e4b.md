# Tool-call eval: gemma4:e2b vs gemma4:e4b

Run on 2026-11-05, 21:34 to 21:39, with `naollm eval -v` and the 41 cases in `tests/eval/tool_calls.yaml`.

## Setup

| Item | Value |
| --- | --- |
| Laptop | Intel i5-1135G7, 8 threads, 15 GiB RAM, Iris Xe; CPU inference only |
| Power | On battery (discharging), governor `powersave`, profile `balanced` |
| Ollama | 0.40.14, `/v1` chat completions, models loaded before the first case |
| Config | Local `config.yaml` with only `llm.model` changed: temperature 0.4, `max_tokens` 200, `reasoning_effort: none` |
| Profiles | `event_default` (31 cases) and `walking_on` (10 cases), both with motors on, on the fake robot |
| Runs | One run per model. With temperature 0.4, a second run can differ by a case or two |

## Result

| | gemma4:e2b | gemma4:e4b |
| --- | --- | --- |
| `event_default` | 26/31 (84 %), **target missed** | 29/31 (94 %), **target met** |
| `walking_on` | 10/10 (100 %) | 10/10 (100 %) |
| Model load | 9.2 s | 13.2 s |
| Wall time, 41 cases | 110 s | 199 s |
| Battery used | 91 % to 87 % | 87 % to 81 % |
| Errors, timeouts, empty replies, cut-off replies | 0 | 0 |
| Cases with reasoning | 0 | 0 |

`reasoning_effort: none` worked for both models: no case produced reasoning, so the first token is always the first answer token.

## Timing

All times are from sending the request. "First text" is when the robot could start to speak.

| Metric | e2b mean | e2b median | e2b p90 | e2b max | e4b mean | e4b median | e4b p90 | e4b max |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Response | 2.5 s | 2.2 s | 3.6 s | 8.0 s | 4.5 s | 3.6 s | 5.0 s | 15.1 s |
| First token | 1.3 s | 0.8 s | 1.3 s | 7.0 s | 2.2 s | 1.2 s | 2.0 s | 12.4 s |
| First text | 1.1 s (n=26) | 0.8 s | 2.3 s | 6.2 s | 1.9 s (n=38) | 1.2 s | 2.0 s | 12.4 s |
| First tool call | 1.4 s (n=27) | 1.0 s | 1.3 s | 7.0 s | 2.0 s (n=29) | 1.3 s | 2.0 s | 12.2 s |

| | e2b | e4b |
| --- | --- | --- |
| Output tokens per case | 23 | 29 |
| Input tokens per case | 622 | 622 |
| Generation rate | 15.2 tokens/s | 12.0 tokens/s |

**Cold prompts.** The slow cases are the first case after the system prompt or the tool list changes: case 1 (German), case 22 (English), case 32 (walking on) and case 38 (English, walking on). There, Ollama has to read all 570 to 720 input tokens again:

| Case | e2b first token | e4b first token |
| --- | --- | --- |
| 1, first German | 6.2 s | 11.5 s |
| 22, first English | 5.9 s | 12.4 s |
| 32, walking on, German | 7.0 s | 12.2 s |
| 38, walking on, English | 6.6 s | 11.9 s |
| All other cases (median) | about 0.8 s | about 1.2 s |

At the booth, the prompt stays the same between turns in one language, so only the first turn, a language change or `walk on|off` pays this. e4b's 12.4 s worst case is under the bridge's 20 s `llm.timeout_s`, but not by much on battery.

**Speed in short.** Warm, e4b takes about 0.4 s longer than e2b to the first word and about 1.4 s longer for the whole reply. Most of the reply-time gap comes from e4b's longer replies (29 vs 23 tokens) and its lower generation rate.

## Failures

| Case | e2b | e4b |
| --- | --- | --- |
| "Verbeug dich vor dem Publikum." | `set_posture(Crouch)` while saying "Ich verbeuge mich" | pass |
| "Nick mal mit dem Kopf." | pass | `look(down)` while saying "Ich nicke" |
| "Kannst du deine Augen orange machen?" | no tool, only offers to do it | pass |
| "Wie voll ist dein Akku?" | no tool, says "Ich werde jetzt meinen Status überprüfen" | pass |
| "Stopp! Hör sofort auf!" | no tool, says "Ich stoppe sofort!" | no tool, says it will take a break |
| "Stop moving!" | no tool, says "I will stop moving right now" | pass |

Both models miss the German "Stopp!" case. e2b also misses the English one.

## What the score does not show

- **e2b says it acts when it doesn't.** In four of its five failures, e2b claims the action ("Ich stoppe sofort!", "Ich werde jetzt meinen Status überprüfen", "Ich verbeuge mich") without the matching tool call. A visitor hears a promise that the robot does not keep. e4b does this once ("Ich nicke" with `look(down)`).
- **e4b answers before the tool result.** For "How is your battery?", e4b calls `get_status` and at once says "I am currently running at a good level", before the result is back. On this robot, the battery value is hidden because it is wrong, so that sentence is invented.
- **e4b can produce emoji and Markdown.** The joke reply was "Weil er die Sonne \*scannt\*! 😊". The sentence splitter passes it through as two sentences, "Weil er die Sonne \*scannt\*!" and "😊", so the robot's TTS would get the asterisks and the emoji. The eval does not score this.
- **e2b underclaims.** It refused the joke ("Ich kann leider keine Witze erzählen"), and its German was weaker ("die 'robot'-Tanz"). e4b told the joke, and offered both dances by name for "Kannst du tanzen?".
- **Over-limit requests.** Both models sent `turn(360)` and `step(500 cm, left)`, which the gate rejects (limits 90° and 30 cm), so the robot then explains its limit. e2b also sent `step(100 cm)` for "Lauf zehn Meter". e4b handled "Lauf zehn Meter" well: "Ich kann nur kleine Schritte machen … Soll ich das tun?". All pass under the `any_of` rule, as intended.
- **Silent actions.** e2b called a tool with no text in 15 of its 27 tool cases, including 6 of the 7 English `event_default` ones. The robot then acts without saying anything. e4b did this in 3 of 29, all in the English walking cases.
- **Language.** Both models always answered in the case's language.

## Relevance to the robot today

The eval runs with motors on, but the real robot runs with motors off. There, the LLM gets only `set_eyes`, `get_status` and `who_is_here`, and a different system prompt. On the cases that use those three tools, e4b passed 6 of 6, and e2b passed 4 of 6: it missed the eye-colour question and the battery question. Both passed all 10 cases that expect no tool. The eval has no motors-off profile, so this is a hint, not a measurement.

## Recommendation

Keep **gemma4:e4b** as the default. It meets the 90 % target in both profiles, passes every motors-off-relevant case, rarely claims an action it does not take, and speaks better German. Warm, it starts answering in about 1.2 s (median), which is acceptable next to speech-to-text and TTS. e2b is about 1.6 times faster but misses the target, and its failures are the bad kind: it says it did something it did not do.

## Possible follow-ups

Not done, because they are new work:

1. Strip emoji and Markdown (`*`, `_`, `#`) from sentences before TTS.
2. Tell the model in the system prompt not to state results before a tool returns, or check for this in the eval.
3. Add a `motors_off` eval profile, so the score matches the robot as it runs today.
4. Warm the prompt cache at `naollm run` start with one short request per configured language, so the first visitor does not wait 12 s.
5. "Stopp!" fails on both models. An emergency stop should not depend on the LLM; the head touch and `estop` already cover it, but a spoken "Stopp" keyword could also be caught before the LLM.
6. Run each model two or three times to see how much the score varies at temperature 0.4.

## Raw logs

The verbose logs from this run are at `/tmp/claude-1000/-home-lukas-Github-naollm/ef13a561-82d5-4129-94ff-3587c6c413bb/scratchpad/eval_e2b.log` and `/tmp/claude-1000/-home-lukas-Github-naollm/ef13a561-82d5-4129-94ff-3587c6c413bb/scratchpad/eval_e4b.log`. They are under `/tmp` and are lost at reboot.
