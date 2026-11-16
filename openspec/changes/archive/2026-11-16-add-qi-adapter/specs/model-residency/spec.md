## ADDED Requirements

### Requirement: Model warm-up at start
`naollm run` SHALL load `llm.model` through the Ollama native API with `keep_alive: -1` before it reports ready. A failed load MUST raise a console alert and MUST NOT stop the start. `naollm admin` MUST NOT load the model at start.

#### Scenario: Ollama endpoint
- **WHEN** `naollm run` starts and `llm.base_url` is `http://localhost:11434/v1`
- **THEN** `POST http://localhost:11434/api/generate` receives the model name and `keep_alive: -1` before the console reports ready

#### Scenario: Endpoint without the native API
- **WHEN** the load request fails or returns an error
- **THEN** the console shows an alert that the first turn will be slower, and the bridge starts

#### Scenario: Admin mode
- **WHEN** `naollm admin` starts
- **THEN** no load request is sent

### Requirement: Model release at exit
When this process loaded the model, every exit of the bridge SHALL unload it with `keep_alive: 0`. This includes `quit`, a second Ctrl+C, SIGTERM and an unhandled error. A failed unload MUST only be logged. The bridge MUST NOT unload at exit a model that it did not load.

#### Scenario: Quit
- **WHEN** the operator types `quit` after a successful warm-up
- **THEN** `POST /api/generate` receives the model name and `keep_alive: 0`

#### Scenario: Warm-up failed
- **WHEN** the warm-up failed and the operator types `quit`
- **THEN** no unload request is sent

### Requirement: Chat requests keep the model loaded
While this process holds the model, chat requests SHALL ask Ollama to keep it loaded. After `model unload`, chat requests MUST NOT carry a keep-alive value.

#### Scenario: Turn after warm-up
- **WHEN** a turn runs after a successful warm-up
- **THEN** the chat request carries `keep_alive: -1`
