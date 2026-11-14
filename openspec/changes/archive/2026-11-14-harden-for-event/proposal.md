## Why

Phase 5 of SPEC §16 asks for a showcase-ready system: a full 30-minute rehearsal without unplanned operator help, eval ≥ 90 % per profile, event-mode checks, signage, consent forms and a deletion checklist (SPEC §12.3). Today each check lives in a different place or only in someone's head, so a wrong setting, a stale eval or a forgotten face template can reach the event or outlast it. This change gives the operator one command before the event and one after it, and adds the dance rate limit from SPEC §13 against overheating.

**Depends on** `add-qi-adapter` and `add-faces-consent` being archived. The motion checks in the rehearsal need `restore-motion`, and the German checks need `restore-german`; without them, the rehearsal runs motors off or English only and the findings say so.

## What Changes

- Add `naollm preflight`, which checks the event setup and exits non-zero on any failed required check: event mode and privacy switches, local LLM endpoint, model loaded, a recent passing eval for the configured model, reviewed animations, installed behaviors, languages, battery trust, overdue consent records, and registry mismatches.
- `naollm eval` writes a result file per model (scores per profile, case file hash, date), and gains `--min-score` to replace the fixed 90 % target.
- Add `naollm cleanup`, the after-event deletion checklist: forget all faces after typing `DELETE`, clear the consent registry, check that no audio or transcript files exist, write a deletion record without names, and print the manual items.
- Add a dance cooldown to the gate: `motion.dance_cooldown_s`, default 120 s.
- Docs: event-day runbook, rehearsal script, signage and consent form templates marked "to be checked by the school", and answers to the open questions in SPEC §17.
- Rehearsal: a 30-minute run with the result recorded.

## Capabilities

### New Capabilities
- `event-readiness`: `naollm preflight` and `naollm cleanup`, their checks and their outputs.

### Modified Capabilities
- `safety-gate`: adds the dance cooldown rule.
- `tool-eval`: adds the result file and `--min-score`.

## Impact

- **Code**: new `deploy/preflight.py` and `deploy/cleanup.py`, `main.py`, `eval.py`, `tools/gate.py`, `tools/messages.py`, `config.py`.
- **Files written**: `~/.naollm/eval/<model>.json` (no visitor data), `~/.naollm/deletions.log` (dates and counts, no names).
- **Docs**: `README.md`, new `docs/event-runbook.md`, `docs/signage.md`, `docs/consent-form.md`.
- **People**: the school and club supervisor must review the signage and consent form, and someone must collect the paper forms (SPEC §17, questions 5 and 6). The change produces templates, not legal sign-off.
