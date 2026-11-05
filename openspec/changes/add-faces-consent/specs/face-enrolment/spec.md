## ADDED Requirements

### Requirement: Enrolment needs consent first
`faces enroll <name>` SHALL ask for the consent form ID before anything happens on the robot. A blank answer MUST abort. It MUST then ask for the deletion date, defaulting to the next day, and MUST reject a date in the past.

#### Scenario: No form ID
- **WHEN** the operator leaves the form ID blank
- **THEN** the command aborts, the robot does not act and nothing is stored

#### Scenario: Past date
- **WHEN** the operator enters a deletion date in the past
- **THEN** the command asks again

### Requirement: Enrolment flow without motors
After consent, the robot SHALL speak a request to look at it in the current language, with green eyes. The console MUST tell the operator to position the robot's head by hand, since head tracking is off. The bridge MUST try `learn_face` up to 3 times, with a spoken hint between tries, and MUST restore the eyes at the end.

#### Scenario: Learned on second try
- **WHEN** the first try fails and the second succeeds
- **THEN** the robot says the name once, and the record is written

#### Scenario: All tries fail
- **WHEN** all 3 tries fail
- **THEN** no record is written and the robot stores no face for that name

#### Scenario: Record write fails
- **WHEN** learning succeeds and the registry write fails
- **THEN** the face is forgotten on the robot and the console reports the failure

### Requirement: Enrolment is limited to safe states
`faces enroll` SHALL run only in IDLE, with `privacy.face_recognition_enabled` true. An emergency stop during enrolment MUST abort it and store nothing. The wake word MUST stay paused during enrolment.

#### Scenario: Stop during enrolment
- **WHEN** the emergency stop fires during a learning try
- **THEN** enrolment ends, any face learned for that name is forgotten and no record is written

#### Scenario: Recognition off
- **WHEN** the privacy switch is false
- **THEN** the command refuses and explains why

### Requirement: Names are validated
Names SHALL be 1 to 32 letters, digits, spaces or hyphens. Enrolling a name that already has a record MUST be refused until it is deleted. The command MUST NOT ask for any other personal detail.

#### Scenario: Duplicate name
- **WHEN** a record for `Lukas` already exists and the operator enrolls `Lukas`
- **THEN** the command refuses and names `faces delete`

### Requirement: Listing and deleting
`faces list` SHALL show enrolled names, consent references and deletion dates. `faces delete <name>` MUST forget the face and remove its record. `faces delete --all` MUST require typing `DELETE`. When forgetting fails on the robot, the record MUST stay and the console MUST report the failure.

#### Scenario: Delete all needs the exact word
- **WHEN** the operator types `delete` in lower case at the prompt
- **THEN** nothing is deleted

#### Scenario: Robot unreachable during delete
- **WHEN** `forget_face` fails
- **THEN** the record stays, so the deletion can be retried

### Requirement: Names stay out of event logs
In event mode, enrolment and deletion log entries SHALL NOT contain names or form IDs.

#### Scenario: Enrolment log
- **WHEN** an enrolment succeeds in event mode
- **THEN** the log shows the outcome with no name or form ID
