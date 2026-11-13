## 1. Probe and legal gate

- [ ] 1.1 Confirm `add-faces-consent` is archived; stop here otherwise
- [x] 1.2 Add a read-only probe to `spike/spike.py`: check for `ALFaceCharacteristics`, run one `analyzeFaceCharacteristics` on a consenting club member, and list which `PeoplePerception/Person/<id>/` keys the call writes, without logging their values. Ask the user to confirm consent before running it
- [ ] 1.3 Record the results in `spike/FINDINGS.md`. If the call writes expression or smile values, stop here and ask the user to get written clearance from the school's data-protection contact

## 2. Adapter

- [x] 2.1 Add `estimate_age() -> AgeEstimate | None` to the `Perception` protocol, with a fake that tests can set
- [x] 2.2 Implement it in `QiPerception`: one visible person, `analyzeFaceCharacteristics`, read only `AgeProperties`
- [x] 2.3 Add a startup probe that hides the tool and raises an alert when the service is missing
- [x] 2.4 Extend `tests/qi_double.py` to fail on reads of gender, smile and expression keys

## 3. Tool, gate and prompt

- [x] 3.1 Add the `guess_age` schema with no parameters, registered only when the game is enabled and the probe succeeded
- [x] 3.2 Add the handler: people count check, confidence check, 5-year range, spoken by the handler in the turn language, result without a number
- [x] 3.3 Add the `age_game_off` gate reason and the spoken messages in German and English
- [x] 3.4 Add the age-game rule to both prompts, in motors-on and motors-off variants
- [x] 3.5 Reject `motion.walking_enabled: true` with the game on in config loading, and make `walk on` refuse

## 4. Operator visibility

- [x] 4.1 Add the startup warning and the status bar flag
- [x] 4.2 Add the age-game sentence to `notice`, and a signage line to the README

## 5. Tests and eval

- [x] 5.1 Unit-test every scenario in the `age-game` spec, including that no number reaches the LLM messages, history or log
- [x] 5.2 Add `tests/eval/profiles/age_game_on.yaml` and cases in German and English: explicit requests expect `guess_age`, questions about the robot's age and unrelated questions expect no tool
- [x] 5.3 Run the eval on the event model and record the score for the new profile

## 6. On the robot (no motors needed)

- [ ] 6.1 Ask the user to confirm consent, then with one consenting club member, check a guess, a refusal with two people, and a refusal at low light; record the confidences
