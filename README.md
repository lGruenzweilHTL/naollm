# naollm

A local bridge that lets a NAO 6 robot hold spoken conversations through a local LLM (any OpenAI-compatible endpoint, default Ollama with `gemma4:e4b`) and move through tool calls that a safety gate checks. It was built for a robotics club showcase at school. The full design is in [`SPEC.md`](SPEC.md).

**Current state:** the bridge talks to the real robot in English, with motors off. Behaviors, gestures, dances, postures, head tracking and `naollm deploy` wait for a new battery and run only on the fake robot, or not at all. See [Real robot](#real-robot).

> **No motors.** This robot's battery is defective, so never command its motors. The real adapter contains no code that can, and a real run refuses to start unless `motion.motors_enabled` is `false`. Keep the robot in a safe seated or lying position: the bridge refuses to start if the robot is awake, but it cannot prevent a wake-up between runs.

## Setup

You need Python 3.10 or newer.

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e '.[dev]'
cp config.example.yaml config.yaml   # then set robot.url
```

The extras are optional. The core package and its tests work without them:

```bash
pip install -e '.[dev,robot,voice]'   # robot: qi; voice: faster-whisper and webrtcvad-wheels
```

To use local Ollama:

```bash
ollama pull gemma4:e4b
```

## Try it without the robot

```bash
naollm admin --dry-run --config config.yaml   # operator console, no LLM
naollm run --dry-run --config config.yaml     # console + conversation loop (needs the LLM endpoint)
```

In dry-run mode, every robot action is printed as a `[robot] ...` line instead of being executed. Type the visitor's words with `chat`, or run a recording through the whole voice flow with `listen --wav`. The file must be 16 kHz mono 16-bit, and the speech model loads at the first `listen`:

```
naollm> chat Can you dance?
naollm> chat --de Kannst du winken?
naollm> listen --wav clip.wav
```

If `event_mode: true` is set (as in the example config), the log does not show spoken text or visitor text. For prompt tuning at home, set `event_mode: false`.

## Real robot

The target is a NAO 6 on NAOqi 2.8 with Python 3.12 and `qi` 3.1.5 on the laptop.

1. Set `robot.url` in `config.yaml`. Set `robot.listen_ip` to this laptop's address if remote audio does not arrive.
2. Run `naollm init --robot <ip-or-hostname>`. It checks the languages, disables Autonomous Life after you confirm, writes the robot profile to `~/.naollm/robots/`, validates animations without playing them, and runs an interactive self-test. It sends no motor command. Use `--skip-selftest`, `--only <step>` or `--dry-run` as needed.
3. Run `naollm admin` for the console with no LLM and no listening, or `naollm run` for the conversation.

To talk to the robot, say the wake phrase, tap the front of its head, or press `space` on an empty console line. The eyes turn blue while it listens and yellow while it thinks. Audio leaves the robot only after a trigger, stays in memory and is discarded after transcription.

A middle or rear head touch runs the emergency stop on purpose. Type `resume` to continue.

The wake phrase is weak on this robot: it scored 0.43 and 0.44 in the spike. Run `ears test` to see the confidences and tune `audio.wake_threshold` on site. The head tap and the key do not depend on it.

`naollm run` loads `llm.model` into Ollama before it says ready, and keeps it loaded with `keep_alive: -1`, so the first turn after a quiet spell is not slow. It unloads the model when the bridge exits. If the laptop process is killed hard, the model stays in RAM until `model unload`, `ollama stop <model>` or an Ollama restart. `naollm admin` loads nothing at start. If the endpoint is not Ollama, the bridge shows an alert and starts anyway.

If `faster-whisper` or `webrtcvad-wheels` is missing, or the speech model cannot load, the bridge still starts with typed `chat` and shows an alert.

## Operator console

Type `help` in the console for the full list.

| Command | What it does |
| --- | --- |
| `status`, `tools`, `behaviors` | Show robot state, the LLM tools and installed behaviors |
| `tool <name> [args…]` | Run a tool through the same safety gate the LLM uses, e.g. `tool dance macarena`, `tool step forward 20`, `tool look direction=left` |
| `say "<text>"` | Make NAO speak |
| `posture <Stand\|Sit\|Crouch>`, `wake`, `rest` | Postures and stiffness (refused with motors off) |
| `walk on\|off` | Enable `turn` and `step` for the LLM too (`on` is refused with motors off) |
| `listen [--wav <file>]` | Start listening; `--wav` only in a dry run |
| `ears on\|off`, `ears test` | Wake word on or off, or show every recognized phrase and confidence for 30 s |
| `reconnect` | Connect again after a network drop; then type `resume` |
| `model load\|unload\|status` | Keep the LLM loaded in Ollama until exit, release it now, or show whether it is loaded (`unload` is refused during a turn) |
| `faces list`, `faces enroll <name>`, `faces delete <name>`, `faces delete --all` | Enrolled faces and their consent records, see [Faces and privacy](#faces-and-privacy) |
| `notice` | The robot speaks the privacy notice (not during a turn) |
| `llm on\|off`, `reset`, `chat` | Pause the conversation, start a new visitor, send typed text |
| `estop`, `resume` | Emergency stop, and leaving it again (asks for `y`) |
| `quit` | Safe shutdown: stop, Crouch, rest, disconnect. With motors off: unsubscribe and disconnect only |

### Hotkeys

- **`Ctrl+E`** runs the emergency stop at any time, even while you are typing.
- **`space` on an empty line** starts listening. With text on the line, `space` types a space.
- **`Ctrl+C`**:
  - The first press runs the emergency stop and keeps the console open.
  - A second press within 3 s shuts down safely and exits.
  - `SIGTERM` and closing the terminal also run the emergency stop first, then shut down.

When the input is piped, as in `cat script.txt | naollm admin --dry-run`, the console runs the commands one after another. Use this for scripted checks.

### Emergency stop limitation

If the laptop crashes hard or loses power, the bridge cannot stop the robot. The last line of defence is then:

- the head-touch stop on the robot (middle or rear head sensor), and
- NAOqi's own fall manager.

The operator always stays within reach of the robot (SPEC §18.3).

## Faces and privacy

Face recognition uses biometric data, and visitors include minors. Before anyone is enrolled, the school and the club supervisor must clear this, and a signed consent form must exist for that person (SPEC §12). This software enforces the workflow, but it does not establish a lawful basis. `config.example.yaml` therefore sets `privacy.face_recognition_enabled: false`. Leave it false until the clearance exists.

`faces enroll <name>` asks for the consent form ID first, and a blank answer aborts. It then asks for the deletion date, tells you to position the robot's head by hand, and tries to learn the face up to 3 times. If anything fails, or the emergency stop fires, nothing is stored. Records go to `~/.naollm/consent.json`, which is readable only by you. At startup the console lists records past their deletion date and offers to delete them. `faces list` shows faces with no record, and records with no face; delete to resolve them. A damaged registry blocks enrolment and is never overwritten. To remove the feature, run `faces delete --all`, then delete the file.

In event mode, the log contains no names and no form IDs.

### Signage text

> Say 'Hey NAO' or tap the front of his head. He then listens for a moment and processes speech only locally on this laptop. Nothing is stored. The camera only notices that someone is there. Faces are recognized only for club members who consented.

Change the wake phrase here if you change `audio.wake_words`, and add the head turning only when motors are back.

### Deletion checklist, after the event

1. Run `faces list`, and check for overdue records and mismatches.
2. Run `faces delete --all`, and type `DELETE`.
3. Run `faces list` again. It must show that no faces are enrolled.
4. Check that `~/.naollm/consent.json` holds an empty list. Keep or delete it as the school requires.
5. Check that the laptop holds no audio files and no transcripts. The bridge writes neither.
6. File the signed consent forms, or destroy them, as the school requires.

## Keyframe animations

Simple gestures are stored as data files in `animations/<name>.anim.yaml` (SPEC §19). Reference one from the config with `gestures: {victory: anim:victory}`.

```bash
naollm anim list                                      # review status of every animation
naollm anim validate --all                            # joint limits, speed, posture, legs rules
naollm anim import export.py --name nodding           # convert a Choregraphe "export to Python" timeline
```

- **Joint limits:** the validator uses built-in NAO 6 reference limits until a robot profile from `naollm init` exists, and always says which source it used.
- **Event mode:** in event mode, animations without `reviewed_by` are not offered to the LLM. Set `reviewed_by` only after previewing the animation on the real robot.
- **Choregraphe exports:** these files are parsed, never executed.

## Tool-call evaluation

```bash
naollm eval --config config.yaml        # progress line and one result line per case
naollm eval --config config.yaml -v     # also the model's reasoning, reply, tool calls and timings per case
naollm eval --config tests/eval/eval_config.yaml                      # every model and reasoning setting in turn
naollm eval --config tests/eval/eval_config.yaml --run "reasoning off" # only the runs whose label contains the text
```

This runs `tests/eval/tool_calls.yaml`: 41 German and English prompts. Each case names a profile, `event_default` or `walking_on`, whose overlay from `tests/eval/profiles/` is merged into your config. The eval uses the fake robot, and the profiles switch the motors on, so it tests tool choice whatever your config says.

Before the first case, the eval loads the model into Ollama and unloads it at the end, unless it was already loaded. Each request may take 120 s, because this laptop runs the model on its CPU; change it with `--timeout`, and skip the load with `--no-warmup`. While a case waits for the model, the terminal shows a spinner, the case number, the phase (waiting, thinking, answering), the time spent and an ETA. Each finished case prints PASS or FAIL, the tool calls, and the model's reply. With `-v`, the reasoning and the reply stream as they arrive, followed by the tool calls with arguments and the case's timings.

The final report shows the accuracy per profile, then the timings: mean, median, p90 and max of the response time, the first token, the first answer token (the first text or tool call, so not reasoning), the first text and the first tool call. It also shows the tokens per case, the generation rate, and how many cases reasoned, were cut off at `max_tokens`, came back empty or failed. The target before the event is at least 90 % per profile. Ctrl+C stops the run and prints the same report for the cases that finished.

A question about an ability, such as "Can you dance?", expects no tool: the robot should say yes and offer it. A request beyond the limits, such as walking ten metres, passes on any safe answer: no tool, or a call in the right direction. These cases use `any_of`, a list of expectations of which one must be met. A reply with no text and no tool always fails, because the robot would say nothing.

`llm.reasoning_effort` is `none` by default, which turns thinking off. On this CPU, thinking adds 10 to 25 s per turn and can use up all of `max_tokens`, so the reply comes back empty. `gemma4` knows only on or off, so `low`, `medium` and `high` all switch it on. Set it to `null` for an endpoint that does not accept the field.

`tests/eval/eval_config.yaml` is a matrix config. Its `base` holds only the keys that change what the model sees or what the gate accepts: the LLM settings, the school name, the motion limits, and the dance and gesture names. Each entry in `runs` is merged onto `base.llm`, so one call compares models, and thinking on and off for the models that support it (`ollama show <model>` lists "thinking"). Thinking runs use `max_tokens: 1024` and take 15 to 40 minutes each on this CPU. Every run loads its model, runs all cases, prints its report and unloads the model. At the end, a table shows one row per run: accuracy per profile, the target, the median time to the first answer token, to the first text and for the whole response, the generation rate, the counts of reasoning, cut-off, empty and failed cases, and the wall time. Ctrl+C stops the current run, prints its partial report and the table for the runs so far, and starts no further run.

## Tests

```bash
pytest
```

The tests use the fake robot, a strict fake of the `qi` module and a stub LLM endpoint. No robot or Ollama is needed. `tests/qi_double.py` fails on any NAOqi call outside its allowlist, which is what proves the motors-off guarantee.
