# Phase 0 findings

Date: 2026-11-05 · Robot name: nao · Operator: ____

## Constraint: no motors on this robot

The robot's battery is defective. Test only features that need no motors, and always run the spike with `--no-motion`. Do not run behaviors, gestures, `ALAnimatedSpeech`, `wakeUp` or `rest`.

Every check that needs motors stays open until the battery is replaced. A result of "not tested" here does not mean the feature works.

## Environment

| Item | Value |
| --- | --- |
| NAOqi version (exact) | 2.8.5.11 |
| Laptop OS, model, RAM | Ubuntu 24.04.5, model code 82KB, 15 GiB (read from this machine; confirm it ran the spike) |
| Python version used | 3.12.3 (repo `.venv`; confirm it ran the spike) |
| `qi` version / wheel source | 3.1.5, pip (repo `.venv`) |
| Python versions with a `qi` wheel | 3.12 works; others not tried |
| `qipkg` version | qibuild 3.18 (repo `.venv`) |
| Network (router, 2.4/5 GHz, laptop on Ethernet?) | not recorded |

## Results

Summary table as the script printed it, from a `--no-motion` run. This run predates the script fixes below; the fixed script would report `speak` and `extras` as FAIL for the same robot behavior.

| Check | Result | Detail |
| --- | --- | --- |
| connect | PASS | robot 'nao', NAOqi 2.8.5.11 |
| motors-off | PASS | Autonomous Life disabled, motors off |
| speak | PASS | spoke in ['English']; ALAnimatedSpeech not tested (--no-motion); languages ['Japanese', 'Chinese', 'English'] |
| battery | PASS | 100 % |
| behavior | SKIP | no --behavior given; run again with one of the paths above |
| audio | PASS | 59 chunks, 5.0 s of audio, level -45.3 dBFS; saved to /tmp/mic.wav (delete it after listening) |
| wakeword | FAIL | RuntimeError:     AsrEngine::setCurrentLanguage<br>This engine does not support the following language: German |
| extras | PASS | head touch events seen: [] |
| qipkg | SKIP | qipkg present (0); give --package <file.pml> to test a deploy |

Second run, with the fixed script, `--no-motion --wake-language English --wake-words "Hey NAO"`:

| Check | Result | Detail |
| --- | --- | --- |
| connect | PASS | robot 'nao', NAOqi 2.8.5.11 |
| motors-off | PASS | Autonomous Life disabled, motors off |
| speak | FAIL | spoke in ['English']; missing ['German']; languages ['Japanese', 'Chinese', 'English']; ALAnimatedSpeech not tested (--no-motion) |
| battery | PASS | 100 % |
| behavior | SKIP | no --behavior given; run again with one of the paths above |
| audio | PASS | 59 chunks, 5.0 s of audio, level -24.4 dBFS; saved to /tmp/mic.wav (delete it after listening) |
| wakeword | FAIL | English: 2 recognitions, 0 above 0.5: [('<...> Hey NAO <...>', 0.43), ('<...> Hey NAO <...>', 0.44)] |
| extras | PASS | head touch events seen: ['FrontTactilTouched', 'MiddleTactilTouched', 'RearTactilTouched']; missing: [] |
| qipkg | SKIP | qipkg present (0); give --package <file.pml> to test a deploy |

## What the results mean

Current status, from both runs:

| Check | Status | Reason |
| --- | --- | --- |
| connect | Works | Python 3 `qi` reaches NAOqi 2.8. |
| motors-off | Works | The gate passed in both runs. |
| speak | **English only** | English TTS works. **German is not installed.** |
| battery | **Do not trust** | The API call works, but a defective battery reports 100 %. The value says nothing about the real charge. |
| behavior | Blocked | Needs motors. |
| audio | Works | Remote audio reaches the laptop at the full 5 s, at -24.4 dBFS in the second run. Speech quality has not been judged by listening or by Whisper. |
| wakeword | **Weak** | English word spotting runs and heard `Hey NAO` twice, but at 0.43 and 0.44, below the 0.5 threshold. The number of attempts and the room noise were not recorded. German is rejected by the ASR engine. |
| extras | Works | Events arrive from all three head sensors. |
| qipkg | Partial | The tool runs. No package was built or deployed. |

## Answers to SPEC §17

1. NAOqi version, and the Python version the `qi` wheel supports: 2.8.5.11; Python 3.12 with `qi` 3.1.5.
2. ~~Languages~~ (resolved: German + English). **This now conflicts with the robot; see below.**
3. Installed TTS languages / voices: Japanese, Chinese, English. No German. Voices not listed.
4. Laptop RAM (`gemma4:e2b` or `e4b`): 15 GiB. Decided later: `gemma4:e4b` is the default.

