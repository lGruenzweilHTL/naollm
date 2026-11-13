# showcase

The Choregraphe project for the robot's custom behaviors (SPEC §9). Package id `showcase`.

- Open `showcase.pml` in Choregraphe and add one behavior per animation. Name its folder after the tool enum entry, for example `dance_macarena` or `gesture_bow`, and map it in `config.yaml` as `showcase/dance_macarena`.
- Each behavior starts and ends in Stand, finishes within 30 s, uses no speech or speech recognition boxes, and has no auto-start trigger. `naollm deploy` refuses a behavior with an auto-start trigger.
- Install with `naollm deploy`. It bumps the patch part of the version in `manifest.xml`, builds with `qipkg make-package`, installs with `qipkg deploy-package`, and checks the installed behaviors against the config. It needs `motion.motors_enabled: true`.

## Behaviors

| Behavior | Source | Length | Notes |
|---|---|---|---|
| `dance_gangnam` | Kürnberghalle event 2022, `Tanz/V6/GangnamStyle` | about 20 s | Whole body, legs included. Plays `court-boom.wav`. The original's volume and `Rest` boxes are removed. |
| `dance_disco` | PowerDays, `PowerDays_Dance` "Disco" | about 18 s | Whole body, legs included, no sound. The original ended in `Sit Down` and never stopped; it now ends in Stand and stops. |

Both come from the `Robotics-Choreograph-Challenges` repository. Each now runs Stand Up, the dance timeline, then Stand Up again, and stops when a posture change fails, so the bridge is never left waiting.

**Music licence:** `court-boom.wav` (17 s) was exported from Adobe After Effects in October 2012 and is most likely a clip of the song. It is for testing only and is not in git (this repository is public): copy it from `../Robotics-Choreograph-Challenges/Kürnberghalle_Programme/Tanz/V6/GangnamStyle/court-boom.wav` to `dance_gangnam/` before building the package. Public playback at the event needs a licence (AKM in Austria), or remove the Play Sound box.

**Box scripts:** the box Python code is stored in CDATA sections, as Choregraphe writes it. Tools that rewrite a `.xar` with `xml.etree` drop the CDATA and merge the code with the XML indentation, which Choregraphe then reports as an `IndentationError`.

Neither has been played through the bridge yet. Both timelines move some joints to their mechanical limits, and the Gangnam timeline turns the elbows faster than the keyframe validator allows (SPEC §19.3). Watch the first run on the robot from close by, with a hand ready to catch it.
