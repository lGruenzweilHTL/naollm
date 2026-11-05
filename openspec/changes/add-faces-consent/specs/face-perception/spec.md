## ADDED Requirements

### Requirement: Face operations on the robot
The real adapter SHALL learn a face, forget one face, forget all faces and list learned faces through `ALFaceDetection`. It MUST NOT save camera images, and MUST NOT call a motor command.

#### Scenario: Learn fails
- **WHEN** `learnFace` returns false
- **THEN** `learn_face` returns false and the learned list does not change

#### Scenario: Forget all
- **WHEN** `forget_all_faces` runs
- **THEN** the robot's learned list is empty afterwards

### Requirement: Face recognition respects the privacy switch
With `privacy.face_recognition_enabled` false, the bridge SHALL NOT start face recognition on the robot and MUST report no names.

#### Scenario: Recognition off
- **WHEN** the switch is false and `who_is_here` runs
- **THEN** the result contains a people count and no names

### Requirement: Recently recognized names only
`recognized_names` SHALL return only names recognized within the last 5 s.

#### Scenario: Person left
- **WHEN** a name was last recognized 10 s ago
- **THEN** `recognized_names` does not include it

### Requirement: People count
`people_count` SHALL return the number of people currently visible through `ALPeoplePerception`. No camera frame MUST leave the robot.

#### Scenario: Nobody visible
- **WHEN** the visible people list is empty
- **THEN** `people_count` returns 0

### Requirement: Who is here on the real robot
The bridge SHALL offer `who_is_here` when perception is available. When the probe at startup fails, it MUST keep rejecting the tool with `perception_off` and raise a console alert. Names MUST stay out of logs in event mode.

#### Scenario: Service missing
- **WHEN** `ALFaceDetection` is not available on the robot at startup
- **THEN** the bridge starts, the tool is not offered and the console shows an alert

#### Scenario: Event mode log
- **WHEN** `who_is_here` returns a name in event mode
- **THEN** the log contains no name
