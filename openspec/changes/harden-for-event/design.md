## Context

The pieces of an event check exist but are spread out: config loading refuses unsafe privacy settings in event mode, the cloud endpoint warning exists, `faces list` shows registry mismatches, unreviewed animations are dropped at startup, and `naollm eval` exits non-zero below 90 %, but its result is printed and lost. Nothing checks them together, and nothing guides the after-event deletion. The gate has battery and thermal rules but no limit on how often a dance runs.

## Goals / Non-Goals

**Goals:**
- One read-only command that says whether the setup is event-ready.
- One guided command that does the deletions after the event and leaves proof without names.
- A dance cooldown against overheating.
- Runbook, rehearsal script and paper templates in the repo.

**Non-Goals:**
- Legal sign-off. The templates are drafts for the school to check.
- Logging visitor sessions or keeping event statistics.
- Fixing problems automatically; preflight only reports them.

## Decisions

**Preflight is a separate command, not a startup step.** It runs the eval freshness and robot checks, which take seconds and need the robot, and the operator runs it once before doors open. `naollm run` keeps its current refusals. *Alternative:* fold it into `naollm run` in event mode. Rejected, because a stale eval would then block the bridge at the event itself, when the right move is to run the eval the day before.

**Eval results are kept as small files per model.** The file holds scores, settings, date and a case file hash, never replies, so it holds no visitor data, because eval prompts are fixed test sentences. The hash makes a result stale when the cases change. *Alternative:* re-run the eval inside preflight. Rejected, because a full run takes minutes per model.

**"Local" endpoint means loopback or a private address.** `localhost`, `127.0.0.0/8`, `::1`, and RFC 1918 or link-local addresses pass. Anything else fails, and the operator must change the signage and the config, not override the check.

**Cleanup forgets faces before removing records.** If the robot cannot be reached, the records stay, because they are the only list of faces still to delete. The deletion log has date and counts only, so the proof of deletion is not itself personal data.

**Leftover file scan is narrow.** It looks under `~/.naollm` and for `naollm*` files in the system temp folder. The bridge writes no audio, so a hit means a bug or the `ALAudioRecorder` fallback, and the operator decides before anything is deleted.

**Cooldown is counted from the end of a dance.** The gate records the end time in the handler's `finally`, so an interrupted dance also counts. *Alternative:* count from the start. Rejected, because heat builds while the dance runs.

## Risks / Trade-offs

- [Preflight passes but the hall differs from the rehearsal room] → The runbook has an on-site `ears test` and threshold step after preflight.
- [Templates are taken as legal text] → Each template starts with "Draft, to be checked by the school", as SPEC §12 says.
- [Cooldown frustrates visitors] → The rejection gives the seconds left, so NAO can say "in a minute"; the operator can change the setting.

## Migration Plan

All additive. `motion.dance_cooldown_s` defaults to 120 s; set it to 0 to keep today's behavior.

## Open Questions

- How long is the showcase (SPEC §17, question 7)? It sets the rehearsal length and the battery and charging plan in the runbook.
- Who collects and keeps the paper consent forms (SPEC §17, question 6)?
