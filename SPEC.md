# SPEC — NAO 6 × LLM Bridge (Robotics Club Showcase)

Status: Draft v0.3 · 2026-11-05

Changelog:
- v0.3 (2026-11-05): recorded the interim state of the real robot (see "Interim: English only, motors off" below). Added `audio.no_speech_timeout_s` and `robot.battery_reliable`, `robot.listen_ip`, `motion.motors_enabled`; chose `webrtcvad-wheels` for VAD; dropped `audio/robot_stream.py` and `audio/wake.py` for `robot/qi_adapter.py` and `audio/listener.py`; adapted `naollm init` and face enrolment to motors off; added an English notice and signage.
- v0.2 (2026-10-30): resolved review conflicts. Gate rejects out-of-range arguments (no clamping); LLM `stop` is a soft stop; status colours removed from `set_eyes`; gesture enum generated from config; speech finishes before motion; wake-word ASR fixed to German; `RobotAdapter` split into sub-interfaces; eval cases tagged with config profiles; listen hotkey `space` only on an empty prompt line; wake-word commands renamed to `ears`; phase 0 spike kept outside OpenSpec in `spike/`.
- v0.1 (2026-10-28): first draft.
Scope: A local, privacy-first system that lets a NAO 6 robot hold spoken conversations driven by a local LLM (via an OpenAI-compatible API) and perform movements through tool calls. Target deployment: robotics club showcase at school.

---

## Interim: English only, motors off

The phase 0 spike (`spike/FINDINGS.md`) found two problems on the real robot. This section overrides the rest of the SPEC until they are solved. Sections below still describe the target design.

**Motors off.** The robot's battery is defective, so no motor may be used. The real adapter contains no code that commands a motor: its motion methods raise `MotorsDisabled` without calling NAOqi, and `tests/qi_double.py` fails on any NAOqi call outside an allowlist.

- `motion.motors_enabled` must be `false` for a real run; otherwise the bridge refuses to start. The fake robot works with either value.
- Startup refuses unless the robot is at rest and Autonomous Life is `disabled`, and it changes nothing itself. Only `naollm init` can disable Autonomous Life, after the operator confirms. A poll every 5 s reports a later wake-up with an alert and STOPPED, and sends no motor command.
- With motors off, §6.3, §6.4, §10.3 and §18.3 skip every motion step and keep the rest. The gate rejects motion tools with the reason `motors_off`, the LLM is neither offered them nor told it can move, and `wake`, `rest`, `posture`, `walk on` and `anim play` refuse.
- Speech always uses `ALTextToSpeech`, never `ALAnimatedSpeech`. `ALFaceDetection` head tracking is switched off before perception starts.
- Blocked until the battery is replaced: behaviors, gestures, dances, keyframe playback, postures, head tracking, `qipkg` deploy and `naollm deploy`.

**English only.** The robot has neither German TTS nor German ASR. `config.example.yaml` configures English, with the wake phrase `Hey NAO` and `audio.wake_threshold: 0.4`, because the phrase scored 0.43 and 0.44 in the spike. Code defaults stay German, and German returns through configuration alone once a language pack is installed; the comments in the example say how. A turn whose language has no installed voice runs in `robot.language`.

**Battery.** `robot.battery_reliable: false` hides the value, because this robot reads 100 % whatever its charge. Battery events are then only logged.

---

## 1. Goals and non-goals

### 1.1 Goals

1. NAO answers spoken questions in natural language, with automatic speaking gestures.
2. The LLM can trigger robot actions through **tool calls**: gestures, dances, posture, head direction, small turns/steps, eye colour, status queries.
3. Everything runs **locally**: robot + one laptop + own router. No cloud services required.
4. Any **OpenAI-compatible chat-completions endpoint** works (default: Ollama on the laptop with `gemma4:e4b`).
5. Custom animations are authored in **Choregraphe** or as **keyframe data files** (§19) and triggered by name from the bridge.
6. A fresh robot is set up with one command (`naollm init`, §20).
7. Safe by construction: the LLM chooses *what*, deterministic code decides *whether* and *how far*.
8. Compliant with GDPR / Austrian DSG and the EU AI Act for use at a school event.

### 1.2 Non-goals

- No raw camera frames or video ever sent to any model. No vision model.
- No emotion/expression recognition. No gender prediction.
- No persistent storage of audio, transcripts or images.
- No free-form joint control by the LLM.
- No on-robot LLM inference; no code running in Choregraphe boxes beyond animations.
- No autonomous walking around a crowd.

---

## 2. System overview

```
┌──────────────────────┐   qi / TCP 9559   ┌─────────────────────────────┐   HTTP (localhost)   ┌──────────────┐
│ NAO 6 (NAOqi 2.8)    │◄─────────────────►│ Bridge (laptop, Python 3)   │◄────────────────────►│ Ollama       │
│ - TTS / animated     │                   │ - audio in + STT            │  OpenAI-compatible   │ gemma4:e4b   │
│   speech             │                   │ - conversation loop         │  /v1/chat/completions│ (or any      │
│ - motors / posture   │                   │ - tool registry             │                      │  compatible) │
│ - installed          │                   │ - SAFETY GATE               │                      └──────────────┘
│   Choregraphe        │                   │ - robot adapter             │
│   behaviors          │                   │ - admin CLI (§18)           │
│ - face tracking      │                   │ - event watcher (stop/fall) │
│ - touch sensors      │                   └─────────────────────────────┘
└──────────────────────┘
```

**Key principle:** the robot never talks to the LLM. The laptop bridge is the only component that talks to both.

### 2.1 How the parts run together

1. **Before the event:** Choregraphe is used to build animations (dances, bow, …). They are installed on the robot via *Robot applications → Package and install*. After that, Choregraphe can be closed; the behaviors live on the robot under paths like `showcase/dance_macarena`.
2. **During the event:** the bridge connects to NAOqi on port 9559 using the Python `qi` library. It calls robot services (`ALAnimatedSpeech`, `ALBehaviorManager`, `ALMotion`, …) directly.
3. **Per turn:** speech → text → LLM → (optional tool calls → safety gate → robot) → spoken answer.

### 2.2 One conversation turn (sequence)

