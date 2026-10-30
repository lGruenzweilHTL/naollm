# Phase 0 findings

Date: YYYY-MM-DD · Robot name: ____ · Operator: ____

## Environment

| Item | Value |
| --- | --- |
| NAOqi version (exact) | |
| Laptop OS, model, RAM | |
| Python version used | |
| `qi` version / wheel source | |
| Python versions with a `qi` wheel | |
| `qipkg` version | |
| Network (router, 2.4/5 GHz, laptop on Ethernet?) | |

## Results

Paste the summary table that the script prints:

| Check | Result | Detail |
| --- | --- | --- |
| connect | | |
| speak | | |
| battery | | |
| behavior | | |
| audio | | |
| wakeword | | |
| qipkg | | |
| extras | | |

## Answers to SPEC §17

1. NAOqi version, and the Python version the `qi` wheel supports:
2. ~~Languages~~ (resolved: German + English)
3. Installed TTS languages / voices:
4. Laptop RAM (`gemma4:e2b` or `e4b`):

## Details for the real adapter

- Default gesture paths that are missing (and their replacements):
- Does `--listen-ip` need to be set for remote audio?
- Wake-word confidences heard (quiet room / noisy room / at 1 m):
- `stopAllBehaviors` latency when stopping a running behavior:
- Autonomous Life state at boot, and what had to be changed:
- Head touch events seen (front / middle / rear):
- Anything that behaved differently from SPEC §6.2:

## Decision

- [ ] pip `qi` on Python 3 works → build `qi_adapter.py` directly
- [ ] Python 2.7 shim needed (SPEC §13 a)
- [ ] ROS 2 `naoqi_driver2` needed (SPEC §13 b)
- [ ] Audio via `ALAudioRecorder` fallback
