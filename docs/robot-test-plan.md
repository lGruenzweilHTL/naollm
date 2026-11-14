# Robot test plan

Everything from the seven open OpenSpec changes that needs the real robot, a person, or a decision. The code for all of them is in place and `pytest` passes (726 tests). Work through the parts in order: later parts assume the earlier ones passed.

Each step names the task it closes, in the form `change task`. Tick the task in `openspec/changes/<change>/tasks.md` when the step passes, and write what you saw in `spike/FINDINGS.md` where the step says so.

**Addresses used below:** robot `10.0.0.135`, laptop `10.0.0.165` (from your `config.yaml`). Change them in the commands if the router gave out other addresses. All commands run from the repository root and work in fish and bash.

> **Motors.** The battery is still defective. Part F commands the motors, so do not start it until a new battery is fitted. Every other part runs with `motion.motors_enabled: false`, and the bridge refuses to start if the robot is awake or stiff.

---

## 0. Before you start

1. Check that the code works on this laptop:
   ```bash
   .venv/bin/python -m pytest -q
   ```
2. Your `config.yaml` stays as it is (English, motors off). The new keys all have safe defaults. Test configurations go to `~/.naollm/configs/`, outside git:
   ```bash
   mkdir -p ~/.naollm/configs
   ```
3. `config.example.yaml` is now German and English. Do not copy it over `config.yaml` until German is installed (part C).
4. Robot seated or lying at rest, switched on, on the club router. Laptop on the same router.

---

## A. Voice turn on the real robot (`add-qi-adapter` 4.8)

Goal: a full voice turn from each trigger, the VAD and wake threshold tuned, `reconnect` checked, and the latency written down.

1. Start the bridge:
   ```bash
   .venv/bin/naollm run --config config.yaml
   ```
   Wait for `naollm ready`.
2. **Head tap:** tap the front of the robot's head, hear the beep, say "Can you dance?". The eyes turn blue, then yellow, and the robot answers. Note the time from the end of your sentence to the first word of the answer.
3. **Operator key:** press `space` on the empty console line, then speak. Same result.
4. **Wake word:** say "Hey NAO", then your question. If it does not trigger, type `ears test`, say "Hey NAO" five times at 1 m, and note every confidence it prints.
5. Repeat 2 to 4 three times each. Note any turn where speech was cut early or the robot never stopped listening.
6. **Reconnect:** pull the robot's network cable, or switch off the router, for 10 s. The console shows `robot connection lost`. Restore it, type `reconnect`, then `resume`, answer `y`, and run one more head-tap turn. If `reconnect` fails, note the exact error: the design falls back to restarting the bridge.
7. Type `quit`.
8. If speech was cut early or the end was missed, record raw clips and replay them through the VAD offline. Record only people who agreed, and delete `spike/clips/` afterwards:
   ```bash
   .venv/bin/python spike/record_clips.py --source robot --raw --tag near
   .venv/bin/python spike/vad_tune.py spike/clips/raw
   ```
   Set the best aggressiveness in `VAD_AGGRESSIVENESS` (`src/naollm/audio/vad.py`) and `audio.vad_silence_s` in `config.yaml`.
9. Set `audio.wake_threshold` in `config.yaml` just below the confidences from step 4.

**Record in `spike/FINDINGS.md`:** latency per trigger (median and worst), wake confidences, the threshold you chose, the VAD setting, and whether `reconnect` worked.

---

## B. Faces with consent (`add-faces-consent` 1.1, 5.3)

Needs: one club member who signed the consent form (`docs/consent-form.md`) and agrees to the test.

1. **Read-only probe** (`add-faces-consent` 1.1). It learns no face and stores nothing; look at the camera while it runs:
   ```bash
   .venv/bin/python spike/spike.py --robot 10.0.0.135 --listen-ip 10.0.0.165 --no-motion --faces 20 --skip speak battery behavior audio wakeword extras qipkg
   ```
   Copy the `faces` line (data shapes, counts) into `spike/FINDINGS.md`.
