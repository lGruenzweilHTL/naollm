# Phase 0: connectivity spike

This is a standalone script, outside OpenSpec and outside the `naollm` package. It answers one question before the real robot adapter is built: can Python 3 on the operator laptop do everything the bridge needs on this NAO? See SPEC §16, phase 0.

Run it on the **operator laptop**, on the same LAN as the robot (your own router, not school Wi-Fi). It was written without access to a robot or to `qi`, so expect to fix small API details on the first run.

## Before you start

- Charge the robot. Put it on the floor in a clear area, with an operator next to it.
- Install `qi` into a fresh venv, using the Python version that has a `qi` wheel:
  ```bash
  python3 -m venv .venv-spike && . .venv-spike/bin/activate
  pip install qi qipkg
  ```
  If `pip install qi` finds no wheel for your Python, try another Python version and write down which ones work.
- Check that the laptop firewall allows incoming connections from the robot. Remote audio needs the robot to call back into the laptop.

## Run

```bash
python spike/spike.py --robot <robot-ip> --listen-ip <laptop-ip> \
    --behavior <an installed behavior path> \
    --package choregraphe/showcase/showcase.pml   # optional, once a Choregraphe project exists
```

- The script asks before anything moves. Leave out `--behavior` on the first run; it lists the installed behaviors for you to pick one.
- The mic check records 5 s of audio and keeps it only in memory. `--save-wav /tmp/mic.wav` writes it out so you can listen to the quality; delete the file afterwards.
- `--skip audio wakeword` skips checks, for example in a noisy room.

## Checklist

| # | Check | Pass when |
| --- | --- | --- |
| 1 | connect | NAOqi 2.8.x is reported over `qi` from Python 3 |
| 2 | speak | German and English sentences are spoken; `ALAnimatedSpeech` works |
| 3 | battery | A charge value is printed |
| 4 | behavior | An installed behavior runs to the end, and `stopAllBehaviors` stops it early |
| 5 | audio | About 5 s of 16 kHz front-mic audio arrive on the laptop |
| 6 | wakeword | "Hallo NAO" / "Hey NAO" are spotted (German ASR) above the threshold, also at 1 m |
| 7 | qipkg | A package is built and installed through the robot's PackageManager |
| + | extras | Default gesture paths are present, head touch events arrive, joint limits print |

If a check fails, pick the fallback from SPEC §11.1 / §13:
- connect: Python 2.7 shim or ROS 2.
- audio: `ALAudioRecorder`.
- wakeword: head tap and operator key only, or the v2 laptop-side wake word.

Then fill in [`FINDINGS.md`](FINDINGS.md). Its answers feed the change that builds the real `qi` adapter.
