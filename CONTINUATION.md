# Continuation prompt

Paste everything below the line into a new Claude Code session started in `~/naollm`.

---

Continue work on `naollm`, a local bridge that lets a NAO 6 robot hold spoken conversations through a local LLM and move through tool calls that a safety gate checks. It is for a school robotics club showcase. Read `SPEC.md` (v0.2) for the design and `README.md` for usage.

## Environment

- This is a dev container with no NAO connection. Do not install NAOqi or `qi` here. Robot tests and real model tests run on my laptop, not here.
- The root disk has about 1.5 GB free. Put caches, downloads and model files in `/tmp`.
- The venv is `/tmp/naollm-venv`. If it is gone, recreate it. `ensurepip` is missing, so use `python3 -m venv --without-pip`, bootstrap pip with `get-pip.py`, then run `pip install -e '.[dev]'`.
- Run the tests with `/tmp/naollm-venv/bin/pytest`. Last result: 160 passed in about 47 s.
- Only Python 3.13 is available here. The code targets `>=3.10`, so avoid 3.11+ only features.
- `master` tracks `origin` (`git@github.com:lGruenzweilHTL/naollm.git`).

## Working rules

- Use AskUserQuestion for every open decision.
- Write an OpenSpec proposal (`/opsx:propose`) before implementing, then apply it (`/opsx:apply`). Archive only when I ask.
- The phase 0 spike stays outside OpenSpec, in `spike/`.
- Commit per step on `master`, ending each message with the attribution line this session's instructions give.

## State

Done, against the fake robot only:

- Config, the robot adapter interface and the fake robot
- Safety gate, state machine, emergency stop and event watcher
- The 10 LLM tools
- Text conversation loop with German and English prompts
- Operator console (`naollm run`, `naollm admin`, hotkeys, two-stage Ctrl+C, signals); visitor text is typed with `chat`
- Keyframe animations: format, validator, Choregraphe import, playback
- `naollm eval` with 40 cases
- `spike/spike.py`, its checklist and the `FINDINGS.md` template

The OpenSpec change `add-offline-core` is complete (36/36 tasks) but not archived, so `openspec/specs/` is still empty.

Waiting for the spike, which needs NAO access:

- The real `qi` adapter (pip `qi`, Python 2 shim or ROS 2, decided by the spike)
- The robot audio stream and wake word
- `naollm init` and `naollm deploy`
- A Choregraphe project with real dances and gestures
- Confirming the default gesture paths, which are unverified guesses
- Reviewing `animations/victory.anim.yaml` on the robot (`reviewed_by` is deliberately empty)
- The hardware parts of perception: head tracking, `who_is_here`, face enrolment

Not verified yet:

- The tests have not run on Python 3.10.
- `naollm eval` has not run against a real model.
- `spike/spike.py` has never talked to NAOqi, so expect small API fixes on the first run.

## Audio decision

`faster-whisper` on the laptop is the v1 path (SPEC §11.1). Gemma native audio is only an optional v2 experiment (SPEC §11.2): E4B only, and only if it beats Whisper on our own recorded clips in both German and English. The default LLM stays `gemma4:e2b`.

## Possible next work

I had not chosen yet. The proposed next change was a VAD + `faster-whisper` wrapper with a WAV file input, so a whole turn can run from a recorded clip before the robot is available. The other option that needs no robot is the consent registry and `faces` commands against the fake robot.

Start by checking `git status` and running the tests, then ask me which one to do.
