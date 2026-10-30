# Design

## Context

Greenfield repository: only `SPEC.md` (v0.2) and OpenSpec scaffolding exist. See proposal.md for motivation. Constraints that shape the approach:

- No robot and no `qi` in the dev container. Everything must run against a fake robot. The real transport is chosen after the phase 0 spike, so nothing outside `robot/` may depend on it.
- The `qi` wheel may force an older Python. Code targets Python `>=3.10` (no 3.11+ only features such as `TaskGroup`, `ExceptionGroup`, `tomllib` or `Self`).
- Ollama is not reachable from the dev container. The conversation loop is tested with a stubbed endpoint; real models are tried on the operator laptop.
- Robot calls are blocking and robot events arrive on foreign threads (qi callbacks). The operator console (prompt_toolkit) and the streaming LLM client are asyncio-based.

## Goals / Non-Goals

**Goals:**
- One asyncio process that hosts the console, the conversation loop and the event watcher, with all robot calls off the event loop.
- Thread-safe state and gate, so robot events from any thread are handled correctly.
- Emergency stop that works from any context (hotkey, signal, robot event thread) and always completes.
- Every requirement scenario covered by a pytest test against the fake robot.

**Non-Goals:**
- Real `qi` adapter, audio, wake word, STT, deployment, perception (blocked on phase 0).
- The full screen layout of SPEC §18.2 with panes. This change uses a prompt with a bottom status toolbar and a printed event log above it.
- The `anim preview` command (needs a robot).

## Decisions

### Package layout follows SPEC §5, trimmed
`src/naollm/` with `config.py`, `state.py`, `robot/` (`adapter.py`, `fake_adapter.py`, `events.py`), `tools/` (`schemas.py`, `registry.py`, `gate.py`, `handlers.py`), `llm/` (`client.py`, `loop.py`, `prompts.py`, `sentences.py`), `admin/` (`cli.py`, `commands.py`, `estop.py`), `animations/` (`model.py`, `validate.py`, `player.py`, `import_choregraphe.py`, `limits.py`), `eventlog.py`, `main.py`. `audio/`, `deploy/`, `privacy/` and `admin/consent.py` come with later changes. Build backend: hatchling. Entry point `naollm = naollm.main:main`.

### Concurrency: asyncio outside, threads inside the adapter
The main process runs one asyncio loop. Blocking robot calls run through `asyncio.to_thread`. The state machine and gate use a `threading.RLock`, because robot events arrive on qi threads. Alternative considered: everything threaded, prompt_toolkit in a thread. Rejected because prompt_toolkit and the OpenAI streaming client are natively async and cancellation of a turn is much simpler with `asyncio.Task.cancel()`.

### Emergency stop is synchronous and thread-safe
`estop(source)` is a plain function, callable from any thread. It sets `STOPPED` under the lock first (so the gate refuses everything immediately), then requests cancellation of the running turn with `loop.call_soon_threadsafe(task.cancel)`, then calls the adapter's stop methods directly (they are designed to interrupt blocking calls in other threads), then `Crouch`, eyes red, log. Each step is wrapped in its own `try/except` that logs and continues. Alternative: an async estop. Rejected because robot event callbacks and signal handlers are not on the event loop.

### Interruptible long actions
Adapter long actions (behaviors, keyframes, moves) block until done. The fake implements them as a wait on a `threading.Event` with the simulated duration; `stop_all_behaviors` and `stop_move` set the event. The real adapter later maps these to NAOqi's stop calls, which end the blocking call on the robot side. Results report `finished` or `interrupted`.

### Gate as a pure pipeline over a snapshot
`Gate.check_and_run(call, source)` parses arguments against the generated JSON schema (hand-written checker for the small schema subset we emit: enum, integer with min/max, required, no extra keys), then checks state, preconditions and thermal level, takes the motion lock, runs the handler in a thread, and releases the lock in `finally`. It never raises; any exception becomes `{"ok": false, "error": ...}`. Error texts come from a small German/English message table keyed by reason, using the turn language. Alternative: `jsonschema` library. Rejected: one more dependency for a tiny subset, and we need custom child-friendly messages anyway.

### Tool catalogue built once per configuration change
`ToolRegistry.build(config, robot_info, animation_library)` produces the enabled tools and their schemas: gesture and dance enums from config filtered by installed behaviors and valid (and, in event mode, reviewed) keyframe animations; `turn`/`step` only when walking is on. The registry is rebuilt when the operator toggles walking, so the next LLM request sees the change.

Gesture target resolution: `animations/...` plays a built-in animation, `anim:<name>` plays a keyframe file, anything else runs an installed behavior.

