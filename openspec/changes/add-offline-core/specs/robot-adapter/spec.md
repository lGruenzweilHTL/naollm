# Spec Delta

## Purpose

Defines the robot access contract the bridge depends on, so the transport (pip `qi`, Python 2 shim, ROS 2) can change, and the fake robot that lets the bridge run and be tested without hardware.

## ADDED Requirements

### Requirement: Single robot access contract
All robot access from the bridge SHALL go through one contract grouped into motion, speech, audio, events and perception (SPEC §6.1). No other part of the bridge MUST call a robot transport directly. Speed and fraction values MUST be fixed inside the adapter and never come from the LLM.

#### Scenario: Transport-independent bridge
- **WHEN** the bridge is started with the fake robot instead of a real transport
- **THEN** every bridge feature in this change works without code changes outside the adapter selection

### Requirement: Fake robot simulates robot state
The fake robot SHALL keep simulated posture, battery level, thermal level, fallen flag, motion activity, installed behaviors, installed TTS languages and joint limits. Tests and the operator MUST be able to set these values, and the fake robot MUST update posture and motion activity when actions run.

#### Scenario: Posture change
- **WHEN** the fake robot is asked to go to posture `Sit`
- **THEN** its reported posture is `Sit`

#### Scenario: Simulated low battery
- **WHEN** a test sets the fake battery to 15 %
- **THEN** the reported status shows 15 % battery

### Requirement: Fake robot records actions
The fake robot SHALL record every action in order with its arguments, so tests can assert what the robot would have done.

#### Scenario: Recorded speech and gesture
- **WHEN** the bridge makes the fake robot say "Hallo" and then play the `wave` gesture
- **THEN** the recorded actions are, in order, a speech action with text "Hallo" and an animation action for the configured `wave` target

### Requirement: Fake robot emits events
The fake robot SHALL let tests and the dry-run operator inject robot events: front, middle and rear head touch, fall, battery level change, thermal level change and connection loss. Injected events MUST reach the bridge the same way real events would.

#### Scenario: Injected head touch
- **WHEN** a test injects a middle head touch
- **THEN** the bridge receives a middle head touch event

### Requirement: Long actions are interruptible
Behaviors, keyframe animations and moves on the fake robot SHALL take simulated time and SHALL end early when a stop is requested, reporting that they were interrupted.

#### Scenario: Stop during dance
- **WHEN** a 10 s dance runs on the fake robot and stop is requested after 1 s
- **THEN** the dance ends within 0.5 s of the stop request and is reported as interrupted

### Requirement: Dry run prints actions
In dry-run mode, every action sent to the fake robot SHALL be printed to the console in a readable form instead of being executed on hardware.

#### Scenario: Dry-run speech
- **WHEN** the bridge runs with `--dry-run` and the robot says "Hallo"
- **THEN** the console shows a line containing the speech action and the text "Hallo"