2. Make a test configuration with face recognition on:
   ```bash
   sed 's/face_recognition_enabled: false/face_recognition_enabled: true/' config.yaml > ~/.naollm/configs/faces.yaml
   ```
3. Start the console with no LLM:
   ```bash
   .venv/bin/naollm admin --config ~/.naollm/configs/faces.yaml
   ```
4. Enrol the member (`add-faces-consent` 5.3). Use their first name; enter the form ID from the paper form and a deletion date. Position the robot's head by hand so the camera sees the face:
   ```
   faces enroll Anna
   ```
5. With the member in front of the robot:
   ```
   tool who_is_here
   ```
   It must name the member and count the people. Then step away and run it again: no name.
6. Delete the face, then check that nothing is left:
   ```
   faces delete Anna
   faces list
   quit
   ```
7. Remove the test configuration:
   ```bash
   rm ~/.naollm/configs/faces.yaml
   ```

**Record in `spike/FINDINGS.md`:** whether enrolment worked first time, how many tries, whether `who_is_here` named the member, and that `faces list` was empty at the end. No name in the findings.

---

## C. German (`restore-german` 1.1, 1.2, 3.1 to 3.4)

Needs: a German language pack installed on the robot for both speech output and speech recognition (see "Installing German" in `spike/FINDINGS.md`). The CLI cannot install it.

1. Make the bilingual configuration from the example, with this robot's addresses:
   ```bash
   sed -e 's#tcp://192.168.8.20:9559#tcp://10.0.0.135:9559#' -e 's/# listen_ip: 192.168.8.10 /listen_ip: 10.0.0.165 /' config.example.yaml > ~/.naollm/configs/german.yaml
   ```
2. **Languages** (`restore-german` 1.1, 3.1). Run init; it checks German and English for both TTS and ASR, and sends no motor command:
   ```bash
   .venv/bin/naollm init --robot 10.0.0.135 --config ~/.naollm/configs/german.yaml
   ```
   The `languages` line must pass. If it fails, stop: German is not installed for both services.
3. Write down the install route, the pack version and the voice name in "Installing German" in `spike/FINDINGS.md` (`restore-german` 1.2).
4. **Wake phrases** (`restore-german` 3.2). Start the bridge and run the ears test, once in a quiet room and once with people talking, at 1 m. Say "Hallo NAO" five times and "Hey NAO" five times:
   ```bash
   .venv/bin/naollm run --config ~/.naollm/configs/german.yaml
   ```
   ```
   ears test
   ```
   Set `audio.wake_threshold` in `~/.naollm/configs/german.yaml` from the confidences, then restart.
5. **Voice turns in both languages** (`restore-german` 3.3). From each trigger (wake word, head tap, `space`):
   - German: "Kannst du winken?", "Wie heißt du?", and a short one: "Ja."
   - English: "Can you wave?", "What's your name?"
   Check: the robot answers in the language you spoke, its voice switches per turn, and the short German "Ja." is not answered in English.
6. **Eval with the bilingual configuration** (`restore-german` 3.4). This needs no robot and takes a while:
   ```bash
   .venv/bin/naollm eval --config ~/.naollm/configs/german.yaml
   ```
   Note the score per profile and per language (`de` and `en` lines).
7. When everything passes, make it your configuration:
   ```bash
   cp config.yaml ~/.naollm/configs/english-backup.yaml
   cp ~/.naollm/configs/german.yaml config.yaml
   ```

**Record in `spike/FINDINGS.md`:** pack version, voice name, wake confidences for both phrases in both rooms, the threshold, and the per-language eval scores.

---

## D. Wake word hit rate and speech to text

1. **Measure the robot's word spotting** with `config.yaml`:
   - 10 tries of "Hey NAO" at 1 m in a quiet room, then 10 in a noisy room. Target: at least 8 of 10.
   - 10 minutes with people chatting near the robot, nobody saying the phrase. Count false triggers (the robot beeps and listens). Target: fewer than 1.
