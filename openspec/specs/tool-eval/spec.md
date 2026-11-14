# tool-eval Specification

## Purpose

Defines the result file that `naollm eval` writes per model and the minimum score that decides its exit code, so preflight can check for a recent passing eval.

## Requirements

### Requirement: Result file
After each run, `naollm eval` SHALL write `~/.naollm/eval/<model>.json` with the date, the model, the run settings, the SHA-256 of the case file, and the score per profile. The file MUST NOT contain model replies. A run stopped with Ctrl+C MUST NOT write a result file.

#### Scenario: Finished run
- **WHEN** a run of `gemma4:e4b` finishes
- **THEN** `~/.naollm/eval/gemma4_e4b.json` holds the scores per profile and the case file hash

#### Scenario: Interrupted run
- **WHEN** the operator presses Ctrl+C during a run
- **THEN** the partial report is printed and no result file is written

### Requirement: Minimum score
`naollm eval --min-score <fraction>` SHALL replace the target score, which is 0.9 without the option. The exit code MUST be non-zero when any profile scores below the target, and the report MUST name those profiles.

#### Scenario: Below the minimum
- **WHEN** `--min-score 0.95` is given and `walking_on` scores 92 %
- **THEN** the exit code is non-zero and the report names `walking_on`

#### Scenario: Default target
- **WHEN** no `--min-score` is given and every profile scores at least 90 %
- **THEN** the exit code is 0
