# naollm

A local bridge that lets a NAO 6 robot hold spoken conversations through a local LLM (any OpenAI-compatible endpoint, default Ollama with `gemma4:e2b`) and move through tool calls that a safety gate checks. It was built for a robotics club showcase at school. The full design is in [`SPEC.md`](SPEC.md).

**Current state:** everything that does not need the robot runs against a fake robot. This covers the configuration, the safety gate, the tools, the text conversation loop, the operator console and keyframe animations. The real robot connection, audio input, `naollm init` and `naollm deploy` are built after the phase 0 connectivity spike in [`spike/`](spike/).

## Setup

You need Python 3.10 or newer.

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e '.[dev]'
cp config.example.yaml config.yaml   # then set robot.url, dances, gestures
```

To use local Ollama:

```bash
ollama pull gemma4:e2b
```

## Try it without the robot

```bash
naollm admin --dry-run --config config.yaml   # operator console, no LLM
naollm run --dry-run --config config.yaml     # console + conversation loop (needs the LLM endpoint)
```

In dry-run mode, every robot action is printed as a `[robot] ...` line instead of being executed. While the audio input does not exist yet, type the visitor's words with `chat`:

```
naollm> chat Kannst du winken?
naollm> chat --en Can you dance?
```

If `event_mode: true` is set (as in the example config), the log does not show spoken text or visitor text. For prompt tuning at home, set `event_mode: false`.

## Operator console

Type `help` in the console for the full list.

| Command | What it does |
| --- | --- |
| `status`, `tools`, `behaviors` | Show robot state, the LLM tools and installed behaviors |
| `tool <name> [args…]` | Run a tool through the same safety gate the LLM uses, e.g. `tool dance macarena`, `tool step forward 20`, `tool look direction=left` |
| `say "<text>"` | Make NAO speak |
| `posture <Stand\|Sit\|Crouch>`, `wake`, `rest` | Postures and stiffness |
| `walk on\|off` | Enable `turn` and `step` (for the LLM too) |
| `llm on\|off`, `reset`, `chat` | Pause the conversation, start a new visitor, send typed text |
| `estop`, `resume` | Emergency stop, and leaving it again (asks for `y`) |
| `quit` | Safe shutdown: stop, Crouch, rest, disconnect |

### Hotkeys

- **`Ctrl+E`** runs the emergency stop at any time, even while you are typing.
- **`space` on an empty line** starts listening. This needs the audio input; until then it only logs a message. With text on the line, `space` types a space.
- **`Ctrl+C`**:
  - The first press runs the emergency stop and keeps the console open.
  - A second press within 3 s shuts down safely and exits.
  - `SIGTERM` and closing the terminal also run the emergency stop first, then shut down.

When the input is piped, as in `naollm admin --dry-run < script.txt`, the console runs the commands one after another. Use this for scripted checks.

### Emergency stop limitation

If the laptop crashes hard or loses power, the bridge cannot stop the robot. The last line of defence is then:

- the head-touch stop on the robot (middle or rear head sensor), and
- NAOqi's own fall manager.

The operator always stays within reach of the robot (SPEC §18.3).

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
naollm eval --config config.yaml -v
```

This runs `tests/eval/tool_calls.yaml`: 40 German and English prompts. Each case names a profile, `event_default` or `walking_on`, whose overlay from `tests/eval/profiles/` is merged into your config. The output shows the accuracy per profile. The target before the event is at least 90 % per profile.

## Tests

```bash
pytest
```

The tests use the fake robot and a stub LLM endpoint. No robot, Ollama or network is needed.