### Conversation loop
`ConversationLoop.run_turn(text, language)` is an asyncio task. It streams the response, splits text deltas into sentences (regex on `.`, `!`, `?` followed by whitespace or end, with a guard against abbreviations and numbers like `3.5`), and pushes sentences to a speech queue served by a single worker that calls the blocking `say`. Tool-call deltas are accumulated by index. Before running tools, the loop awaits the speech queue drain (speak-before-act). Malformed calls get one error result; a second malformed round sends the next request without `tools`. History is a `deque` of completed turns; an idle timestamp check clears it before the next request if `idle_reset_s` elapsed.

Turn language: until STT exists, typed `chat` text uses `robot.language` from config, and `chat --en <text>` / `chat --de <text>` override it per turn. The loop already switches TTS language per turn through the adapter.

### Console
prompt_toolkit `PromptSession` with `patch_stdout` so log lines print above the prompt, a bottom toolbar refreshed every second for the status line, and key bindings: `c-e` runs estop; `space` with a filter "buffer is empty" requests listening, otherwise inserts a space. `Ctrl+C` inside the prompt arrives as `KeyboardInterrupt`; it is routed into the same two-stage handler as `SIGINT`. `SIGTERM` is installed with `loop.add_signal_handler`. A top-level `try/finally` in `main` guarantees estop then safe shutdown on unhandled exceptions. The command parser is a separate pure module (`commands.py`) so it is unit-tested without a terminal.

`listen` and the empty-line `space` hotkey only log "listening not available yet" until the audio change lands; the binding and its filter are what this change delivers.

### Event log and privacy
A single `EventLog` with structured entries (time, kind, source, fields). Rendering hides `text` fields when `event_mode` is true. Nothing is written to disk; the log lives in the console scroll-back. The cloud-endpoint check uses `ipaddress` on the resolved host of `llm.base_url` (loopback, private and link-local count as local; a hostname that is not `localhost` counts as remote unless it resolves to a private address).

### Keyframe animations
Pydantic model for the file format. Validator returns a list of violations (rule, joint, time, message) instead of raising. Joint limits: built-in NAO 6 reference table (`limits.py`, angles and max velocity from the NAO 6 documentation), replaced by `~/.naollm/robots/<id>.yaml` when a profile path is given. Speed check uses angle delta / time delta between consecutive keyframes (from 0 s and the posture angle for the first keyframe). Posture reference angles for `Stand`, `Sit`, `Crouch` are a built-in table of the touched joints.

The Choregraphe importer parses the export with `ast` and only evaluates the literal arguments of `names.append(...)`, `times.append(...)`, `keys.append(...)` with `ast.literal_eval`. It supports both key formats Choregraphe emits (plain angle and `[angle, [handle], [handle]]` Bezier lists, taking the angle).

The player calls the adapter's `set_balancer(True)`, `play_keyframes`, and `set_balancer(False)` in `finally`.

### Evaluation runner
`naollm eval <cases.yaml>` runs profile-tagged cases (SPEC §14.3) against the configured endpoint: each profile overlays `tests/eval/profiles/<profile>.yaml` onto the base config, sends one turn with the fake robot, and records the first tool call and gate outcome. It prints accuracy per profile. Not run in CI here (no endpoint); `pytest` only checks the runner against a stubbed endpoint.

### Tooling
venv at `/tmp/naollm-venv` (the container's root disk is almost full), `pip install -e .[dev]`. Tests with `pytest`. No linters in this change.

## Risks / Trade-offs

- [Ollama streaming of tool calls differs from OpenAI's delta format] → the accumulator keys on `index` and tolerates complete tool calls arriving in one delta; tested with both shapes in the stub.
- [Signal handling differs on Windows (no `add_signal_handler`)] → fall back to `signal.signal` for `SIGINT`; documented. Operator laptop OS is still open (SPEC §17).
- [Built-in joint limits differ from the actual robot] → limits from the robot profile replace them once `naollm init` exists; the validator always names its source.
- [prompt_toolkit key bindings are hard to test without a terminal] → bindings are thin wrappers over tested functions; one test drives a `PromptSession` with prompt_toolkit's pipe input.
- [Sentence splitting misfires on abbreviations] → small German/English abbreviation list; worst case one sentence is split in two, which is still spoken correctly.
- [Thread-safety bugs between event callbacks and the loop] → all shared state behind one lock; tests inject events from a separate thread during a running turn.

## Open Questions

- Operator laptop OS (affects only signal-handling fallback and venv instructions in the README).
