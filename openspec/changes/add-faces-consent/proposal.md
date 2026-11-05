## Why

SPEC §18 includes face enrolment for club members with signed consent, and it needs no motors. Face templates are biometric data under GDPR Art. 9, and visitors include minors (SPEC §12.1), so this needs its own consent registry and deletion path. It is separate from `add-qi-adapter` because `ALFaceDetection` and `ALPeoplePerception` have never been tested on this robot, and this feature must be reviewable on its own.

## What Changes

- Implement the real perception methods over `ALFaceDetection` and `ALPeoplePerception`: learn, forget, forget all, list learned faces, recognized names and people count.
- Enable `who_is_here` on the real robot, replacing the `perception_off` rejection from `add-qi-adapter`.
- Add a consent registry at `~/.naollm/consent.json`, with a startup warning for records past their deletion date.
- Add `faces list`, `faces enroll <name>`, `faces delete <name>` and `faces delete --all` (SPEC §18.4, §18.6).
- Add the `notice` command, which speaks the privacy notice (SPEC §12.3), and an English signage text.
- Adapt enrolment to motors off: no head tracking, so the operator positions the head by hand.

Out of scope: head tracking, the age-guess game, emotion recognition and gender estimation. The last two stay excluded under SPEC §12.

**Depends on `add-qi-adapter`.** Apply this change after it.

## Capabilities

### New Capabilities

- `face-perception`: real face and people perception on the robot, and `who_is_here` behavior.
- `consent-registry`: the local record of consent, deletion dates and mismatch reporting.
- `face-enrolment`: the `faces` commands and the guided enrolment flow.
- `privacy-notice`: the spoken notice and signage text.

### Modified Capabilities

None. New requirements only.

## Impact

- **Code**: `robot/adapter.py`, `robot/qi_adapter.py`, `robot/fake_adapter.py`, new `admin/consent.py`, `admin/commands.py`, `tools/registry.py`, `tools/gate.py`, `tools/messages.py`, `bridge.py`.
- **Data**: a new local file with names and form IDs. Face templates stored on the robot by NAOqi.
- **Compliance**: the school and club supervisor must clear this before use (SPEC §12), and the consent form and deletion checklist (SPEC §12.3) must exist on paper. This change does not produce or replace them.
- **Hardware**: needs no motors. The first task probes the services, and if they are missing on this robot image, the change stops there.