## Details for the real adapter

- Default gesture paths that are missing (and their replacements): not recorded. The `extras` console output was not kept. Checking that a path is installed needs no motors, but playing it does.
- Does `--listen-ip` need to be set for remote audio? Not recorded; the command line was not kept.
- Wake-word confidences heard (quiet room / noisy room / at 1 m): English `Hey NAO`, 0.43 and 0.44; room and distance not recorded.
- `stopAllBehaviors` latency when stopping a running behavior: not tested (needs motors).
- Autonomous Life state at boot, and what had to be changed: `disabled` at the time of the gate. State at boot not recorded.
- Head touch events seen (front / middle / rear): all three, in the second run.
- Anything that behaved differently from SPEC §6.2:
  - SPEC §11.1 fixes the wake-word ASR language to German. This robot's ASR engine rejects German.
  - SPEC §11.1 and §11.3 switch TTS to German per turn. This robot has no German TTS.

## Installing German

Researched on 2026-11-05 from vendor pages. Nothing here has been tried on the robot.

**Who supplies it now.** Aldebaran shut down, and its knowledge base URLs now redirect to Maxtronics.

**Is German available?**
- The NAOqi 2.8.7.4 docs list German for `ALTextToSpeech` and `ALSpeechRecognition` on NAO 6, in a list of 21 languages. The docs call that list indicative.
- Maxtronics says "You can now download the 21 languages compatible with NAO6 free of charge". It does not name them. Matching the two lists is an inference.
- No page says whether a language install covers both TTS and ASR.

