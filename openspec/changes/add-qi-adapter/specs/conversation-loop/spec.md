## MODIFIED Requirements

### Requirement: OpenAI-compatible endpoint
The bridge SHALL talk to any endpoint that implements OpenAI-compatible `/v1/chat/completions` with tools, using `llm.base_url`, `llm.api_key`, `llm.model`, `llm.temperature`, `llm.max_tokens` and `llm.timeout_s` from the configuration.

#### Scenario: Ollama defaults
- **WHEN** the default configuration is used
- **THEN** requests go to `http://localhost:11434/v1` with model `gemma4:e4b`, temperature 0.4 and at most 200 tokens
