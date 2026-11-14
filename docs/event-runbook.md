# Event runbook

What the operator does before, during and after the showcase. Commands assume the repository root, the project venv (`. .venv/bin/activate`) and `config.yaml`.

For a tick list of the day before and the day of the event, see `docs/event-checklist.md`.

Open questions that change this plan (SPEC §17): how long the showcase runs (question 7) sets the battery and break plan, and who collects the consent forms (question 6) sets the after-event step for paper.

## The day before

1. Charge the robot fully. Charge the laptop.
2. Run the tool-call eval for the event model. It takes several minutes on this laptop and writes `~/.naollm/eval/<model>.json`:
   ```bash
   naollm eval --config config.yaml
   ```
   Every profile the configuration uses must reach at least 90 %, German and English shown separately.
3. Switch on the robot and run the preflight check. It is read-only and sends no motor command:
   ```bash
   naollm preflight --config config.yaml
   ```
   Fix every `✗` line. Read every `!` line: warnings do not block, but most need the signage or a review.
4. Print the signage (`docs/signage.md`) in the variant that matches the configuration: German and English, motors on or off, laptop wake engine, age game.
5. If faces are enrolled: check that every enrolled club member has a signed consent form (`docs/consent-form.md`).

## Setup at the booth

1. Own router; laptop on Ethernet; robot on the same LAN. School Wi-Fi often blocks device-to-device traffic (SPEC §3).
2. Robot on the floor in a marked area of at least 1.5 × 1.5 m, visitors outside the line. Never on a table while motion tools are on. With motors off, the robot sits or lies at rest.
3. Charger and power strip within reach of the operator, not in the visitors' path.
4. Put up the signage where visitors read it before they reach the robot.
5. Run the preflight again on site:
   ```bash
   naollm preflight --config config.yaml
   ```

## Doors open

1. Start the bridge:
   ```bash
   naollm run --config config.yaml
   ```
2. Tune the wake word for the room: type `ears test`, say the wake phrase at 1 m a few times, and look at the scores. Set `audio.wake_threshold` between the background and the phrase, then restart.
3. Type `notice`. The robot speaks the privacy notice. Repeat it now and then, between visitors.
4. Type `status`. Check: connected, the right state, `motors OFF` if motors are off, `AGE GAME ON` only if planned.

## During the event

- One operator stays within reach of the robot at all times (SPEC §10.4).
- **Emergency stop:** `Ctrl+E`, `estop`, or a touch on the robot's middle or rear head sensor. `resume` (answer `y`) continues.
- **Next visitor:** `reset` clears the conversation history.
- **Breaks:** plan a pause at least every 30 minutes: `llm off`, then `quit` if the robot has to cool down or charge. With motors on, a dance has a 120 s cooldown; the robot says so.
- **If the robot falls:** state is FALLEN. Place it upright, check it, then `resume`.
- **If the network drops:** `reconnect`, then `resume`.
- Watch the console for `ALERT` lines: battery, heat, wake-ups with motors off, lost connection.

## After the event

1. Type `quit` in the console. With motors on, the robot crouches and rests.
2. Run the deletion checklist. Type `DELETE` when asked:
   ```bash
   naollm cleanup --config config.yaml
   ```
   It forgets every learned face on the robot, removes every consent record, checks for leftover audio or transcript files, and writes one line with the date and counts to `~/.naollm/deletions.log`. If the robot cannot be reached, the records stay: forget the faces first.
3. Do the manual items the command prints: file or destroy the paper consent forms as the school requires, take down the signage, and set `privacy.face_recognition_enabled: false` again.

## Rehearsal

Before the event, run the whole booth for 30 minutes with club members as visitors (SPEC §16, phase 5). Use only the planned operator steps above. Write down every unplanned intervention, the turn latencies you notice, false wake word triggers, and whether the run was motors off or English only, in `spike/FINDINGS.md`.
