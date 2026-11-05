# Continuation prompt

Paste everything below the line into a new Claude Code session started in `~/naollm`.

---

Continue work on `naollm`, a local bridge that lets a NAO 6 robot hold spoken conversations through a local LLM. It is for a school robotics club showcase. Read `SPEC.md` (v0.3, start with "Interim: English only, motors off") for the design and `README.md` for usage.

## Environment

- This laptop reaches the robot at `nao.local`. NAOqi 2.8.5.11, Python 3.12, `qi` 3.1.5.
- **Never command the robot's motors. The battery is defective.** Test only motor-free features. A read-only check of stiffness before and after a run is cheap, so do it.
- The venv is `.venv`, with `qi`, `qibuild`, `pytest` and `webrtcvad-wheels`. It has no pip; use `uv pip install --python .venv/bin/python`. `faster-whisper` is not installed yet, and needs the `voice` extra.
- Run the tests with `.venv/bin/python -m pytest`. `test_public_endpoint_warns` fails on this network, because `api.example.com` resolves to a private address here. It failed before this work too.
- The root disk has about 3 GB free. Whisper models and the `voice` extra use several hundred MB.

## Working rules

- Use AskUserQuestion for every open decision.
- Write an OpenSpec proposal (`/opsx:propose`) before implementing, then apply it (`/opsx:apply`). Archive only when I ask.
- The phase 0 spike stays outside OpenSpec, in `spike/`.
- Commit per step on `master`, ending each message with the attribution line this session's instructions give.

## State

The spike has run on the robot; see `spike/FINDINGS.md`. It found no German on the robot, a battery that must not be used, and a weak wake word.

Both changes are implemented and tested against the fake robot and `tests/qi_double.py`. They wait for work at the robot, so they are not archived:

- `add-qi-adapter`: real adapter, motors-off mode, voice turn, `naollm init`. Open: task 4.8 (a voice turn on the robot).
- `add-faces-consent`: perception, consent registry, `faces`, `notice`. Open: task 1.1 (data shapes with a person in view) and 5.3 (real enrolment, after the school has cleared it and consent exists).

The `FaceDetected` parsing follows the NAOqi docs and has not seen real data. Run `spike/spike.py --no-motion --faces 15` with a person in view, and compare.

Ran on the real robot with nobody present: `naollm admin` (speech, eyes, status, refusals, estop, reconnect, `who_is_here`, `notice`) and `naollm init --skip-selftest`. See "First run of the real adapter" in `spike/FINDINGS.md`.

Not verified yet:

- A voice turn on the robot and wake word tuning. `naollm admin` and `naollm init` with the self-test worked with the operator present.
- Real speech through Whisper, and turn latency.
- The tests have not run on Python 3.10.
- `naollm eval` has not run against a real model.

Blocked until the battery is replaced: real motion, keyframe playback, head tracking, `qipkg` deploy and `naollm deploy`.

## Audio decision

`faster-whisper` on the laptop is the v1 path (SPEC §11.1). Gemma native audio is only an optional v2 experiment (SPEC §11.2). The default LLM stays `gemma4:e2b`.

## Possible next work

Finish the on-robot checks above, then archive both changes. German needs the store route in `spike/FINDINGS.md`.
