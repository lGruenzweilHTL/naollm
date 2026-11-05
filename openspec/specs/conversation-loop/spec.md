# conversation-loop Specification

## Purpose

Defines one conversation turn from visitor text to spoken answer: how the LLM is called, how tool rounds work, how history is kept, and what the robot says when something fails.

## Requirements

### Requirement: OpenAI-compatible endpoint
The bridge SHALL talk to any endpoint that implements OpenAI-compatible `/v1/chat/completions` with tools, using `llm.base_url`, `llm.api_key`, `llm.model`, `llm.temperature`, `llm.max_tokens` and `llm.timeout_s` from the configuration.

#### Scenario: Ollama defaults
- **WHEN** the default configuration is used
- **THEN** requests go to `http://localhost:11434/v1` with model `gemma4:e2b`, temperature 0.4 and at most 200 tokens

### Requirement: Request content
Each request SHALL contain the system prompt for the turn language, the most recent `conversation.history_turns` turns, the new user text and the schemas of the currently enabled tools, with tool choice left to the model.

#### Scenario: History window
- **WHEN** 8 turns have happened and `history_turns` is 6
- **THEN** the next request contains only the 6 most recent turns plus the new user text

### Requirement: System prompt rules
The system prompt SHALL exist in German and English and SHALL instruct the model to: act as NAO at the school robotics club showcase, answer in at most two short friendly sentences, use tools only when they fit, never ask for personal information, never guess gender, mood or emotions, state it cannot see images, describe its abilities as dancing, gestures and talking about robotics, and explain tool errors simply.

#### Scenario: Language-specific prompt
- **WHEN** the turn language is English
- **THEN** the request uses the English system prompt and asks for an English answer

### Requirement: Sentences are spoken as they stream
Answer text SHALL be split into sentences and each sentence SHALL be spoken as soon as it is complete, without waiting for the whole answer.

#### Scenario: Two sentences
- **WHEN** the model streams "Hallo! Ich bin NAO."
- **THEN** "Hallo!" is sent to speech before "Ich bin NAO." has finished streaming

### Requirement: Tool rounds
When the model returns tool calls, each call SHALL go through the safety gate, its result SHALL be returned to the model, and the model SHALL be called again. At most `llm.max_tool_rounds` (default 3) rounds MUST happen per turn. If the limit is reached, the robot MUST say a fallback line and end the turn.

#### Scenario: Tool then answer
- **WHEN** the model first calls `dance` with `macarena` and then answers "Das hat Spaß gemacht!"
- **THEN** the dance runs, the result is sent back, and "Das hat Spaß gemacht!" is spoken

#### Scenario: Endless tool calls
- **WHEN** the model returns a tool call in every round
- **THEN** after 3 rounds the robot says the fallback line and no fourth request is sent

### Requirement: Speak before acting
If a response contains both text and a motion tool call, all of its sentences SHALL finish speaking before the motion starts. A response with only tool calls MUST NOT produce extra speech.

#### Scenario: Text and gesture in one response
- **WHEN** a response contains "Schau mal!" and a `play_gesture` call with `wave`
- **THEN** the recorded actions show the speech finishing before the gesture starts

### Requirement: Malformed tool calls
A tool call with invalid JSON, an unknown tool or an invalid argument SHALL produce an error result for the model once. If the model sends a malformed call again in the same turn, the bridge MUST stop offering tools for that turn and ask for a text answer.

#### Scenario: Bad JSON twice
- **WHEN** the model sends malformed tool-call JSON in two consecutive rounds
- **THEN** the third request contains no tools and the text answer is spoken

### Requirement: Timeouts and endpoint errors
If the endpoint does not answer within `llm.timeout_s` or returns an error, the robot SHALL say a short apology and the bridge SHALL return to `IDLE`.

#### Scenario: Timeout
- **WHEN** the endpoint does not respond within the timeout
- **THEN** the robot says a short apology and the state is `IDLE`

### Requirement: Cancelable turn
A running turn SHALL be cancelable by the emergency stop. After cancellation, no further tool call of that turn MUST run and no further text MUST be spoken.

#### Scenario: Emergency stop during a turn
- **WHEN** the emergency stop fires while the model is streaming a response with a pending tool call
- **THEN** the pending tool call does not run and nothing more is spoken

### Requirement: History stays in memory
Conversation history SHALL be held only in memory, SHALL be cleared after `conversation.idle_reset_s` (default 60 s) without a turn and on operator reset, and MUST never be written to disk. In event mode, logs MUST NOT contain user text or answer text.

#### Scenario: Idle reset
- **WHEN** 61 s pass after the last turn
- **THEN** the next request contains no earlier turns

#### Scenario: Event-mode log
- **WHEN** a turn completes in event mode
- **THEN** the log contains tool names, results and timings but not the user text or answer text