**Route to try.**
1. Sign in or register at the [Maxtronics store](https://cloud.nao6.maxtronics.com/).
2. Select German for the robot. The signed-in store home page has no link to languages. Open `https://cloud.nao6.maxtronics.com/languages/` directly. That path requires login and matches `/languages/` on the old Aldebaran store; its contents are not confirmed. On 2026-11-05 a signed-in browser was sent back to the home page with `?next` set, for `/languages/` and `/manage/`. The cause is not known. An older guide starts the store sign-in from Robot Settings, so try that next.
3. Open Robot Settings and update the robot's applications. The robot needs internet access (implied by the pages, not stated).

**Open risks.**
- The Maxtronics page says "Aldebaran Cloud Services will end on October 1st 2026" and that software updates then need manual installation "via Choregraphe and our technical assistance". The store page still loads, but the language download was not tested after that date.
- This robot runs 2.8.5.11. Maxtronics lists 2.8.7.4 for robots sold in 2025 or before. No page says whether the store works with 2.8.5.11.
- `aldebaran/nao6-binaries` contains a `robocupper-nao-x86-2.8.5.11` image with the same version. This robot may run that image. That is not confirmed, and no page says what it means for languages.
- The docs allow "two additional languages". This robot already has three, and the limit may block a fourth.
- An older knowledge base article calls the choice of a second language definitive; replacing it needs support.
- No install page mentions motors, a reboot or the battery. A reboot could change the Autonomous Life state, so check that state before any motor-free test continues.

**Do not flash an image only to get German.** Choregraphe "Update robot system" applies a factory reset that deletes user data. No page says which languages an image contains, a power loss during the flash is a risk with this battery, and a reset may restore Autonomous Life, which can wake the motors.

**No offline route found.** No page describes a language pack as a local file. `aldebaran/nao6-binaries` has images and tools but no separate language pack.

**If this fails.** Use the [support form](https://maxtronics.com/en/maxtronics-support/) with the robot's serial number. If German stays unavailable, decide between English only on the robot and laptop-side German speech.

Sources: [install steps](https://maxtronics.com/support/kb/nao6/languages-nao6/nao-add-a-2nd-language-using-ade), [SW update notice](https://maxtronics.com/en/new-sw-update-nao6/), [language setup](https://doc.nao6.maxtronics.com/family/nao_user_guide/setting_language_nao.html), [supported languages](https://doc.nao6.maxtronics.com/family/nao_technical/languages_naov6.html), [advanced operations](https://maxtronics.com/en/nao-advanced-operations), [nao6-binaries](https://github.com/aldebaran/nao6-binaries).

## First run of the real adapter

Date: 2026-11-05. Robot on Wi-Fi at `nao.local`, posture `LyingBack`, Autonomous Life `disabled`, no stiffness before and after each run. Nobody was at the robot, so nothing that needs a person is covered here, and nobody confirmed hearing or seeing the output.

**Ran without an error:**

- `naollm admin`: session, `info`, `status`, `get_status`, `say` (returned normally), `set_eyes` through `ALLeds.fadeRGB` (about 0.2 s per call), `estop`, `resume`, `reconnect`, `who_is_here` (0 people), `faces list`, `notice`.
- Every console motion command and motion tool was refused, with stiffness still 0 afterwards.
- `naollm init --skip-selftest`: all steps passed and the profile was written (to a scratch directory, not `~/.naollm`).
- `reconnect` works on the same `qi.Application`, by closing the session and connecting again. Event delivery after it is not yet confirmed, since it needs a head touch.

**API facts read from the robot:**

| Call | Result |
| --- | --- |
| `ALMotion.getLimits("HeadYaw")` | `[[min, max, max_velocity, max_torque]]`, a list inside a list, in radians. `[[-2.0857, 2.0857, 8.268, 1.547]]` |
| `ALMotion.getLimits("LHand")` | `[[0.0, 1.0, 8.33, 0.292]]`: an open fraction, not radians |
| `ALMemory.getData("Device/DeviceList/ChestBoard/BodyId")` | The body ID, used as the robot id |
| `ALMemory.getData("robotHasFallen")` | `None` here, so the adapter tracks falls from the event instead |
| `ALBodyTemperature.getTemperatureDiagnosis()` | `None` when nothing is hot. The shape with a hot joint was not seen |
| `ALRobotPosture.getPosture()` | `LyingBack`, which is why a lying posture alone does not count as fallen |
| ASR languages | `Chinese`, `English`, `Japanese` |
| Installed behaviors | 889, none of them run |
| Signatures in `metaObject` | `ALAudioPlayer.playSine(iiif)`, `ALLeds.fadeRGB(sif)`, `ALSpeechRecognition.removeAllContext()`, `setAudioExpression(b)`, `setVisualExpression(b)` exist. None of the first three has been heard or seen working |

**Face and people probe** (`spike.py --no-motion --faces 8`, read-only, no face stored):

- `ALFaceDetection` and `ALPeoplePerception` both exist and respond on this image. Learned faces: 0.
- **`ALFaceDetection.isTrackingEnabled()` is true by default, and tracking moves the head.** The adapter calls `setTrackingEnabled(False)` before it subscribes, and treats a failure there as perception unavailable. Afterwards the robot read tracking false and recognition false. These settings stay on the robot until they are changed or it reboots; this was not tested further.
- Events `FaceDetected`, `PeoplePerception/VisiblePeopleList` and `PeoplePerception/PeopleDetected` exist.
- **Not seen yet: the data shapes with a person in view.** Nobody was in front of the camera, and a robot lying on its back looks at the ceiling. `face_labels` in `qi_adapter.py` follows the NAOqi documentation and ignores anything unexpected, so a wrong shape shows as no names, not as a crash. Run the probe again with a person in view before relying on names.

**Speech to text:** `audio/stt.py` was run once against real `faster-whisper` with the `tiny` model on the CPU, using generated audio with no words. The model loaded in 7.7 s, a forced language worked, and `info.all_language_probs` has the shape the code expects. This proves the API calls only. No real speech and no robot audio has been through Whisper, and the laptop's latency with the `small` model is unknown.

**With the operator at the robot** (2026-11-05): `naollm admin` with `say`, eye colours and all three head touches, before and after `reconnect`, and `naollm init` with the self-test both worked, as the operator reported. No API differences were reported. The detailed output was not kept.

## Open tests that need no motors

- A voice turn from each trigger: wake word, head tap, operator key. Tune VAD aggressiveness and `audio.wake_threshold`, and record latency against the SPEC §16 target.
- The face probe again with a person in view, then enrolment of a consenting member after clearance.

- Wake word hit rate and threshold, with a recorded number of attempts, at 1 m and in a noisy room.
- `qipkg` build and deploy of a package that contains no motion.
- Mic quality: run recorded speech through Whisper.
- List of `extras` output: default gesture paths, Autonomous Life state, joint limits.

## Spike script fixes after this run

Checked against a fake session, then run on the robot in the second run above.

- `wakeword` hard-coded German. It now takes `--wake-language` and `--wake-words`, and fails with the available list when the language is missing.
- `wakeword` left the ASR engine paused when `setLanguage` raised. It now always resumes.
- `speak` passed when only one language spoke. It now needs German and English.
- `extras` always recorded PASS. It now needs events from all three head sensors.

## Decision

- [x] pip `qi` on Python 3 works → build `qi_adapter.py` directly. Confirmed for session, TTS and remote audio only. Behaviors are not tested.
- [ ] Python 2.7 shim needed (SPEC §13 a)
- [ ] ROS 2 `naoqi_driver2` needed (SPEC §13 b)
- [ ] Audio via `ALAudioRecorder` fallback
- [ ] **Open: how to handle German**, which the robot has neither for TTS nor for ASR. Try the store route under "Installing German" first.
