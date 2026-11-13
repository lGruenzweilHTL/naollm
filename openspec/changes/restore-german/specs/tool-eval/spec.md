## ADDED Requirements

### Requirement: Score per language
The eval report SHALL show the score per profile and per prompt language, with `de` or `en` taken from each case's `lang` field. A case without `lang` MUST count as `de`, as the runner does today. The matrix comparison table MUST show one column per language.

#### Scenario: Weak German
- **WHEN** a run scores 95 % on English cases and 80 % on German cases in `event_default`
- **THEN** the report shows both numbers for `event_default`, and the German one is not hidden in an average

#### Scenario: Case without a language
- **WHEN** a case has no `lang` field
- **THEN** it is counted as German
