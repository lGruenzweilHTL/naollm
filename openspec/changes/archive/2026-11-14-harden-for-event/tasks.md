## 1. Preconditions

- [ ] 1.1 Confirm `add-qi-adapter` and `add-faces-consent` are archived; stop here otherwise

## 2. Eval results

- [x] 2.1 Write `~/.naollm/eval/<model>.json` after a finished run: date, model, run settings, case file SHA-256, score per profile, no replies
- [x] 2.2 Add `--min-score` to replace the fixed target, and name the profiles below it
- [x] 2.3 Test the file contents, no file on Ctrl+C, and the exit code with and without `--min-score`

## 3. Dance cooldown

- [x] 3.1 Add `motion.dance_cooldown_s` (default 120, 0 turns it off) to the config and `config.example.yaml`
- [x] 3.2 Record the dance end time in the handler's `finally`, and add the `dance_cooldown` gate check with the seconds left and spoken messages in German and English
- [x] 3.3 Test the three cooldown scenarios for the LLM and the operator

## 4. Preflight

- [x] 4.1 Add `deploy/preflight.py` with the configuration, endpoint, model, eval, robot, animation and consent checks, read-only
- [x] 4.2 Add `naollm preflight`, with counts instead of names in event mode
- [x] 4.3 Test each check's pass and fail case against the fake robot and a stubbed model endpoint

## 5. Cleanup

- [x] 5.1 Add `deploy/cleanup.py`: `DELETE` confirmation, forget all faces, then remove the records, leftover file scan with a prompt, deletion log line without names, manual items
- [x] 5.2 Add `naollm cleanup`
- [x] 5.3 Test the confirm, abort, leftover file and unreachable robot scenarios

## 6. Docs

- [x] 6.1 Write `docs/event-runbook.md`: day before (eval, preflight), setup (router, floor area, charger), doors open (`ears test`, `notice`), during (breaks, e-stop), after (`naollm cleanup`, paper forms)
- [x] 6.2 Write `docs/signage.md` and `docs/consent-form.md` from SPEC §12.3, each marked as a draft for the school to check, with variants for the age game and the laptop wake engine
- [ ] 6.3 Ask the user for answers to SPEC §17 questions 5, 6 and 7, and record them in the SPEC

## 7. Rehearsal

- [ ] 7.1 Run `naollm preflight` against the real robot and fix every failed check
- [ ] 7.2 Run a 30-minute rehearsal with club members as visitors, with planned operator steps only, and record interventions, latencies and false triggers in `spike/FINDINGS.md`. State whether it ran motors off or English only
