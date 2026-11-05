## ADDED Requirements

### Requirement: Consent records
The registry SHALL store `name`, `consent_form_id`, `enrolled_at` and `delete_after` for each enrolled person in a local file, `~/.naollm/consent.json` by default, and nothing else. The file MUST be readable and writable only by its owner, and MUST be written atomically.

#### Scenario: File permissions
- **WHEN** the registry writes the file on Linux
- **THEN** its mode is 0600

#### Scenario: Interrupted write
- **WHEN** a write fails part-way
- **THEN** the previous file content stays intact

### Requirement: Damaged registry blocks enrolment
A registry file that cannot be parsed SHALL stop enrolment and raise a console alert. The bridge MUST NOT overwrite the damaged file.

#### Scenario: Invalid file
- **WHEN** the file contains invalid JSON at startup
- **THEN** the console shows an alert, `faces enroll` refuses and the file does not change

### Requirement: Overdue records
At startup, the console SHALL list records past `delete_after` and offer to delete them. Nothing MUST be deleted without confirmation.

#### Scenario: Overdue record declined
- **WHEN** a record is overdue and the operator answers no
- **THEN** the record and the face stay, and the warning appears again at next startup

#### Scenario: Overdue record accepted
- **WHEN** a record is overdue and the operator answers yes
- **THEN** the robot's face and the record are both deleted

### Requirement: Mismatches are reported
`faces list` SHALL report names learned on the robot with no record, and records with no learned face on the robot. A mismatch MUST be resolved only by deleting.

#### Scenario: Face without consent
- **WHEN** the robot has a learned face named `Alex` and no record exists
- **THEN** `faces list` marks `Alex` as having no consent record, and `faces delete Alex` removes the face