2. **Compare speech to text models.** Record cut clips in German and English, then compare Whisper models, and Gemma audio input as one more candidate:
   ```bash
   .venv/bin/python spike/record_clips.py --source robot --tag near
   .venv/bin/python spike/stt_bench.py spike/clips --models small,base,gemma:gemma4:e4b
   ```

**Record in `spike/FINDINGS.md`:** hits and false triggers per room, the threshold, and the speech to text table.
---

## E. Age game (`add-age-game` 1.2, 1.3, 6.1)

Legal gate first. The game stays off (`privacy.age_game_enabled: false`) unless step 2 finds no expression or smile values, or the school's data-protection contact clears it in writing. Needs one consenting club member, alone in front of the camera.

1. Ask the member, and check the signed form.
2. **Probe** (`add-age-game` 1.2). One analysis, key names only, no values; it asks for consent confirmation first:
   ```bash
   .venv/bin/python spike/spike.py --robot 10.0.0.135 --listen-ip 10.0.0.165 --no-motion --age 20 --skip speak battery behavior audio wakeword extras qipkg
   ```
3. **Record and decide** (`add-age-game` 1.3). Copy the `age` line into `spike/FINDINGS.md`. If it printed `LEGAL BLOCKER`, stop here and ask the school's data-protection contact for written clearance (EU AI Act Art. 5(1)(f)).
4. Only with clearance: make a test configuration and start it. The console must show `AGE GAME ON`, and `walk on` must refuse:
   ```bash
   sed 's/age_game_enabled: false/age_game_enabled: true/' config.yaml > ~/.naollm/configs/age-game.yaml
   .venv/bin/naollm run --config ~/.naollm/configs/age-game.yaml
   ```
   ```
   status
   walk on
   notice
   ```
5. **On the robot** (`add-age-game` 6.1), with `tool guess_age` in the console or by asking "Guess how old I am!":
   - The member alone, in good light: the robot speaks a 5-year range.
   - Two people in view: it refuses and says it sees several people.
   - Low light or face turned away: it says it cannot see well enough.
   Note the confidence the robot reports by watching the outcome only; the bridge never prints the age.
6. `quit`, then remove the configuration:
   ```bash
   rm ~/.naollm/configs/age-game.yaml
   ```

**Record in `spike/FINDINGS.md`:** probe result, which refusals worked, and whether 0.5 is a sensible confidence floor on this robot. No age values, no names.

---

## F. Motion (`restore-motion` 1.2, 7.1 to 7.6, 6.2) — only with a new battery

**Do not start before a new battery is fitted.** An operator stays within reach for every step, the robot is on the floor on a mat, and the area of at least 1.5 × 1.5 m is clear.

1. **Battery reading** (`restore-motion` 1.2). With the robot seated at rest, compare the battery value with the charger LED over one full charge cycle. Read the value from the admin console's `status` with a config that has `battery_reliable: true`; it sends no motor command while `motors_enabled` is false:
   ```bash
   sed 's/battery_reliable: false/battery_reliable: true/' config.yaml > ~/.naollm/configs/battery.yaml
   .venv/bin/naollm admin --config ~/.naollm/configs/battery.yaml
   ```
   ```
   status
   ```
   Only if the readings are believable, go on. Record them.
2. Make the motion configuration:
   ```bash
   sed -e 's/battery_reliable: false/battery_reliable: true/' -e 's/motors_enabled: false/motors_enabled: true/' config.yaml > ~/.naollm/configs/motors.yaml
   ```
3. **Confirm the area** (`restore-motion` 7.1): robot on a mat, area clear, your hands near it.
4. **Wake and rest seated, then Stand and Crouch** (`restore-motion` 7.2). Startup wakes the robot and stands it up:
   ```bash
   .venv/bin/naollm admin --config ~/.naollm/configs/motors.yaml
   ```
   ```
   posture Crouch
   rest
   wake
   posture Stand
   posture Crouch
   ```
