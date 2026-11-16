## ADDED Requirements

### Requirement: Eval progress
`naollm eval` SHALL print the case count, the model and the endpoint before the first request. While a case waits for the model on a terminal, it SHALL show one redrawn line with a spinner, the case number, the prompt, the time spent and an ETA from the mean time per case so far. When a case finishes, it SHALL print the case number, PASS or FAIL, the tool calls and the time, then the expectation for a failed case and the model's reply text. When the output is not a terminal, it SHALL print the result lines only.

#### Scenario: Result line per case
- **WHEN** a case with the prompt "Can you dance?" finishes with no tool call and the reply "Yes! Shall I show you?"
- **THEN** the output contains a PASS line for that case and the line `    says: Yes! Shall I show you?`

#### Scenario: Interrupted run
- **WHEN** the operator presses Ctrl+C during a case
- **THEN** the live line is cleared, the output says how many cases finished, a partial report follows, and the exit code is 130

### Requirement: Alternative expectations
A case's `expect` SHALL accept `any_of`, a non-empty list of expectations, and the case SHALL pass when one of them is met. The safety gate SHALL run at most once per case. A case file with an empty or malformed `any_of` SHALL be refused with its case number.

#### Scenario: Safe answers to an over-limit request
- **WHEN** the expectation is `any_of: [{no_tool: true}, {tool: step, args: {direction: forward}}]`
- **THEN** a reply with no tool, a 30 cm step forward and a 1000 cm step forward all pass, and a step back or a dance fails

### Requirement: Case expectations match the intended behavior
A question about an ability, such as "Kannst du tanzen?" or "Can you dance?", SHALL expect no tool, because the robot answers and offers the action instead of doing it. Requests beyond the motion limits SHALL accept any safe answer: no tool, or the matching tool in the requested direction, whether or not the gate rejects it.

#### Scenario: Ability question
- **WHEN** the eval runs the case "Can you dance?"
- **THEN** the case passes only if the model calls no tool

### Requirement: Model warm-up and timeout
Before the first case, `naollm eval` SHALL load the model through the Ollama native API unless it is already loaded or `--no-warmup` is given, and SHALL unload it at the end, also after Ctrl+C, only if it loaded it. Each request SHALL wait up to 120 s, or `--timeout`, in place of `llm.timeout_s`. If the endpoint is not Ollama, the eval SHALL say so and run the cases anyway.

#### Scenario: Warm-up before the first case
- **WHEN** the eval runs against Ollama with the model not loaded
- **THEN** the output says the model loaded before the first result line, and says it was unloaded after the last

### Requirement: Verbose trace
With `-v`, each case SHALL print its prompt and expectation, stream its reasoning and its reply as they arrive, then print each tool call with its arguments, the result, and its timings, tokens and finish reason.

#### Scenario: Case with reasoning and a tool call
- **WHEN** a case streams reasoning, the text "Hi there!" and a `play_gesture` call, with `-v`
- **THEN** the output shows a `thinking:` line, a `says: Hi there!` line, a `tool call: play_gesture` line with the arguments, and a timing line

### Requirement: Timing report
The final report SHALL show, over all finished cases, the mean, median, p90 and max of the response time, the time to the first token, to the first answer token (text or tool call), to the first text and to the first tool call, with the case count when a value is missing for some cases. It SHALL show the mean output and input tokens when the endpoint reports usage, a generation rate from the cases that streamed for 0.5 s or more, and how many cases reasoned, were cut off at `max_tokens`, came back empty or failed.

#### Scenario: Cut-off reply
- **WHEN** a case's reasoning uses all of `max_tokens` and no text or tool call follows
- **THEN** the case fails with "empty reply, cut off at max_tokens", and the report counts it as cut off and empty

### Requirement: Partial report
When the operator presses Ctrl+C, the eval SHALL print the accuracy and timing report for the cases that finished, say that the target is not judged, and exit with code 130.

#### Scenario: Interrupted after ten cases
- **WHEN** the operator presses Ctrl+C during case 11 of 41
- **THEN** the output says "partial report: 10 of 41 cases ran" and shows the accuracy and timings of those 10

### Requirement: Eval matrix
The eval SHALL accept a matrix config with only `base` and `runs`. `base` is a config. Each entry of `runs` is a set of `llm` settings with an optional `label`, merged onto `base.llm`. The eval SHALL run all cases once per run, in order, each with its own warm-up, report and model release, and SHALL then print a comparison table with one row per run: accuracy per profile, whether the target was met, the median time to the first answer token, to the first text and for the response, the generation rate, the counts of reasoning, cut-off, empty and failed cases, and the wall time. `--run TEXT` SHALL keep only the runs whose label contains TEXT. A plain config SHALL be one run. The repository SHALL ship `tests/eval/eval_config.yaml` with the candidate models, thinking off for each, and thinking on with `max_tokens: 1024` for the models that support it.

#### Scenario: Compare thinking on and off
- **WHEN** the operator runs `naollm eval --config tests/eval/eval_config.yaml --run qwen3:4b`
- **THEN** the eval runs `qwen3:4b` with thinking off, then on, and prints a table with one row for each

#### Scenario: Interrupted matrix
- **WHEN** the operator presses Ctrl+C during the third of eleven runs
- **THEN** the eval prints the partial report of the third run and the table for the three runs, starts no further run, and exits with code 130

#### Scenario: Bad matrix
- **WHEN** a run sets a key that `llm` does not have, or the file has a top-level key other than `base` and `runs`
- **THEN** the eval refuses the file and names the run or the key