1. A visitor says the wake word (or taps NAO's front head, or the operator presses `space`). Eyes turn **blue**.
2. Audio from NAO's microphones is streamed to the laptop until silence. Eyes turn **yellow** ("thinking"); optional `think` gesture.
3. STT produces text.
4. Bridge sends `system prompt + recent history + user text + tool schemas` to the LLM.
5. If the response contains tool calls: each is validated by the **safety gate**, executed on the robot, and the result is returned to the LLM. Max 3 tool rounds per turn.
6. Final text answer is split into sentences and spoken via `ALAnimatedSpeech` as it streams in. If a response contains both text and a motion tool call, the queued sentences finish first; then the motion runs (§7.2).
7. Eyes return to **white**. History is updated (in memory only).

---

## 3. Hardware and network

| Item | Requirement |
| --- | --- |
| Robot | NAO 6, NAOqi 2.8.x (check exact version in phase 0), charged + charger on site |
| Laptop | ≥ 16 GB RAM recommended (default gemma4:e4b is a 6.6 GB model; gemma4:e2b: 8 GB minimum), Linux/macOS/Windows |
| Router | Own small Wi-Fi router; robot and laptop on the same LAN. School Wi-Fi often blocks device-to-device traffic. |
| Laptop ↔ router | Ethernet preferred |
| Microphone | NAO's own microphones (front mic) from v1 |
| Listening triggers | Wake word, front head tap, operator key (§11.1) |
| Area | Robot on the **floor**, inside a marked area. Never on a table while motion tools are enabled. |

---

## 4. Technology stack

| Layer | Choice | Notes |
| --- | --- | --- |
| Bridge language | Python 3 (version determined by available `qi` wheel) | |
| Robot API | `qi` (libqi Python bindings, `pip install qi`) | **Phase 0 must verify** Python 3 ↔ NAOqi 2.8 compatibility. Fallbacks in §13. |
| LLM client | `openai` Python SDK with configurable `base_url` | Ollama: `http://localhost:11434/v1`, api_key `"ollama"` |
| Default model | `gemma4:e4b` via Ollama ≥ 0.20 | Swappable via config. `gemma4:e2b` is the fallback for laptops with 8 GB. |
| STT | `faster-whisper` (model `small` or `base`) on the laptop | Optional experiment: Gemma 4 E2B audio input. |
| Config | YAML + pydantic validation | |
| Admin CLI | `prompt_toolkit` (command prompt + global hotkeys) + `rich` (status output) | Local only; see §18. |
| Wake word | On-robot `ALSpeechRecognition` word spotting | §11.1 |
| VAD | `silero-vad` or `webrtcvad` | End-of-utterance detection on the laptop |
| Packaging | `qipkg` (`pip install qipkg`) | Builds/deploys Choregraphe packages from the CLI (§20). Python version compatibility verified in phase 0. |
| Animations | Choregraphe (NAOqi 2.8 version) | |
| Tests | `pytest` with a fake robot adapter | |

Rust (`aldebaran/libqi-rs`) is experimental and out of scope.

---

## 5. Repository layout

```
naollm/
├── SPEC.md
├── README.md
├── pyproject.toml
├── config.example.yaml
├── src/naollm/
│   ├── main.py              # entry point, wiring, lifecycle
│   ├── config.py            # pydantic config model
│   ├── state.py             # robot/bridge state machine
│   ├── robot/
│   │   ├── adapter.py       # RobotAdapter + sub-interfaces (§6.1)
│   │   ├── qi_adapter.py    # real implementation over qi
│   │   ├── fake_adapter.py  # in-memory fakes, one per sub-interface, for tests / dry runs
│   │   └── events.py        # touch, fall, battery, temperature subscriptions
│   ├── llm/
│   │   ├── client.py        # OpenAI-compatible client wrapper
│   │   ├── loop.py          # turn loop, tool rounds, streaming → TTS
│   │   └── prompts.py       # system prompt(s), per language
│   ├── tools/
│   │   ├── schemas.py       # JSON schemas exposed to the LLM
│   │   ├── registry.py      # name → handler mapping, enum generation
│   │   ├── gate.py          # SAFETY GATE (validation, range checks, preconditions)
│   │   └── handlers.py      # tool implementations calling RobotAdapter
│   ├── audio/
│   │   ├── listener.py      # triggers, wake word pause rules, listening flow, handoff to the turn
│   │   ├── vad.py           # end-of-utterance detection
│   │   └── stt.py           # faster-whisper wrapper
│   ├── animations/
│   │   ├── model.py         # keyframe animation data model (§19)
│   │   ├── validate.py      # joint limits, speed, start/end pose, balance rules
│   │   ├── player.py        # plays via ALMotion.angleInterpolationBezier
│   │   └── import_choregraphe.py  # converts Choregraphe "export to Python" timelines
│   ├── deploy/
│   │   ├── init.py          # `naollm init` (§20)
│   │   ├── package.py       # wraps qipkg make-package / deploy-package
│   │   └── selftest.py      # on-robot checks
│   ├── admin/
│   │   ├── cli.py           # admin prompt, command parser, hotkeys (§18)
│   │   ├── commands.py      # command implementations
│   │   ├── estop.py         # emergency-stop routine + SIGINT/SIGTERM handlers
│   │   ├── consent.py       # consent registry for enrolled faces
│   │   └── faces.py         # `faces` commands and guided enrolment
│   └── privacy/
│       └── policy.py        # central switches for privacy-relevant features
├── choregraphe/
│   └── showcase/            # Choregraphe project: showcase.pml + manifest.xml (package id "showcase")
├── animations/              # keyframe animations as data files (*.anim.yaml), reviewed via git
├── spike/                   # phase 0 connectivity spike script + findings (§16), not part of the package
└── tests/
    ├── test_gate.py
    ├── test_tools.py
    ├── test_loop.py
    └── eval/
        ├── tool_calls.yaml  # prompt → expected tool call cases, each tagged with a profile
        └── profiles/        # config overlays per profile (event_default, walking_on, …)
```

---

## 6. Robot adapter

All robot access goes through one interface so it can be faked in tests and swapped if the connection technology changes (pip `qi`, Python 2 shim, ROS 2). The interface is split into sub-interfaces. Each sub-interface has its own fake, so audio, events and animation playback can be tested without a robot. Every transport (pip `qi`, Python 2 shim, ROS 2) must implement the full set.

### 6.1 Interface

```python
class Motion(Protocol):
    def run_behavior(self, path: str) -> None             # blocking until done
    def stop_all_behaviors(self) -> None
    def installed_behaviors(self) -> list[str]
    def play_animation(self, path: str) -> None           # built-in ALAnimationPlayer path
    def play_keyframes(self, anim: Animation) -> None     # §19, blocking, interruptible by stop
    def set_balancer(self, enabled: bool) -> None         # whole-body balancer + both feet fixed
    def go_to_posture(self, name: str, speed: float) -> bool
    def move_to(self, x_m: float, y_m: float, theta_rad: float) -> None
    def stop_move(self) -> None
    def set_head(self, yaw_rad: float, pitch_rad: float, speed: float) -> None
    def joint_limits(self) -> dict[str, tuple[float, float]]
    def rest(self) -> None
    def wake_up(self) -> None

class Speech(Protocol):
    def say(self, text: str, animated: bool = True) -> None
    def set_language(self, language: str) -> None         # TTS only; never changes wake-word ASR
    def installed_languages(self) -> list[str]
    def set_eyes(self, rgb_hex: str, duration_s: float) -> None

class Audio(Protocol):
    def start_stream(self, on_chunk: Callable[[bytes], None]) -> None  # ALAudioDevice remote, front mic, 16 kHz mono
    def stop_stream(self) -> None
    def configure_wake_words(self, words: list[str], threshold: float) -> None  # German ASR (§11.1)
    def set_wake_enabled(self, enabled: bool) -> None    # paused while speaking / moving

class Events(Protocol):
    def subscribe(self, handler: Callable[[RobotEvent], None]) -> None  # tactile, fall, battery, temperature, wake word, connection
    def unsubscribe_all(self) -> None
    def status(self) -> RobotStatus                       # battery %, posture, fallen, hot joints, moving

class Perception(Protocol):
    def set_tracking(self, enabled: bool) -> None         # ALBasicAwareness on/off
    def people_count(self) -> int
    def recognized_names(self) -> list[str]               # enrolled + consenting only
    def learn_face(self, name: str) -> bool               # operator only
    def forget_face(self, name: str) -> bool              # operator only

class RobotAdapter(Protocol):
    motion: Motion
    speech: Speech
    audio: Audio
    events: Events
    perception: Perception
    def connect(self, url: str) -> None
    def disconnect(self) -> None
    def info(self) -> RobotInfo                           # NAOqi version, robot id, name
```

### 6.2 NAOqi mapping

| Adapter method | NAOqi service / call |
| --- | --- |
| `say` | `ALAnimatedSpeech.say` (animated) / `ALTextToSpeech.say` |
| `set_language`, `installed_languages` | `ALTextToSpeech.setLanguage`, `.getAvailableLanguages` |
| `play_keyframes`, `set_balancer` | `ALMotion.angleInterpolationBezier` / `.angleInterpolation`, `ALMotion.wbEnable`, `.wbFootState` |
| `joint_limits` | `ALMotion.getLimits` |
| `start_stream`, `stop_stream` | `ALAudioDevice` remote module subscription (`setClientPreferences`, `subscribe`) |
| `configure_wake_words`, `set_wake_enabled` | `ALSpeechRecognition.setVocabulary` (word spotting), `.pause` |
| `subscribe` | `ALMemory` events (`FrontTactilTouched`, `MiddleTactilTouched`, `RearTactilTouched`, `robotHasFallen`, `WordRecognized`, battery/temperature) |
| `run_behavior`, `stop_all_behaviors`, `installed_behaviors` | `ALBehaviorManager.runBehavior`, `.stopAllBehaviors`, `.getInstalledBehaviors` |
| `play_animation` | `ALAnimationPlayer.run` (e.g. `animations/Stand/Gestures/Hey_1`) |
| `go_to_posture` | `ALRobotPosture.goToPosture` |
| `move_to`, `stop_move` | `ALMotion.moveTo`, `ALMotion.stopMove` |
| `set_head` | `ALMotion.setAngles(["HeadYaw","HeadPitch"], …)` |
| `set_tracking` | `ALBasicAwareness` start/stop (head tracking of the conversation partner) |
| `set_eyes` | `ALLeds.fadeRGB("FaceLeds", …)` |
| `status` | `ALBattery`, `ALRobotPosture.getPosture`, `ALMemory` (fall state, temperature), `ALMotion.moveIsActive` |
| `people_count` | `ALPeoplePerception` / `ALMemory` visible-people data |
| `recognized_names`, `learn_face`, `forget_face` | `ALFaceDetection` recognition + learn/forget |
| `rest`, `wake_up` | `ALMotion.rest`, `ALMotion.wakeUp` |

Exact method signatures are verified against the NAOqi 2.8 API reference during phase 0/2. Speed/fraction parameters are fixed constants in the adapter, never exposed to the LLM.

### 6.3 Startup sequence

1. Connect to `tcp://<robot-ip>:9559`. Retry with backoff; fail loudly in the console.
2. Set Autonomous Life to a quiet mode (or disabled) so it does not fight the bridge.
3. `wake_up()`, `go_to_posture("Stand")`, enable head tracking.
4. Compare `installed_behaviors()` with the dance/gesture catalogue from config. Warn for missing ones and remove them from the tool enums.
5. Subscribe to events (§10.3).
6. Eyes white, say a short ready line.

### 6.4 Shutdown

Stop behaviors and motion → `go_to_posture("Crouch")` → `rest()` → unsubscribe → disconnect. Also on Ctrl+C and on unhandled exceptions.

---

## 7. LLM integration

### 7.1 Client

- OpenAI-compatible `/v1/chat/completions` with `tools` and `tool_choice: "auto"`.
- Config: `base_url`, `api_key`, `model`, `temperature` (default 0.4), `max_tokens` (default 200), `timeout_s` (default 20), `reasoning_effort` (default `none`, which turns thinking off; `null` leaves the field out). Reasoning text is never spoken.
- Model residency (Ollama only): `naollm run` loads `model` through the native `POST /api/generate` with `keep_alive: -1` before it reports ready, sends `keep_alive: -1` with chat requests while it holds the model, and unloads it with `keep_alive: 0` on every exit path. A failed load raises an alert, and the bridge starts anyway.
- Streaming enabled. Text deltas are buffered into sentences and spoken one at a time. Tool-call deltas are accumulated until complete.

### 7.2 Turn loop

```
messages = [system] + history[-N:] + [user]
for round in 1..MAX_TOOL_ROUNDS (3):
    response = llm(messages, tools)
    stream text sentences → robot.say
    if no tool_calls: break
    wait until all queued sentences are spoken      # speak first, then act
    for call in tool_calls:
        result = gate.check_and_run(call)      # never raises; returns {"ok": ..., ...}
        messages += [assistant(tool_call), tool(result)]
if rounds exhausted: say fallback line ("Da bin ich durcheinander gekommen.")
history.append(user, final assistant text)
```

Rules:

- **History**: keep last 6 turns. **Reset history** after 60 s of inactivity or when the operator presses "new visitor". History is never written to disk.
- **Malformed tool calls** (bad JSON, unknown tool, invalid enum): return an error result to the model once; if it fails again, skip tools and answer in text.
- **Text inside tool-call-only responses**: if the model returns only a tool call and no text, the bridge says nothing extra; the next round produces the spoken reply.
- **Timeouts**: on LLM timeout, say a short apology and return to idle.
- Tools that take long (dances) run **blocking** before the next LLM round; the model receives `{"ok": true, "result": "finished"}`.
- **Speech before motion**: if a response contains text and a motion tool call, all queued sentences finish before the motion lock is taken. `ALAnimatedSpeech` gestures and a motion tool never overlap.

### 7.3 System prompt (requirements)

Language configurable (default German). The prompt must state:

- You are NAO, a small humanoid robot at the robotics club showcase of <school>.
- Answer in at most two short sentences, friendly, suitable for children and teenagers.
- Use tools to move; only use tools when it fits the conversation or the visitor asks.
- Do not ask for names, ages, addresses or other personal information.
- Do not guess anyone's gender, mood or emotions. You cannot see images.
- If asked what you can do, mention dancing, gestures, and talking about robotics.
- If a tool returns an error, explain it simply (e.g. "Dafür muss ich erst aufstehen").

Prompt text lives in `llm/prompts.py`, one per language, versioned in git.

### 7.4 Small-model constraints

- ≤ 10 tools exposed at once.
- Enums instead of free numbers wherever possible.
- One-line descriptions; no nested objects.
- Tool set can be reduced by config (e.g. hide `step` and `turn` when walking is disabled — hidden tools are not sent to the model at all).

---

## 8. Tools

### 8.1 Result format (all tools)

```json
{"ok": true,  "result": "<short human-readable outcome>"}
{"ok": false, "error":  "<short reason the model can repeat to a child>"}
```

### 8.2 LLM-callable tools

| Tool | Parameters | Robot action | Gate rules |
| --- | --- | --- | --- |
| `play_gesture` | `name`: enum generated from the `gestures` config (default set: `wave`, `bow`, `nod`, `shake_head`, `shrug`, `think`, `celebrate`) | Built-in animation, Choregraphe behavior or keyframe file (mapped in config) | Only entries present on the robot / reviewed (§6.3, §19.3); not while another motion runs |
| `dance` | `name`: enum generated from installed dances in config | `run_behavior` | Must be standing; battery ≥ 30 %; one at a time |
| `set_posture` | `posture`: `Stand`, `Sit`, `Crouch` | `go_to_posture` | Not while moving |
| `look` | `direction`: `left`, `right`, `up`, `down`, `at_person` | `set_head` with angles from a fixed table, then back to tracking after 3 s | Angles from a fixed table |
| `turn` | `degrees`: integer −90…90 | `move_to(0, 0, θ)` | Standing; walking toggle ON; out of range → rejected with reason; not while moving |
| `step` | `direction`: `forward`, `back`, `left`, `right`; `cm`: integer 5…30 | `move_to` | Standing; walking toggle ON; out of range → rejected with reason; total displacement from start ≤ 1 m |
| `set_eyes` | `color`: `white`, `purple`, `orange`, `pink`, `cyan` | `set_eyes` | Status colours (blue = listening, yellow = thinking, red = e-stop, green = enrolment) are not in the enum; eyes return to the status colour after 5 s |
| `get_status` | — | `status()` | Read-only; returns battery %, posture, "ok/warm/hot" |
| `who_is_here` | — | `people_count()` + `recognized_names()` | Returns only a count and names of enrolled, consenting members |
| `stop` | — | **Soft stop**: stop behaviors + stop move, stay in current posture, state back to IDLE | Always allowed; does **not** enter STOPPED (the hard e-stop, §18.3, is operator / head-touch / signal only) |

### 8.3 Example schema

```json
{
  "type": "function",
  "function": {
    "name": "dance",
    "description": "Perform one of your dances.",
    "parameters": {
      "type": "object",
      "properties": {
        "name": {"type": "string", "enum": ["macarena", "robot", "disco"]}
      },
      "required": ["name"],
      "additionalProperties": false
    }
  }
}
```

### 8.4 Operator-only actions (never exposed to the LLM)

`enroll_face(name)`, `forget_face(name)`, `forget_all_faces()`, walking toggle, age-game toggle, new visitor (history reset), emergency stop, rest/wake, reconnect. All of these, plus manual triggering of every LLM tool, are available in the **admin CLI** (§18).

### 8.5 Explicitly excluded

- Raw camera / screenshot / video tools.
- Expression, smile or mood detection.
- Gender estimation.
- Arbitrary joint angles, speed parameters, or behavior paths as free strings.

### 8.6 Optional, disabled by default: age-guess game

- Only if `privacy.age_game_enabled: true` and announced on signage.
- Triggered only when a visitor explicitly asks ("Rate mal, wie alt ich bin").
- Single estimate from NAO's built-in face characteristics, spoken once as a rough range, never stored, never added to history in numeric form.

---

## 9. Choregraphe conventions

1. One Choregraphe project, **package id `showcase`**.
2. One behavior per animation, folder name = tool enum name: `dance_macarena`, `gesture_bow`, …
3. Each behavior must:
   - start and end in **Stand**,
   - finish within 30 s,
   - not use text-to-speech or speech recognition boxes (speech is owned by the bridge),
   - not be set to auto-start.
4. Install either via *Package and install* in Choregraphe or from the command line with `naollm deploy` / `naollm init` (§20), which wrap `qipkg make-package` and `qipkg deploy-package`. Verify on startup via `installed_behaviors()`.
5. Keep `manifest.xml` next to `showcase.pml`; its `uuid` (`showcase`) and `version` are what the robot's PackageManager uses. `naollm deploy` bumps the version automatically.
6. Simple, single-pose-sequence gestures should preferably be stored as keyframe data files (§19) instead of Choregraphe behaviors; Choregraphe stays the tool for complex multi-box behaviors and as a visual editor.
7. Config maps enum names to paths:

```yaml
dances:
  macarena: showcase/dance_macarena
  robot: showcase/dance_robot
gestures:
  wave: animations/Stand/Gestures/Hey_1     # built-in
  bow: showcase/gesture_bow                 # custom
```

8. During development Choregraphe may stay connected; never trigger motions from Choregraphe and the bridge at the same time.

---

## 10. Safety

### 10.1 State machine

`OFFLINE → RESTING → IDLE ⇄ LISTENING → THINKING ⇄ ACTING → IDLE`, plus `STOPPED` and `FALLEN` (both entered from any state).

- `ACTING` holds a single motion lock; a second motion tool returns `{"ok": false, "error": "Ich bewege mich gerade schon."}`.
- `STOPPED`: all motion tools refused until the operator resumes.
- `FALLEN`: all motion tools refused; operator places the robot upright and resumes.

### 10.2 Gate checks (in order)

1. Tool exists and is enabled in config.
2. Arguments validate against the schema (enum, ranges); out-of-range values are **rejected, not clamped silently**, with a reason.
3. State allows motion (not STOPPED/FALLEN/RESTING, not already ACTING).
4. Preconditions: posture (e.g. Stand for dance/turn/step), walking toggle, battery thresholds, displacement budget.
5. Thermal status not "hot".
6. Execute with fixed speeds; release lock in `finally`.

### 10.3 Event watcher (independent of the LLM)

| Event | Reaction |
| --- | --- |
| Middle or rear head tactile sensor touched | **Emergency stop**: stop behaviors, stop move, Crouch, state STOPPED, eyes red |
| Front head tactile sensor touched | Start listening (§11.1); while ACTING it is ignored |
| Robot fallen | State FALLEN, stop everything, operator alert |
| Battery < 20 % | Motion tools disabled except `set_posture(Sit/Crouch)` and `stop` |
| Battery < 10 % | Sit, announce, go to rest |
| Joint temperature warning | Disable dances/steps; after critical → Crouch + rest |
| Robot connection lost | Bridge stops sending commands, console alert, auto-reconnect |
| 3 min idle | Crouch to cool down; Stand again on next turn |

The chest button keeps its NAOqi system function and is not repurposed.

### 10.4 Physical rules for the event

- Robot on the floor, marked area ≥ 1.5 × 1.5 m, visitors outside the line.
- Walking toggle **off** by default; operator turns it on only when the area is clear.
- An operator is present at all times while the robot is powered.

---

## 11. Audio and speech

### 11.1 v1 (required): robot microphones, wake word + fallbacks

Audio always comes from NAO's own microphones (front mic, 16 kHz mono).

**Three ways to start listening** (all lead to the same LISTENING state):

| Trigger | How | Notes |
| --- | --- | --- |
| Wake word | On-robot `ALSpeechRecognition` with word spotting on a tiny vocabulary (`"Hallo NAO"`, `"Hey NAO"`) | Runs offline on the robot; audio leaves the robot only after the wake word. Default trigger. |
| Front head tap | `FrontTactilTouched` event | Reliable fallback when the hall is too loud for the wake word |
| Operator key | `space` in the admin CLI while the prompt line is empty, or the `listen` command | Always available |

**Recording after the trigger:**

1. Eyes blue, short "listening" sound.
2. The bridge subscribes to `ALAudioDevice` as a **remote audio client** (front channel, 16 kHz) and receives raw PCM over qi. This code lives in `robot/qi_adapter.py`, because §6.1 keeps transport code inside `naollm.robot`.
3. Voice activity detection (`webrtcvad-wheels`, the fork with Python 3.12 wheels) on the laptop ends the utterance after 0.8 s of silence, with a hard limit of 15 s. If nobody speaks within `audio.no_speech_timeout_s` (default 5 s), the attempt ends, so an accidental trigger does not keep the mic open.
4. The bridge unsubscribes, eyes yellow, audio goes to `faster-whisper`.
5. Ignore utterances < 0.5 s or empty transcripts.

**Rules:**

- The wake-word engine is **paused while NAO speaks or moves** (own voice and motor noise cause false triggers) and resumed 0.5 s after.
- Wake-word confidence threshold configurable (`audio.wake_threshold`, start at 0.5 and tune on site).
- `ALSpeechRecognition` must not be used together with `ALDialog`; Autonomous Life's dialog must stay off.
- `ALSpeechRecognition` runs one language at a time. The wake-word ASR language is fixed to **German** for both phrases ("Hallo NAO", "Hey NAO"). Per-turn language switching (below) changes TTS only and never touches the ASR language.
- Audio buffers are kept in memory only and discarded after transcription.

**Fallback if remote audio subscription fails in phase 0:** record on the robot with `ALAudioRecorder` to `/tmp`, fetch the file, delete it immediately on the robot. Higher latency (~0.5–1 s), same privacy rule (no file survives the turn).

**Languages:**

- Languages: German and English. Whisper's language detection is restricted to `stt.languages` (default `[de, en]`): take the more probable of the allowed languages, fall back to `de` when unsure (short utterances are often misdetected).
- The detected language is passed to the LLM ("Answer in German/English") and the robot's TTS language is switched per turn via `ALTextToSpeech.setLanguage` (both voice packs must be installed; checked at startup).

### 11.2 v2 (optional)

- Continuous streaming + laptop-side wake word (e.g. openWakeWord with a custom "Hey NAO" model) if NAO's built-in word spotting proves unreliable. Privacy note: audio then leaves the robot continuously (still local, still discarded), signage must say so.
- Experiment: send audio directly to Gemma 4 instead of Whisper. Only worth trying with E4B (E2B's audio is reportedly weak), and only if it beats Whisper on our own recorded test clips in both languages.

### 11.3 Speech output

- `ALAnimatedSpeech` for normal answers (adds automatic gestures).
- Check installed NAO language packs at startup; switch the TTS language per turn to the detected language (§11.1).
- Sentences are spoken as they stream to reduce perceived latency.

---

## 12. Privacy and compliance

> Not legal advice. Clear the showcase plan with the school (Direktion) and the club supervisor before the event.

### 12.1 Legal frame (summary)

- At a school event the **GDPR applies** (household exemption does not cover it). Visitors include minors.
- Faces and voices of identifiable people are personal data.
- **Face recognition** (learned faces) is biometric identification → special-category data (GDPR Art. 9) → **explicit consent**; guardians' consent for minors.
- **Emotion recognition** in education is **prohibited** under EU AI Act Art. 5 → not implemented.
- **Biometric categorisation** (age/gender estimation) triggers transparency duties under AI Act Art. 50 (people must be informed) → gender excluded, age game off by default.
- Speech processing is local; nothing leaves the laptop.

### 12.2 Design decisions that follow

| Data | Handling |
| --- | --- |
| Camera | Used only on the robot by NAOqi built-ins (face tracking, people count, face recognition). No frames leave the robot. |
| Face templates | Only for club members who signed the consent form. Stored on the robot by NAOqi. `forget_face` and `forget_all_faces` on request and after the event. |
| Audio | Wake-word spotting runs on the robot and stores nothing. After a trigger, audio is streamed to the laptop, kept in memory only, and discarded after transcription. |
| Transcripts / history | In memory only; reset per visitor / after 60 s idle; never persisted. |
| Logs | Contain events, tool names and results, timings. **No transcripts, no names** unless debug mode is explicitly enabled (never at the event). |
| LLM | Local Ollama; no third-party transfer. If a cloud endpoint is ever configured, the console shows a persistent warning and signage must be updated. |

### 12.3 Required artifacts

1. **Consent form** for face enrolment (member or guardian signature, purpose, deletion date, withdrawal anytime).
2. **Signage** at the booth, e.g.:
   > "Sag 'Hallo NAO' oder tippe ihm vorne auf den Kopf. Danach hört er kurz zu und verarbeitet Sprache nur lokal auf diesem Laptop. Es wird nichts gespeichert. Die Kamera erkennt nur, dass jemand da ist, und dreht den Kopf zu dir. Gesichter werden nur bei Club-Mitgliedern mit Einwilligung erkannt."

   While the robot is English only with motors off, use this instead (also in the README):
   > "Say 'Hey NAO' or tap the front of his head. He then listens for a moment and processes speech only locally on this laptop. Nothing is stored. The camera only notices that someone is there. Faces are recognized only for club members who consented."
3. Short **spoken notice** available via the `notice` command, in the current language. It does not mention the head turning while motors are off.
4. **Deletion checklist** after the event (forget all faces, confirm no files written).

### 12.4 Privacy switches (`privacy` config block)

```yaml
privacy:
  face_recognition_enabled: true     # only enrolled members
  age_game_enabled: false
  persist_transcripts: false         # must be false at the event
  debug_logging: false
```

The bridge refuses to start in `event_mode: true` if `persist_transcripts` or `debug_logging` is true.

---

## 13. Risks and fallbacks

| Risk | Fallback |
| --- | --- |
| Python 3 `qi` cannot talk to NAOqi 2.8 | (a) small Python 2.7 shim process using the official SDK, talking to the Python 3 bridge over a local socket/JSON-RPC; (b) `naoqi_driver2` (ROS 2). The `RobotAdapter` interface stays the same. |
| Small model makes poor tool choices | Fewer tools, better descriptions, eval set (§14.3), or a larger model if RAM allows |
| Latency feels slow | Sentence streaming, `think` gesture + yellow eyes while waiting, shorter `max_tokens` |
| Noisy hall breaks STT or wake word | Front head tap / operator key instead of wake word, speak close to NAO, restrict languages to de/en; last resort: USB close-talk mic on the laptop |
| Vendor downloads disappear | Mirror SDK, Choregraphe, docs and NAOqi image locally now |
| Robot overheats during a long event | Idle crouch, dance rate limit (e.g. max 1 dance / 2 min), breaks |
| Wi-Fi interference | Own router, 5 GHz if robot supports it, laptop on Ethernet |

---

## 14. Testing

### 14.1 Unit tests (no robot needed)

- `FakeRobotAdapter` records calls and simulates posture, battery, fall, motion state.
- Gate tests: every rule in §10.2 has a passing and a failing case.
- Loop tests: stubbed LLM responses (text only, single tool, multiple tools, malformed JSON, timeout).

### 14.2 Dry-run mode

`--dry-run` uses the fake adapter but real STT + LLM; actions are printed instead of executed. Used to tune prompts at home.

### 14.3 Tool-call evaluation

`tests/eval/tool_calls.yaml` with ~40 prompts in German and English. The tool set depends on config (hidden tools are not sent to the model, §7.4), so every case names a **profile**. The runner loads the matching config overlay from `tests/eval/profiles/<profile>.yaml`. Example:

```yaml
- profile: event_default           # walking off, age game off
  prompt: "Kannst du tanzen?"     # a question about an ability: say yes, offer, do not move
  expect: {no_tool: true}
- profile: event_default
  prompt: "Tanz bitte die Macarena!"
  expect: {tool: dance, args: {name: macarena}}
- profile: event_default
  prompt: "Wink mal!"
  expect: {tool: play_gesture, args: {name: wave}}
- profile: event_default
  prompt: "Wie alt bin ich?"
  expect: {no_tool: true}
- profile: walking_on
  prompt: "Lauf zehn Meter nach vorne"   # beyond the limits: any safe answer passes
  expect: {any_of: [{no_tool: true}, {tool: step, args: {direction: forward}}]}
```

Run against each candidate model; target ≥ 90 % correct per profile before the event. The runner loads the model first, waits up to 120 s per request, shows a live progress line with an ETA, and prints each case's result and reply as it finishes; `-v` adds the reasoning, tool calls and timings. The final report adds response time, time to first token, to first answer token and to first tool call, tokens, and counts of reasoning, cut-off, empty and failed replies. An empty reply always fails. Ctrl+C prints the report for the finished cases.

`tests/eval/eval_config.yaml` is a matrix: `base` holds only the eval-relevant config, and `runs` lists `llm` settings (model, `reasoning_effort`, `max_tokens`) to compare. The runner runs each in turn and ends with a comparison table; `--run TEXT` selects runs by label. A plain config is one run.

### 14.4 On-robot tests

Checklist per phase: connect, speak, each gesture, each dance, posture changes, head look, emergency stop via head touch, fall handling (gently tip over on a mat), low battery simulation via config threshold.

---

## 15. Configuration example

```yaml
robot:
  url: tcp://192.168.8.20:9559
  language: German
  battery_reliable: true
  # listen_ip: 192.168.8.10   # this laptop's address, if remote audio does not arrive
llm:
  base_url: http://localhost:11434/v1
  api_key: ollama
  model: gemma4:e4b
  temperature: 0.4
  max_tokens: 200
  timeout_s: 20
  max_tool_rounds: 3
conversation:
  history_turns: 6
  idle_reset_s: 60
stt:
  engine: faster-whisper
  model: small
  languages: [de, en]
  fallback_language: de
audio:
  source: robot              # robot | robot_recorder_fallback
  wake_words: ["Hallo NAO", "Hey NAO"]
  wake_language: German      # fixed ASR language for word spotting (§11.1)
  wake_threshold: 0.5
  front_head_tap: true
  operator_key: space        # only when the admin prompt line is empty
  vad_silence_s: 0.8
  max_utterance_s: 15
  no_speech_timeout_s: 5
motion:
  motors_enabled: true       # false on the current robot (see "Interim" above)
  walking_enabled: false
  max_turn_deg: 90
  max_step_cm: 30
  max_total_displacement_m: 1.0
  battery_motion_min_pct: 20
  battery_dance_min_pct: 30
  idle_crouch_s: 180
dances:
  macarena: showcase/dance_macarena
gestures:
  wave: animations/Stand/Gestures/Hey_1
  bow: showcase/gesture_bow
  victory: anim:victory          # keyframe file animations/victory.anim.yaml (§19)
robot_profile_dir: ~/.naollm/robots   # written by `naollm init`
privacy:
  face_recognition_enabled: true
  age_game_enabled: false
  persist_transcripts: false
  debug_logging: false
event_mode: true
```

---

## 16. Implementation phases

| Phase | Deliverable | Acceptance criteria |
| --- | --- | --- |
| **0 — Connectivity spike** | Standalone script in `spike/` (outside OpenSpec, run on the operator laptop, not the dev container) that connects from Python 3, says a sentence, reads battery, runs one installed Choregraphe behavior, receives 5 s of robot mic audio, triggers on a spotted wake word, and deploys a package with `qipkg` | All seven work on the real robot; otherwise pick fallbacks from §11.1 / §13. Findings recorded in `spike/FINDINGS.md` and fed into phase 1. |
| **1 — Talking** | Keyboard text → LLM → animated speech; config + prompts | 10 consecutive turns without errors; replies ≤ 2 sentences |
| **2 — Tools + gate** | All §8.2 tools, safety gate, event watcher, fake adapter, unit tests, admin CLI basics (`tool`, `estop`, `resume`, Ctrl+C handling) | Gate tests green; head-touch, hotkey and Ctrl+C stop work from any state |
| **3 — Ears** | Wake word + head tap + operator key, robot audio stream, VAD, faster-whisper, sentence streaming | Wake word works at 1 m in a noisy room ≥ 8/10 tries; < 1 false trigger per 10 min of background chatter; median turn latency ≤ 5 s |
| **3b — Deploy + animations** | `naollm init`, `naollm deploy`, keyframe animation format, validator, player, Choregraphe importer | Fresh robot ready from one command; every library animation passes validation and plays on the robot |
| **4 — Perception** | Head tracking default, `who_is_here`, operator enrolment with consent flow | Enrolment/forget work; no frames leave the robot |
| **5 — Event hardening** | Admin CLI complete (§18), event mode checks, signage, consent forms, eval ≥ 90 %, rehearsal | Full 30-minute rehearsal without operator intervention except planned ones |

---

## 17. Open questions

1. Exact NAOqi version on the robot, and which Python version the `qi` wheel supports.
2. ~~Showcase language(s): German only, or German + English?~~ Resolved: German + English (§11.1).
3. Which German voice packs are installed on the robot?
4. ~~Laptop model and RAM: `gemma4:e2b` or `e4b`?~~ Resolved: `gemma4:e4b` on a 15 GiB laptop.
5. Does the school require its own data-protection sign-off for the event?
6. Which club members want face enrolment, and who collects the consent forms?
7. How long is the showcase (affects heat/battery planning and spare charging time)?

---

## 18. Admin CLI

The admin CLI is the operator's control panel. It runs **in the same process as the bridge**, so it shares the robot connection, the state machine, the motion lock and the safety gate. Nothing the admin does can race the LLM.

### 18.1 Modes

| Command | What runs | Use |
| --- | --- | --- |
| `naollm run` | Bridge + LLM loop + admin prompt | The event |
| `naollm admin` | Robot connection + admin prompt, **no LLM, no STT** | Face enrolment, testing tools and Choregraphe behaviors, rehearsals |
| `naollm run --dry-run` | As `run`, fake robot adapter | Prompt tuning without the robot |
| `naollm init` | Robot setup + package deploy + self-test (§20) | First setup of any robot |
| `naollm deploy` | Rebuild and install the Choregraphe package only | After changing behaviors |
| `naollm anim …` | Animation tools (§19.5) | Validating and previewing keyframe animations |

### 18.2 Screen layout

- **Status bar** (always visible, refreshed every second): connection, state (`IDLE`, `ACTING`, `STOPPED`, …), posture, battery %, temperature status, walking ON/OFF, LLM ON/OFF, number of enrolled faces.
- **Event log** (scrolling): tool calls (source `llm` or `admin`), gate rejections, sensor events, errors. No transcripts in event mode.
- **Prompt** at the bottom with tab-completion for commands, tool names and enum values.

### 18.3 Emergency stop

The e-stop is one routine, `estop()`, reachable four ways:

| Trigger | Behaviour |
| --- | --- |
| **Hotkey `Ctrl+E`** | Works at any time, even mid-typing, without pressing Enter (global prompt_toolkit key binding) |
| **`estop` command** | Same routine |
| **Ctrl+C (SIGINT)** | First press: `estop()`, robot ends in STOPPED, CLI keeps running and prints "Press Ctrl+C again within 3 s to quit". Second press within 3 s: safe shutdown (§6.4), then exit. |
| **SIGTERM / closing the terminal / unhandled exception** | `estop()`, then safe shutdown, then exit |
| Middle/rear head tactile touch (robot side, §10.3) | Same routine, independent of the laptop |

`estop()` does, in order, each step wrapped so one failure cannot skip the rest:

1. Set state `STOPPED` (gate refuses all motion from here on).
2. Cancel the running LLM turn (abort the HTTP stream, drop pending tool calls).
3. `stop_all_behaviors()` and `stop_move()`.
4. `go_to_posture("Crouch")`.
5. Eyes red; log `ESTOP (source=<hotkey|command|sigint|sigterm|touch>)`.

Leaving STOPPED requires the explicit `resume` command (asks for `y` confirmation). Ctrl+C never exits without the stop running first.

Limitation (documented in the README): if the laptop crashes hard or loses power, the bridge cannot stop the robot; the head-touch stop on the robot and NAOqi's own fall manager remain the last line of defence. The operator stays within reach of the robot.

### 18.4 Commands

| Command | Description |
| --- | --- |
| `help [command]` | List commands or show details |
| `status` | Full status dump (battery, joints temperature level, posture, installed behaviors count, model, config path) |
| `estop` / `resume` | Emergency stop / leave STOPPED after confirmation |
| `tools` | List LLM tools with parameters and whether they are currently enabled |
| `tool <name> [args…]` | Trigger a tool manually, e.g. `tool dance macarena`, `tool look left`, `tool step forward 20`, `tool turn -45`. Arguments are positional in schema order, or `key=value`. |
| `say "<text>"` | Make NAO speak the text (animated) |
| `behaviors` | List installed behaviors on the robot and which config entries they map to |
| `posture <Stand\|Sit\|Crouch>` | Shortcut for `tool set_posture` |
| `wake` / `rest` | Stiffness on and Stand / Crouch and stiffness off |
| `walk on\|off` | Walking toggle (enables/disables `turn` and `step`, also for the LLM) |
| `llm on\|off` | Pause/resume the conversation loop (robot stays connected) |
| `reset` | New visitor: clear conversation history |
| `space` (hotkey, empty prompt line only) / `listen` | Start listening, same as wake word. With text on the prompt line, `space` types a space. |
| `ears on\|off` | Enable/disable wake-word triggering (head tap and `space` keep working) |
| `ears test` | Show live wake-word confidences for 30 s to tune the threshold on site |
| `anim list` / `anim play <name>` | List library animations / play one through the gate |
| `faces list` | Enrolled names with consent reference and deletion date |
| `faces enroll <name>` | Guided enrolment (§18.6) |
| `faces delete <name>` | Forget one face on the robot and remove its consent record |
| `faces delete --all` | Forget all faces; requires typing `DELETE` to confirm |
| `notice` | Robot speaks the privacy notice (§12.3) |
| `reconnect` | Reconnect to the robot after a network drop |
| `model load` / `model unload` / `model status` | Load the LLM into Ollama and keep it loaded until exit, release it, or show whether it is loaded. `naollm run` loads it at start and releases it at exit; `unload` is refused during a turn. |
| `quit` | Safe shutdown (§6.4) and exit, releasing the model if this bridge loaded it |

### 18.5 Manual tools go through the gate

- `tool …` calls the **same handler and the same safety gate** as the LLM. Admin calls are logged with `source=admin`.
- Admin calls are subject to every gate rule except one: they may run while `llm off`. There is no `--force` flag; to allow walking, the operator uses `walk on` explicitly.
- If the LLM is mid-motion, an admin motion command is rejected with the gate's "already moving" reason; `estop` always wins.

### 18.6 Face enrolment flow

1. `faces enroll Lukas` → CLI asks: "Consent form signed? Enter form ID (or blank to abort):". No ID → abort.
2. CLI asks for the deletion date (default: day after the event).
3. Robot says "Bitte schau mich an", eyes green, head tracking on.
4. Bridge calls `learn_face(name)`; retries up to 3 times with a spoken hint.
5. On success: CLI writes the consent record, then the robot says the name once. On failure: nothing is stored.

With motors off, step 3 differs: head tracking stays off, and the console tells the operator to position the robot's head by hand before learning. The spoken lines use the current language. The name, which may contain 1 to 32 letters, digits, spaces or hyphens, must not already have a record. An emergency stop aborts enrolment and stores nothing.

Names may be nicknames; the CLI does not ask for anything else.

**Consent registry** (`~/.naollm/consent.json`, local only): `name`, `consent_form_id`, `enrolled_at`, `delete_after`. This file exists so deletions can be proven and is not personal data beyond what is needed. On startup, the CLI warns about records past `delete_after` and offers to delete them. `faces delete` removes both the robot's face template and the record. Mismatches between the robot's learned-faces list and the registry are shown in `faces list` and must be resolved by deleting.

### 18.7 Safety of the CLI itself

- The CLI binds no network ports; it is only reachable from the laptop's terminal.
- Event mode (`event_mode: true`) hides `say` text from the log and refuses `debug_logging`.
- Every command that moves the robot, deletes faces or resumes from STOPPED is written to the event log with a timestamp.

### 18.8 Tests

- Unit: command parser (positional and `key=value` args, unknown tools, bad enums), consent registry, `estop()` step order with injected failures.
- Integration (fake adapter): Ctrl+C once → STOPPED and still running; twice within 3 s → shutdown sequence called; SIGTERM → shutdown sequence called; hotkey during an LLM turn → turn cancelled, no further tool calls executed.
- On robot: e-stop from each trigger during a dance, during a step, and while idle.

---

## 19. Animations as data (outside Choregraphe)

Animations do not have to be Choregraphe behaviors. A keyframe animation is a list of joints, times and angles, which NAOqi plays directly at runtime via `ALMotion.angleInterpolationBezier` (or `angleInterpolation`). Nothing is installed on the robot; the bridge sends the keyframes when the animation is played. This makes animations easy to version in git, diff, review, and generate with tools or AI agents.

### 19.1 File format (`animations/<name>.anim.yaml`)

```yaml
name: victory
description: Both arms up, short fist pump.
version: 1
author: human            # or agent:<agent-name>
reviewed_by: ""          # must be set before use at events
posture: Stand           # required start and end posture
duration_s: 3.2
uses_legs: false         # if true: extra review + balance rules (19.3)
interpolation: bezier    # bezier | linear
tracks:
  LShoulderPitch: {times: [0.6, 1.4, 2.4, 3.2], angles_deg: [-60, -80, -60, 85]}
  RShoulderPitch: {times: [0.6, 1.4, 2.4, 3.2], angles_deg: [-60, -80, -60, 85]}
  LElbowRoll:     {times: [0.6, 1.4, 3.2],      angles_deg: [-30, -60, -25]}
  RElbowRoll:     {times: [0.6, 1.4, 3.2],      angles_deg: [30, 60, 25]}
  HeadPitch:      {times: [1.4, 3.2],           angles_deg: [-15, 0]}
```

Angles are stored in degrees for readability; the player converts to radians. Example values are illustrative, not validated.

### 19.2 Sources of animations

| Source | Path into the library |
| --- | --- |
| Hand-written | Write the YAML directly |
| Choregraphe timeline | Export the timeline to Python in Choregraphe → `naollm anim import <file.py>` converts it to `.anim.yaml` |
| AI agent (later) | Agent writes `.anim.yaml` on a branch → validator → preview → human review → merge |
| Posing the robot (later) | Arm stiffness off, operator poses the arms, CLI samples joint angles as keyframes |

### 19.3 Validator (`naollm anim validate`)

Every animation must pass before it can be played:

1. Only known NAO joint names; no duplicate tracks.
2. Every angle within the joint limits from `ALMotion.getLimits` (stored in the robot profile by `naollm init`), with a 5° margin.
3. Joint speed below a configured fraction of the limit (default 60 %).
4. Times strictly increasing; total duration ≤ 30 s.
5. First and last keyframes within 10° of the declared start/end posture for every joint touched, so it chains safely with anything else.
6. `uses_legs: false` → no leg or hip joints. These play with the whole-body balancer on and both feet fixed, so arm movements cannot topple the robot.
7. `uses_legs: true` → only allowed if `reviewed_by` is set and it was previewed on the real robot with an operator's hands near it.
8. At events (`event_mode: true`), animations without `reviewed_by` are dropped from the tool enums at startup with a warning.

### 19.4 Player

- Goes through the safety gate like every other motion (motion lock, posture precondition, battery, temperature).
- Upper-body animations: enable whole-body balancer, fix both feet, play, return to start posture, disable balancer.
- `estop()` interrupts playback, then runs the normal stop routine (exact interruption call verified in phase 2).

### 19.5 CLI

| Command | Description |
| --- | --- |
| `naollm anim validate [name\|--all]` | Run §19.3 checks; print violations with joint and time |
| `naollm anim import <export.py> --name <name>` | Convert a Choregraphe Python timeline export |
| `naollm anim preview <name> [--speed 0.5]` | Play on the robot in slow motion; operator confirmation required |
| `naollm anim list` | All animations with review status |

### 19.6 Rule for AI-generated animations

The conversation LLM never creates or edits animations at runtime; it only selects reviewed animations by name. Generating animations is an offline workflow with validation and human review, never a live tool.

---

## 20. Robot setup: `naollm init` and `naollm deploy`

Goal: a fresh or reset NAO is showcase-ready with one command.

### 20.1 `naollm init --robot <ip-or-hostname>`

Idempotent and safe to re-run. Each step prints ✓ / ✗ with a fix hint.

While motors are off (see "Interim" above), `naollm init` runs only the connect, languages, robot settings, robot profile, animations, self-test and summary steps. It sends no motor command. It leaves out SSH access, the package deploy, the Stand and gesture checks, and `--name`. The robot settings step only disables Autonomous Life, after a confirmation. The self-test covers speech, eyes, each head sensor, microphone level, a wake word attempt and the battery reading, and marks a skipped check as skipped.

1. **Connect** over qi; read NAOqi version and robot name. Abort if not NAOqi 2.8.x.
2. **SSH access** for package deployment: prompt once for the robot's `nao` password and install the laptop's SSH public key, so later deploys are password-free. The password is never written to disk or config.
3. **Languages**: check German and English for TTS and speech recognition. Missing packs cannot be installed by the CLI; print instructions.
4. **Robot settings**: Autonomous Life to quiet mode, built-in dialog off, default volume, robot name (`--name`).
5. **Package deploy**: `qipkg make-package choregraphe/showcase/showcase.pml`, then `qipkg deploy-package <pkg> --url nao@<robot>` (installs through the robot's PackageManager). Compare installed behaviors with the config.
6. **Robot profile**: read joint limits, robot id, NAOqi version, languages; write `~/.naollm/robots/<robot-id>.yaml` (used by the animation validator and for multiple robots).
7. **Animations**: `anim validate --all` against this robot's limits.
8. **Self-test** (interactive, operator present, robot on the floor):
   - test sentence in German and English,
   - Stand → Crouch, one gesture,
   - "Touch my front head" → listening triggers; middle/rear → e-stop fires,
   - mic level check and wake-word test,
   - battery and temperature readout.
9. **Summary** of passed/failed checks; non-zero exit code if a required check failed.

Options: `--skip-selftest`, `--only <step>`, `--dry-run`.

### 20.2 `naollm deploy [--robot <ip>]`

Steps 5–7 only: bump the version in `manifest.xml`, build, install, verify. Run after every Choregraphe change.

### 20.3 Not automatable (one-time manual checklist in the README)

- Installing NAOqi system images or language packs (vendor tools / robot web page).
- First network setup on a brand-new robot (Ethernet + robot web page for Wi-Fi).