5. **Emergency stop from each trigger** (`restore-motion` 7.3), each while standing, during a gesture, and during keyframes:
   - Gesture: `tool play_gesture wave`, then stop it with the rear head sensor, the middle head sensor, `Ctrl+E`, and `estop`. After each: `resume`, `y`, `posture Stand`.
   - Keyframes: in a second terminal, play the victory animation slowly and stop it with the rear head sensor:
     ```bash
     .venv/bin/naollm anim preview victory --speed 0.5 --config ~/.naollm/configs/motors.yaml
     ```
   Check each time: the motion stops, the robot crouches, the eyes turn red, the state is STOPPED. Note whether the keyframe playback stopped at once (`killTasksUsingResources`).
6. **Deploy** (`restore-motion` 7.4). In Choregraphe, add one short test behavior to `choregraphe/showcase/` (folder `gesture_test`, starts and ends in Stand, no auto-start), map it in `~/.naollm/configs/motors.yaml` as `gestures: {test: showcase/gesture_test}`, then:
   ```bash
   .venv/bin/naollm deploy --robot 10.0.0.135 --config ~/.naollm/configs/motors.yaml
   ```
   Then in the admin console: `tool play_gesture test`, and stop it half-way with the rear head sensor.
7. **Init with motors** (`restore-motion` 7.5). It installs the SSH key (it asks for the robot's `nao` password itself), deploys the package, and its self-test stands, gestures and crouches after you confirm a clear area:
   ```bash
   .venv/bin/naollm init --robot 10.0.0.135 --config ~/.naollm/configs/motors.yaml
   ```
   Note whether head tracking (`ALBasicAwareness`) followed you without moving the body, and which built-in gesture paths exist (`behaviors` in the admin console).
8. **Review the animation:** if the victory preview looked right and safe, set `reviewed_by:` in `animations/victory.anim.yaml` to your name.
9. **Only after steps 4 to 7 passed** (`restore-motion` 7.6, 6.2): set `motion.motors_enabled: true` in `config.example.yaml`, and remove the "Motors off" part of the SPEC "Interim" section.

**Record in `spike/FINDINGS.md`:** battery readings vs charger, each e-stop result, the keyframe interruption, deploy output, the self-test lines, and the `ALBasicAwareness` and `ALAnimationPlayer` findings.

---

## G. Event readiness (`harden-for-event` 6.3, 7.1, 7.2)

1. **Answer three questions** (`harden-for-event` 6.3), then record them in SPEC §17:
   - Q5: Does the school require its own data-protection sign-off for the event, and who gives it?
   - Q6: Which club members want face enrolment, and who collects and keeps the paper consent forms?
   - Q7: How long is the showcase? This sets the rehearsal length, breaks and charging.
2. **Eval, then preflight** (`harden-for-event` 7.1). The eval writes the result file that preflight reads:
   ```bash
   .venv/bin/naollm eval --config config.yaml
   .venv/bin/naollm preflight --config config.yaml
   ```
   Fix every `✗` line and run preflight again until it says `ready for the event`.
3. **Rehearsal** (`harden-for-event` 7.2). 30 minutes with club members as visitors, following `docs/event-runbook.md`, with planned operator steps only. Afterwards:
   ```bash
   .venv/bin/naollm cleanup --config config.yaml
   ```

**Record in `spike/FINDINGS.md`:** every unplanned intervention, turn latencies, false wake triggers, and whether the rehearsal ran motors off or English only.

---

## H. Archive, in this order

Several changes start with "confirm the earlier change is archived". Archive each one when its tasks are all ticked:

```
/opsx:archive add-qi-adapter
/opsx:archive add-faces-consent
/opsx:archive restore-german
/opsx:archive add-age-game
/opsx:archive restore-motion
/opsx:archive harden-for-event
```

`restore-motion` comes late because part F waits for the battery. `harden-for-event` comes last, after the rehearsal.
