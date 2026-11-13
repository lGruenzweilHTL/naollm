## ADDED Requirements

### Requirement: Animated speech with motors on
With motors on, `say(text, animated=True)` SHALL use `ALAnimatedSpeech.say`, and `say(text, animated=False)` SHALL use `ALTextToSpeech.say`. `stop_speech` MUST stop both.

#### Scenario: Normal answer
- **WHEN** the conversation loop speaks a sentence with motors on
- **THEN** `ALAnimatedSpeech.say` receives the sentence

#### Scenario: Motion tool follows speech
- **WHEN** a response holds text and a `dance` call
- **THEN** `ALAnimatedSpeech.say` returns before the behavior starts

### Requirement: Head tracking
With motors on, `set_tracking(True)` SHALL start `ALBasicAwareness` with head-only tracking, and `set_tracking(False)` SHALL stop it. Tracking MUST stop before a motion tool runs and MUST resume when the tool ends, unless the state is STOPPED or FALLEN.

#### Scenario: Tracking during a dance
- **WHEN** a dance starts while tracking is on
- **THEN** tracking stops before the behavior and resumes after it ends

#### Scenario: Tracking after an emergency stop
- **WHEN** the emergency stop fires
- **THEN** tracking is off and stays off until `resume`
