# Event checklist

A tick list for the showcase. The day before does everything that does not depend on the booth: evals, model choice, deploy, motion and speech tests. The day of the event only checks the robot and the setup at the booth. Details for each step are in `docs/event-runbook.md`.

Commands assume the repository root, the project venv (`. .venv/bin/activate`) and `config.yaml`. Every step that moves the robot needs an operator at the robot and the robot on the floor in a clear area.

## The day before

### Software

- [ ] All changes committed, `python -m pytest -q` passes.
- [ ] Ollama runs and has the event model: `ollama list` shows `gemma4:e4b`.
- [ ] Model comparison, reasoning off (about 30 minutes): `naollm eval --config tests/eval/eval_config.yaml --run "reasoning off"`. Keep `gemma4:e4b` unless another model meets the 90 % target with a faster response. The last comparison: `gemma4:e4b` 97 % and 100 %, 3.5 s median response; no other model met the target with reasoning off.
- [ ] Final eval of the event configuration: `naollm eval --config config.yaml`. Every profile the configuration uses is at 90 % or more, German and English separately. Preflight accepts a result up to 14 days old, made with the current case file, so do not change `tests/eval/tool_calls.yaml` after this run.
- [ ] Decide the open SPEC §17 questions 5 to 7. The length of the showcase sets the battery and break plan.

### Network, as it will be at the booth

- [ ] Set up the event router at home, with the robot on its own Wi-Fi and the laptop on Ethernet. Do not use the school network.
- [ ] Give the robot and the laptop fixed addresses (DHCP reservation). `robot.url` and `robot.listen_ip` in `config.yaml` must match them. If `robot.listen_ip` is wrong, the robot cannot send audio to the laptop.
- [ ] Change the robot password from the default `nao`.

### Robot

- [ ] Charge the robot fully, then the laptop.
- [ ] Refresh the robot profile and run the self-test: `naollm init --robot 10.0.0.251`. German and English speech pass, and the robot hears `Hallo NAO` and `Hey NAO`.
- [ ] Redeploy the showcase package: `naollm deploy`. `choregraphe/showcase/dance_gangnam/court-boom.wav` must be on the laptop.
- [ ] Play every configured dance and gesture once through `naollm admin` (`tool dance <name>`), with the 120 s cooldown between dances: `macarena`, `robot`, `disco`, `gangnam`, `bow`. Remove a dance from the configuration if it is not stable.
- [ ] `robot` (`animations/robot.anim.yaml`): sign it off with `reviewed_by` after watching it, or event mode hides it.
- [ ] `victory`: preview it with `naollm anim preview victory` and sign it off with `reviewed_by`, or leave it out. Event mode hides animations that are not reviewed.
- [ ] Voice turns in German and English from each trigger: wake word, head tap and operator key.
- [ ] Emergency stop from each trigger during a dance: `Ctrl+E`, `estop`, middle head touch and rear head touch.
- [ ] Set a starting value for `audio.wake_threshold` with `ears test` in a quiet room. Only the fine tuning is left for the booth.
- [ ] Full preflight: `naollm preflight --config config.yaml`. Every line is `✓`, and every `!` line is read and accepted.
- [ ] Rehearsal of 30 minutes with club members as visitors, if not done yet (runbook, "Rehearsal").
- [ ] Charge the robot fully again overnight.

### Pack

- [ ] Router, Ethernet cable, robot charger, laptop charger, power strip.
- [ ] Mat and tape for an area of at least 1.5 × 1.5 m.
- [ ] Signage printed in the variant that matches the configuration (`docs/signage.md`).
- [ ] Consent forms (`docs/consent-form.md`), if faces are enrolled.
- [ ] Music for the Macarena, played separately. Public playback of `court-boom.wav` needs an AKM licence; without one, leave `gangnam` out.

## The day of the event

### Setup at the booth

- [ ] Router on, robot on its own Wi-Fi, laptop on Ethernet.
- [ ] The laptop has the configured address (`ip -4 addr`), and the robot answers: `ping 10.0.0.251`.
- [ ] Robot on the floor in the marked area, visitors outside the line. Charger and power strip within the operator's reach, not in the visitors' path.
- [ ] Signage up where visitors read it before they reach the robot.

### Robot checks

- [ ] Robot boots, battery reads well above 30 % (`status` in the console). Dances are refused below 30 %.
- [ ] Autonomous Life is off. If the bridge refuses with `Autonomous Life is solitary`, run `naollm init --robot 10.0.0.251 --only settings`.
- [ ] Preflight on site: `naollm preflight --config config.yaml`. Every line is `✓`.
- [ ] Start the bridge: `naollm run --config config.yaml`. `status` shows connected, the right state and the right motor mode.
- [ ] Tune the wake word for the room: `ears test` at 1 m with the room noise, set `audio.wake_threshold`, restart the bridge.
- [ ] One voice turn in German and one in English, through the wake word.
- [ ] One dance in the booth area, to check the floor and the space. Then wait the 120 s cooldown.
- [ ] One emergency stop with a head touch, then `resume`.
- [ ] `notice`: the robot speaks the privacy notice.
- [ ] Note the time and the battery level. Plan a break at least every 30 minutes.
