## MODIFIED Requirements

### Requirement: System prompt rules
The system prompt SHALL exist in German and English and SHALL instruct the model to: act as NAO at the school robotics club showcase, answer in at most two short friendly sentences, use tools only when they fit, never ask for personal information, never guess gender, mood or emotions, state it cannot see images, describe its abilities as dancing, gestures and talking about robotics, and explain tool errors simply. With the age game enabled, it MUST also instruct the model to guess an age only with `guess_age`, only when the visitor asks for it, and never to say a number itself.

#### Scenario: Language-specific prompt
- **WHEN** the turn language is English
- **THEN** the request uses the English system prompt and asks for an English answer

#### Scenario: Age game rule
- **WHEN** the prompt is built with the age game enabled
- **THEN** it contains the age-game rule, and without the game it does not
