# naollm

A local bridge that lets a NAO 6 robot hold spoken conversations through a local LLM (any OpenAI-compatible endpoint, default Ollama with `gemma4:e4b`) and move through tool calls that a safety gate checks. It was built for a robotics club showcase at school. The full design is in [`SPEC.md`](SPEC.md).

**Current state:** the bridge talks to the real robot in German and English, with motors on. The test robot has a working battery and passed the on-robot motion checks on 2026-11-16 (see `spike/FINDINGS.md`): postures, emergency stop, a deployed behavior, keyframe dances and `naollm init` with motors on. German needs a language pack on the robot; see [Languages](#languages). See [Real robot](#real-robot) and [Motors on](#motors-on).

> **Motors are on.** `config.example.yaml` sets `motion.motors_enabled: true` and `robot.battery_reliable: true`, so startup wakes the robot and stands it up. Before every run, put the robot on the floor in a marked area with an operator in reach; see [Motors on](#motors-on). For a robot whose motors must not be used, for example one with a defective battery, set `motion.motors_enabled: false`.

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
2. Run `naollm init --robot <ip-or-hostname>`. It checks the languages, disables Autonomous Life after you confirm, writes the robot profile to `~/.naollm/robots/`, validates animations without playing them, and runs an interactive self-test. With motors on, it also sets up SSH and deploys the Choregraphe package; see [Motors on](#motors-on). With motors off, it sends no motor command. Use `--skip-selftest`, `--only <step>` or `--dry-run` as needed.
3. Run `naollm admin` for the console with no LLM and no listening, or `naollm run` for the conversation.

To talk to the robot, say the wake phrase, tap the front of its head, or press `space` on an empty console line. The eyes turn blue while it listens and yellow while it thinks. Audio leaves the robot only after a trigger, stays in memory and is discarded after transcription.

A middle or rear head touch runs the emergency stop on purpose. Type `resume` to continue.

The wake phrase is weak on this robot: the English `Hey NAO` scored 0.43 and 0.44 in the spike. Run `ears test` to see the confidences and tune `audio.wake_threshold` on site. The head tap and the key do not depend on it.

### Motors on

Motors are on in `config.example.yaml`. `robot.battery_reliable` says whether the battery reading can be trusted. Set it to `false` for a battery that reads 100 % whatever its charge: a real run with motors on then refuses to start. For a new battery, compare the reading with the charger LED over one charge cycle, with the robot seated at rest, before you set it to `true`.

- **Robot on the floor**, on a mat, in a marked area of at least 1.5 × 1.5 m, with visitors outside the line. An operator stays within reach whenever the robot is powered (SPEC §10.4).
- With `motion.motors_enabled: true`, a real run refuses to start when `robot.battery_reliable` is `false`, when the battery is below `motion.battery_motion_min_pct`, or when Autonomous Life is not `disabled`. Startup wakes the robot, stands it up and starts head tracking. If the wake-up or Stand fails, it sets the robot to rest and exits.
- NAOqi's fall manager, collision protection and smart stiffness stay on. Speeds are fixed in the adapter, and every motion goes through the same safety gate as the LLM's tools.
- `naollm init --robot <ip>` with motors on also installs this laptop's SSH key for the `nao` user (the system `ssh-copy-id` asks for the password; the bridge never sees it), deploys the Choregraphe package, and its self-test stands the robot up, plays the first configured gesture and crouches, after you confirm a clear area. `--name <name>` sets the robot name.
- `naollm deploy [--robot <ip>]` bumps the patch version in `choregraphe/showcase/manifest.xml`, builds the package with `qipkg make-package`, copies it to the robot with `scp`, installs it through the robot's `PackageManager` on the configured NAOqi port, and checks that every `showcase/...` behavior in the config is installed. It refuses with motors off, refuses a behavior with an auto-start trigger, and undoes the version bump when the build or the install fails.
- `naollm anim preview <name> [--speed 0.5]` plays one animation slowly through the gate, after you confirm that your hands are near the robot. Set `reviewed_by` in the file only after a preview.
- With `motion.motors_enabled: false`, the real adapter uses a motion class that contains no code that can command a motor. Startup refuses if the robot is awake or Autonomous Life is not `disabled`, and a poll every 5 s raises an alert on a later wake-up, but the bridge cannot prevent a wake-up between runs, so keep the robot seated or lying at rest. The LLM is neither offered motion tools nor told it can move. `wake`, `rest`, `posture`, `walk on`, `anim play`, `naollm deploy` and `naollm anim preview` refuse, and `naollm init` skips the SSH, deploy and motor steps.

### Languages

`config.example.yaml` is German and English: each turn's language comes from speech to text, and the robot answers in it. The wake phrases are `Hallo NAO` and `Hey NAO`, spotted by the German ASR, because word spotting runs in one fixed language. This needs a German language pack on the robot for both `ALTextToSpeech` and `ALSpeechRecognition`. The pack is installed by hand (SPEC §20.3), and `naollm init` checks both languages. On a robot without German, switch to English only as the comments at the top of the example say, then run `ears test` and `naollm init` again. `naollm eval` reports the score per profile and per language, so a weak language is not hidden in an average.

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

> Sag 'Hallo NAO' oder tippe ihm vorne auf den Kopf. Danach hört er kurz zu und verarbeitet Sprache nur lokal auf diesem Laptop. Es wird nichts gespeichert. Die Kamera erkennt nur, dass jemand da ist, und dreht den Kopf zu dir. Gesichter werden nur bei Club-Mitgliedern mit Einwilligung erkannt.

> Say 'Hey NAO' or tap the front of his head. He then listens for a moment and processes speech only locally on this laptop. Nothing is stored. The camera only notices that someone is there, and he turns his head to you. Faces are recognized only for club members who consented.

With `privacy.age_game_enabled: true`, add:

> KI-Hinweis: Wenn du NAO darum bittest, schätzt er mit einer KI ungefähr dein Alter. Die Schätzung wird nicht gespeichert. Geschlecht und Gefühle werden nie erkannt.

> AI notice: If you ask NAO, he uses AI to roughly guess your age. The guess is not stored. Gender and emotions are never detected.

Change the wake phrases here if you change `audio.wake_words`. With motors off, leave out the head turning. [`docs/signage.md`](docs/signage.md) has every variant.

### Age game

`privacy.age_game_enabled: true` offers the LLM a `guess_age` tool (SPEC §8.6). It is off by default, and it is biometric categorisation of visitors who include minors, so the school and the club supervisor must clear it first, and the signage must announce it. Before that, run the probe in `spike/spike.py --age 20` with one consenting club member: if one analysis writes expression or smile values on the robot, the game stays off until the school's data-protection contact clears it in writing (EU AI Act Art. 5(1)(f)).

- The tool runs only for exactly one visible person, and only with a confidence of at least 0.5. The bridge speaks a 5-year range itself, such as "10 to 15", and the LLM gets "told the visitor a rough age range". The number never reaches the LLM, the history or a log.
- The adapter reads only the age and its confidence, never gender, smile or expression.
- With the game on, walking cannot be switched on, so at most 10 tools are sent. The console warns at startup, the status line shows `AGE GAME ON`, and `notice` mentions the game.
- If `ALFaceCharacteristics` is missing, the bridge starts without the tool and raises an alert.

### Deletion checklist, after the event

Run `naollm cleanup --config config.yaml` and type `DELETE`. It forgets every learned face on the robot, then removes every consent record, checks `~/.naollm` and the temp folder for audio or transcript files (and asks before deleting any), and appends one line with the date and the counts, never a name, to `~/.naollm/deletions.log`. If the robot cannot be reached, the records stay, because they are the list of faces still to forget. Then do the manual items it prints: file or destroy the signed consent forms as the school requires.

## Event day

The full plan is in [`docs/event-runbook.md`](docs/event-runbook.md), with [`docs/signage.md`](docs/signage.md) and [`docs/consent-form.md`](docs/consent-form.md) as drafts for the school to check.

```bash
naollm eval --config config.yaml          # the day before; writes ~/.naollm/eval/<model>.json
naollm preflight --config config.yaml     # read-only: ready or not, with a fix hint per failed check
naollm run --config config.yaml           # then `ears test` and `notice`
naollm cleanup --config config.yaml       # after the event
```

`naollm preflight` checks event mode and the privacy switches, that the LLM endpoint is local, that the model loads, that the latest eval result for the model is at most 14 days old, was made with the current case file and scored at least 90 % in every profile the configuration uses, the robot's NAOqi version, languages and behaviors, that configured animations are reviewed, that motors on comes with a reliable battery, and that the consent registry has no overdue record and matches the robot's learned faces. In event mode it shows counts, not names. It sends no motor command and changes nothing; if the model is not loaded, it loads it and releases it again. The age game and face recognition show as warnings, so you can check the signage.

`motion.dance_cooldown_s` (default 120) is the time from the end of one dance to the next, against overheating. The gate refuses an earlier dance from the LLM and the operator alike, with the seconds left. `0` turns it off.

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

This runs `tests/eval/tool_calls.yaml`: 76 German and English prompts, with an English counterpart for every German one. Each case names a profile, `event_default`, `walking_on` or `age_game_on`, whose overlay from `tests/eval/profiles/` is merged into your config. The eval uses the fake robot, and the profiles switch the motors on, so it tests tool choice whatever your config says.

Before the first case, the eval loads the model into Ollama and unloads it at the end, unless it was already loaded. Each request may take 120 s, because this laptop runs the model on its CPU; change it with `--timeout`, and skip the load with `--no-warmup`. While a case waits for the model, the terminal shows a spinner, the case number, the phase (waiting, thinking, answering), the time spent and an ETA. Each finished case prints PASS or FAIL, the tool calls, and the model's reply. With `-v`, the reasoning and the reply stream as they arrive, followed by the tool calls with arguments and the case's timings.

Each finished run writes `~/.naollm/eval/<model>.json` (`--results-dir` to change it) with the date, the settings, the SHA-256 of the case file and the score per profile and language, never a reply; `naollm preflight` reads it. A run stopped with Ctrl+C writes no file. `--min-score 0.95` replaces the 90 % target, and the report names the profiles below it.

The final report shows the accuracy per profile and per language, then the timings: mean, median, p90 and max of the response time, the first token, the first answer token (the first text or tool call, so not reasoning), the first text and the first tool call. It also shows the tokens per case, the generation rate, and how many cases reasoned, were cut off at `max_tokens`, came back empty or failed. The target before the event is at least 90 % per profile. Ctrl+C stops the run and prints the same report for the cases that finished.

A question about an ability, such as "Can you dance?", expects no tool: the robot should say yes and offer it. A request beyond the limits, such as walking ten metres, passes on any safe answer: no tool, or a call in the right direction. These cases use `any_of`, a list of expectations of which one must be met. A reply with no text and no tool always fails, because the robot would say nothing.

`llm.reasoning_effort` is `none` by default, which turns thinking off. On this CPU, thinking adds 10 to 25 s per turn and can use up all of `max_tokens`, so the reply comes back empty. `gemma4` knows only on or off, so `low`, `medium` and `high` all switch it on. Set it to `null` for an endpoint that does not accept the field.

`tests/eval/eval_config.yaml` is a matrix config. Its `base` holds only the keys that change what the model sees or what the gate accepts: the LLM settings, the school name, the motion limits, and the dance and gesture names. Each entry in `runs` is merged onto `base.llm`, so one call compares models, and thinking on and off for the models that support it (`ollama show <model>` lists "thinking"). Thinking runs use `max_tokens: 1024` and take 15 to 40 minutes each on this CPU. Every run loads its model, runs all cases, prints its report and unloads the model. At the end, a table shows one row per run: accuracy per profile, the target, the median time to the first answer token, to the first text and for the whole response, the generation rate, the counts of reasoning, cut-off, empty and failed cases, and the wall time. Ctrl+C stops the current run, prints its partial report and the table for the runs so far, and starts no further run.

## Tests

```bash
pytest
```

The tests use the fake robot, a strict fake of the `qi` module and a stub LLM endpoint. No robot or Ollama is needed. `tests/qi_double.py` fails on any NAOqi call outside its allowlist, which is what proves the motors-off guarantee. `QiDouble(motors=True)` adds the motion mapping, and the calls that would switch off NAOqi's protections fail in both modes.
