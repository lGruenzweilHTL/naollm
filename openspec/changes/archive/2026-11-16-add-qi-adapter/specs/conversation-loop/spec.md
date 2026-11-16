## MODIFIED Requirements

### Requirement: OpenAI-compatible endpoint
The bridge SHALL talk to any endpoint that implements OpenAI-compatible `/v1/chat/completions` with tools, using `llm.base_url`, `llm.api_key`, `llm.model`, `llm.temperature`, `llm.max_tokens` and `llm.timeout_s` from the configuration.

#### Scenario: Ollama defaults
- **WHEN** the default configuration is used
- **THEN** requests go to `http://localhost:11434/v1` with model `gemma4:e4b`, temperature 0.4 and at most 200 tokens

## ADDED Requirements

### Requirement: Reasoning is not spoken
The client SHALL report reasoning text from the endpoint, in `reasoning` or `reasoning_content`, separately from the answer text, and the conversation loop SHALL never speak it or keep it in the history. The client SHALL also report when a tool call starts, the finish reason and the token usage.

#### Scenario: Thinking model
- **WHEN** the endpoint streams reasoning, then "Sure!", then a `dance` call
- **THEN** the robot says only "Sure!" and the `dance` call runs
