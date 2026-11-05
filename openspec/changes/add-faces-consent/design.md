## Context

`Perception` in `robot/adapter.py` has `learn_face`, `forget_face`, `people_count` and `recognized_names`, implemented only by `FakeRobot`. `add-qi-adapter` reports perception as unavailable on the real robot. The spike never tested `ALFaceDetection` or `ALPeoplePerception`. This robot may run the RoboCup image of 2.8.5.11, and with no German packs it already differs from a stock robot, so these services are not assumed to work.

With motors off the head has no stiffness and cannot track. The camera points wherever the head rests.

## Goals / Non-Goals

**Goals:**
- Enrol only after consent, and always be able to prove and carry out a deletion.
- Fail safely: never a face without a record, or a lost record for an existing face.

**Non-Goals:**
- Head tracking.
- Producing the consent form or legal clearance.
- Any recognition beyond enrolled club members.
- Age or gender estimation and emotion recognition, which SPEC §12 excludes or disables.

## Decisions

### D1: Probe first

The first task is a read-only check of the services, with no face stored. If they are missing or unusable on this image, the change stops and the user decides. Building first was the alternative, but the result might never run on this robot.

### D2: Separate learned and recognized

`Perception` gains `learned_names()` and `forget_all_faces()`. `recognized_names()` already means "seen now". `faces list` needs the stored list, and mixing the two would make mismatch reporting wrong.

### D3: Order of writes protects the invariant

Enrol: consent questions, learn, write the record, and on a write failure, forget the face. Delete: forget, then remove the record, so a failure keeps the record and the deletion can be retried. Writing the record first was the alternative, but a failed learn would leave consent data for nobody.

### D4: Atomic registry with owner-only access

Write to a temporary file in the same directory with mode 0600, then rename. A damaged file blocks enrolment and is left alone, since overwriting it would destroy the only proof of consent.

### D5: Recognition runs only when enabled

The adapter subscribes to `ALFaceDetection` and `ALPeoplePerception` only when `privacy.face_recognition_enabled` is true or a people count is needed. Names expire after 5 s, using the `FaceDetected` data. The names are held in memory only.

### D6: Enrolment with a hand-positioned head

The console tells the operator to position the head by hand before learning. SPEC §18.6 step 3 turns tracking on, which needs motors, so the step is skipped by checking `bridge.motors_enabled`. The change works unmodified after motors return. Spoken lines come from `tools/messages.py` in the current language, replacing the German-only wording in the SPEC.

### D7: Enrolment takes exclusive control

Enrolment pauses the wake word and registers a canceller through `register_turn`, so the existing emergency stop ends it. It leaves the state in IDLE, since no new state is justified for one operator-run command. A check at each step refuses to continue if the state changed.

## Risks / Trade-offs

- **Services missing on this robot image** → D1 stops the change early.
- **Head droops and the camera misses the face** → The operator holds or props the head, and all failures store nothing. Learning quality may be poor, and this is only measurable on the robot.
- **Robot and registry drift apart** → `faces list` shows mismatches, and deleting resolves them.
- **Recognition is unreliable in a real hall** → `who_is_here` is a small feature, and the switch turns it off.
- **Personal data on the robot survives** → The deletion checklist (SPEC §12.3) and overdue warnings cover it. A factory reset is not part of this change.
- **Legal status** → The change enforces the workflow but does not establish a lawful basis. The school must clear it first. The default of `privacy.face_recognition_enabled: true` from the SPEC stays in code, and `config.example.yaml` sets it to `false` until clearance.

## Migration Plan

Apply after `add-qi-adapter`. No existing data needs conversion. To remove the feature later: run `faces delete --all`, then delete `~/.naollm/consent.json`.

## Open Questions

- Has the school cleared this, and who collects consent forms (SPEC §17 items 5 and 6)? Needed before real enrolment, not before implementation or tests with the fake robot.
- What are the exact `FaceDetected` data and `PeoplePerception/VisiblePeopleList` shapes on 2.8.5.11? Answered by task 1.1.
