## 1. Probe

- [ ] 1.1 Add a no-motion, read-only probe to `spike/spike.py` for `ALFaceDetection`, `ALPeoplePerception` and their data shapes, with no face stored, run it on the robot and record the results in `spike/FINDINGS.md`
- [x] 1.2 Stop and ask the user if a service is missing or unusable; otherwise continue

## 2. Perception

- [x] 2.1 Add `learned_names`, `forget_all_faces` and an availability flag to `Perception`, and update `FakeRobot`, with tests
- [x] 2.2 Implement real perception in `qi_adapter.py` with an extended allowlist in `tests/qi_double.py`, and confirm no motor calls, with tests for failed learning, name expiry and an empty list
- [x] 2.3 Enable `who_is_here` when perception is available, and keep `perception_off` otherwise, with tests including the event mode log

## 3. Consent registry

- [x] 3.1 Implement `admin/consent.py` with atomic writes and mode 0600, with tests for failed writes and damaged files
- [x] 3.2 Add the overdue check at startup with confirmed deletion, with tests for both answers
- [x] 3.3 Add mismatch detection between the robot and the registry, with tests in both directions

## 4. Commands

- [x] 4.1 Add `faces enroll` with consent questions, the 3-try flow and name validation, with tests for each abort path
- [x] 4.2 Add estop handling and rollback during enrolment, with tests for a stop mid-try and a failed record write
- [x] 4.3 Add `faces list`, `faces delete` and `faces delete --all`, with tests for the exact `DELETE` word and for failed robot deletes
- [x] 4.4 Add a test that event mode logs contain no names or form IDs for any `faces` command
- [x] 4.5 Add the `notice` command and its messages in both languages, with tests for motors-off wording and refusal during a turn

## 5. Docs and robot check

- [x] 5.1 Set `privacy.face_recognition_enabled: false` in `config.example.yaml` with a comment on clearance, and update the SPEC for motors-off enrolment and the English notice
- [x] 5.2 Add English signage and the deletion checklist to `README.md`
- [ ] 5.3 Ask the user to confirm consent is in place, then on the robot, with no motors, enrol a consenting club member, check `who_is_here` and delete the face, and record the results
- [x] 5.4 Run the full test suite and report the results
