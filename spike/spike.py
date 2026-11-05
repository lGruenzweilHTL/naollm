#!/usr/bin/env python3
"""Phase 0 connectivity spike (SPEC §16). Run on the operator laptop, next to the robot.

Checks, in order, each independently:
  1. connect      Python 3 `qi` can open a session to NAOqi 2.8 and read the version
  2. speak        ALTextToSpeech says one German and one English sentence
  3. battery      ALBattery charge
  4. behavior     one installed Choregraphe behavior runs (asks before moving)
  5. audio        5 s of front-mic audio arrive on the laptop as a remote ALAudioDevice client
  6. wakeword     ALSpeechRecognition word spotting hears "Hallo NAO" / "Hey NAO"
  7. qipkg        `qipkg make-package` + `deploy-package` install a package (optional)
plus extras that answer SPEC §17 questions (languages, built-in animation paths,
Autonomous Life state, joint limits, head touch events).

Nothing is stored except what you ask for with --save-wav (for listening to the
mic quality yourself; delete it afterwards). Results go to stdout; copy them into
spike/FINDINGS.md.

Usage:
  pip install qi            # check which Python versions have a wheel
  python spike.py --robot 192.168.8.20 [--listen-ip 192.168.8.10] \
      [--behavior showcase/dance_macarena] [--package ../choregraphe/showcase/showcase.pml]
"""

from __future__ import annotations

import argparse
import math
import platform
import shutil
import struct
import subprocess
import sys
import threading
import time
import traceback
import wave

RESULTS = []


def record(name, ok, detail=""):
    RESULTS.append((name, ok, detail))
    mark = "PASS" if ok else ("SKIP" if ok is None else "FAIL")
    print(f"[{mark}] {name}: {detail}", flush=True)


def ask(question):
    try:
        return input(f"{question} [y/N] ").strip().lower() == "y"
    except EOFError:
        return False


def check(name):
    def deco(fn):
        def run(*args, **kwargs):
            print(f"\n=== {name} ===", flush=True)
            try:
                fn(*args, **kwargs)
            except Exception as err:  # keep going with the other checks
                record(name, False, f"{type(err).__name__}: {err}")
                traceback.print_exc()
        return run
    return deco


# --- 1 ------------------------------------------------------------------------
def connect(args):
    print("=== connect ===", flush=True)
    print(f"laptop: Python {platform.python_version()} on {platform.platform()}")
    try:
        import qi
    except ImportError as err:
        record("connect", False, f"cannot import qi ({err}). Try `pip install qi`; if no wheel exists for "
               "this Python, try another Python version, else fall back to the Python 2.7 SDK shim (SPEC §13).")
        return None, None
    print(f"qi module: {getattr(qi, '__version__', 'unknown version')} from {qi.__file__}")
    qi_args = ["naollm-spike", f"--qi-url=tcp://{args.robot}:9559"]
    if args.listen_ip:
        qi_args.append(f"--qi-listen-url=tcp://{args.listen_ip}:0")
    app = qi.Application(qi_args)
    app.start()
    session = app.session
    system = session.service("ALSystem")
    version = system.systemVersion()
    name = system.robotName()
    ok = version.startswith("2.8")
    record("connect", ok, f"robot '{name}', NAOqi {version}" + ("" if ok else " (expected 2.8.x)"))
    return app, session


def motors_off(session):
    """--no-motion gate: run the checks only while the robot is at rest with its motors off.

    Autonomous Life can wake the robot by itself, so a state other than "disabled" also stops the run,
    unless the operator chooses to disable it.
    """
    print("\n=== motors-off ===", flush=True)
    life = session.service("ALAutonomousLife")
    motion = session.service("ALMotion")

    def awake():
        stiffness = max(motion.getStiffnesses("Body"))
        print(f"Autonomous Life {life.getState()}, wake-up {motion.robotIsWakeUp()}, max stiffness {stiffness}")
        return motion.robotIsWakeUp() or stiffness > 0

    if awake():
        record("motors-off", False, "robot is awake or stiff; put it at rest, then run again")
        return False
    if life.getState() != "disabled":
        print("Autonomous Life can wake the robot during the run. The effect of disabling it was not tested.")
        if not ask("Robot in a safe position, set Autonomous Life to disabled now?"):
            record("motors-off", False, f"Autonomous Life stays {life.getState()}; run stopped")
            return False
        life.setState("disabled")
        if awake():
            record("motors-off", False, "robot is awake or stiff after disabling Autonomous Life; run stopped")
            return False
    record("motors-off", True, f"Autonomous Life {life.getState()}, motors off")
    return True


# --- 2 ------------------------------------------------------------------------
@check("speak")
def speak(session, animated=True):
    tts = session.service("ALTextToSpeech")
    langs = tts.getAvailableLanguages()
    print(f"installed TTS languages: {langs}")
    sentences = (("German", "Hallo, ich bin NAO."), ("English", "Hello, I am NAO."))
    spoken = []
    for lang, text in sentences:
        if lang in langs:
            tts.setLanguage(lang)
            tts.say(text)
            spoken.append(lang)
    missing = [lang for lang, _ in sentences if lang not in spoken]
    ok = not missing  # SPEC §11.1 needs both languages
    summary = f"spoke in {spoken}; missing {missing}; languages {langs}"
    if not animated:
        record("speak", ok, f"{summary}; ALAnimatedSpeech not tested (--no-motion)")
        return
    tts.setLanguage("German" if "German" in langs else langs[0])
    session.service("ALAnimatedSpeech").say("Und jetzt mit Gesten.")
    record("speak", ok, f"{summary}; ALAnimatedSpeech ok")


# --- 3 ------------------------------------------------------------------------
@check("battery")
def battery(session):
    charge = session.service("ALBattery").getBatteryCharge()
    record("battery", True, f"{charge} %")


# --- 4 ------------------------------------------------------------------------
@check("behavior")
def behavior(session, path):
    bm = session.service("ALBehaviorManager")
    installed = bm.getInstalledBehaviors()
    print(f"{len(installed)} installed behaviors; first 15 non-animation ones:")
    for b in [b for b in installed if not b.startswith("animations/")][:15]:
        print(f"  {b}")
    if not path:
        record("behavior", None, "no --behavior given; run again with one of the paths above")
        return
    if path not in installed:
        record("behavior", False, f"{path} not installed")
        return
    if not ask(f"Robot on the floor, area clear, run '{path}' now?"):
        record("behavior", None, "skipped by operator")
        return
    motion = session.service("ALMotion")
    motion.wakeUp()
    session.service("ALRobotPosture").goToPosture("Stand", 0.6)
    t0 = time.time()
    bm.runBehavior(path)  # blocks until done
    record("behavior", True, f"{path} finished in {time.time() - t0:.1f} s")
    print("stopAllBehaviors test: starting again and stopping after 1 s")
    if ask("Run it once more to test stopping?"):
        future = bm.runBehavior(path, _async=True)
        time.sleep(1.0)
        bm.stopAllBehaviors()
        future.wait(5000)
        print(f"  stopped; running now: {bm.getRunningBehaviors()}")


# --- 5 ------------------------------------------------------------------------
class AudioCapture(object):
    def __init__(self):
        self.chunks = []
        self.lock = threading.Lock()

    def processRemote(self, nbOfChannels, nbOfSamplesByChannel, timeStamp, inputBuffer):  # noqa: N802
        with self.lock:
            self.chunks.append(bytes(inputBuffer))


def rms_dbfs(pcm):
    n = len(pcm) // 2
    if not n:
        return float("-inf")
    samples = struct.unpack(f"<{n}h", pcm[: n * 2])
    rms = math.sqrt(sum(s * s for s in samples) / n)
    return 20 * math.log10(rms / 32768) if rms else float("-inf")


@check("audio")
def audio(session, save_wav):
    capture = AudioCapture()
    service_id = session.registerService("NaollmSpikeAudio", capture)
    device = session.service("ALAudioDevice")
    try:
        # 16 kHz, channel 3 = front microphone, deinterleave 0
        device.setClientPreferences("NaollmSpikeAudio", 16000, 3, 0)
        print("Speak to the robot for 5 s ...", flush=True)
        device.subscribe("NaollmSpikeAudio")
        time.sleep(5.0)
        device.unsubscribe("NaollmSpikeAudio")
    finally:
        session.unregisterService(service_id)
    pcm = b"".join(capture.chunks)
    seconds = len(pcm) / 2 / 16000
    detail = f"{len(capture.chunks)} chunks, {seconds:.1f} s of audio, level {rms_dbfs(pcm):.1f} dBFS"
    if save_wav and pcm:
        with wave.open(save_wav, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(16000)
            w.writeframes(pcm)
        detail += f"; saved to {save_wav} (delete it after listening)"
    record("audio", seconds >= 4.0, detail + ("" if seconds >= 4.0 else
           ". No audio: is the laptop reachable from the robot? Try --listen-ip <laptop LAN IP>, check the "
           "firewall, else use the ALAudioRecorder fallback (SPEC §11.1)."))


# --- 6 ------------------------------------------------------------------------
@check("wakeword")
def wakeword(session, seconds, threshold, language, words):
    asr = session.service("ALSpeechRecognition")
    memory = session.service("ALMemory")
    available = asr.getAvailableLanguages()
    print(f"ASR languages: {available}")
    if language not in available:
        record("wakeword", False, f"ASR language {language} not available; available {available}")
        return
    asr.pause(True)
    try:
        asr.setLanguage(language)
        asr.setVocabulary(words, True)  # True = word spotting
    finally:
        asr.pause(False)  # resume even when the setup fails, so the robot's ASR is not left paused
    heard = []
    sub = memory.subscriber("WordRecognized")
    sub.signal.connect(lambda value: heard.append(value))
    asr.subscribe("NaollmSpike")
    try:
        print(f"Say {words} a few times within {seconds} s (also try at 1 m) ...", flush=True)
        time.sleep(seconds)
    finally:
        asr.unsubscribe("NaollmSpike")
    hits = []
    for value in heard:
        # value: [word, confidence, word, confidence, ...]; spotted words look like "<...> Hallo NAO <...>"
        for i in range(0, len(value) - 1, 2):
            if value[i]:
                hits.append((value[i], round(float(value[i + 1]), 2)))
    good = [h for h in hits if h[1] >= threshold]
    record("wakeword", bool(good), f"{language}: {len(hits)} recognitions, {len(good)} above {threshold}: {hits}")


# --- 7 ------------------------------------------------------------------------
@check("qipkg")
def qipkg(robot, pml):
    exe = shutil.which("qipkg")
    if not exe:
        record("qipkg", False, "qipkg not found: pip install qibuild (it provides qipkg; check it supports this Python)")
        return
    if not pml:
        out = subprocess.run([exe, "--help"], capture_output=True, text=True)
        record("qipkg", None, f"qipkg present ({out.returncode}); give --package <file.pml> to test a deploy")
        return
    made = subprocess.run([exe, "make-package", pml], capture_output=True, text=True)
    print(made.stdout, made.stderr)
    pkgs = sorted(p for p in made.stdout.split() if p.endswith(".pkg"))
    if made.returncode != 0 or not pkgs:
        record("qipkg", False, "make-package failed (see output above)")
        return
    deployed = subprocess.run([exe, "deploy-package", pkgs[-1], "--url", f"nao@{robot}"],
                              capture_output=True, text=True)
    print(deployed.stdout, deployed.stderr)
    record("qipkg", deployed.returncode == 0, f"deploy-package exit {deployed.returncode}")


# --- faces --------------------------------------------------------------------
@check("faces")
def faces(session, seconds):
    """Read-only, no motors: learns no face and saves no image. Reports data shapes only, not names."""
    detection = session.service("ALFaceDetection")
    people = session.service("ALPeoplePerception")
    memory = session.service("ALMemory")
    print(f"learned faces: {len(detection.getLearnedFacesList())}")
    print(f"recognition enabled: {detection.isRecognitionEnabled()}, tracking enabled: {detection.isTrackingEnabled()}")
    events = set(memory.getEventList())
    wanted = ("FaceDetected", "PeoplePerception/VisiblePeopleList", "PeoplePerception/PeopleDetected")
    print(f"events: { {name: name in events for name in wanted} }")
    shapes = {}

    def shape(value, depth=0):
        if isinstance(value, (list, tuple)):
            if depth >= 5 or not value:
                return f"list[{len(value)}]"
            return [shape(v, depth + 1) for v in list(value)[:6]]
        return type(value).__name__

    detection.subscribe("NaollmSpikeFaces")
    people.subscribe("NaollmSpikePeople")
    try:
        print(f"Look at the robot's camera for {seconds} s ...", flush=True)
        end = time.time() + seconds
        counts = {"faces": 0, "people": 0}
        while time.time() < end:
            face = memory.getData("FaceDetected")
            visible = memory.getData("PeoplePerception/VisiblePeopleList")
            if face:
                counts["faces"] += 1
                shapes["FaceDetected"] = shape(face)
            if visible:
                counts["people"] = max(counts["people"], len(visible))
                shapes["VisiblePeopleList"] = shape(visible)
            time.sleep(0.2)
    finally:
        detection.unsubscribe("NaollmSpikeFaces")
        people.unsubscribe("NaollmSpikePeople")
    print(f"data shapes: {shapes}")
    record("faces", True, f"services respond; samples with a face: {counts['faces']}, most people: {counts['people']}; "
                          f"tracking enabled: {detection.isTrackingEnabled()}; shapes: {shapes or 'nobody seen'}")


# --- extras -------------------------------------------------------------------
@check("extras")
def extras(session, seconds):
    life = session.service("ALAutonomousLife")
    print(f"Autonomous Life state: {life.getState()}")
    bm = session.service("ALBehaviorManager")
    installed = set(bm.getInstalledBehaviors())
    defaults = ["animations/Stand/Gestures/Hey_1", "animations/Stand/Gestures/Yes_1",
                "animations/Stand/Gestures/No_3", "animations/Stand/Gestures/IDontKnow_1",
                "animations/Stand/Gestures/Thinking_1", "animations/Stand/Gestures/Enthusiastic_4"]
    for path in defaults:
        print(f"  default gesture {path}: {'installed' if path in installed else 'MISSING'}")
    motion = session.service("ALMotion")
    for joint in ("HeadYaw", "HeadPitch", "LShoulderPitch", "RElbowRoll"):
        print(f"  limits {joint}: {motion.getLimits(joint)}")
    print(f"  posture family: {session.service('ALRobotPosture').getPostureFamily()}")
    memory = session.service("ALMemory")
    touched = []
    subs = []
    sensors = ("FrontTactilTouched", "MiddleTactilTouched", "RearTactilTouched")
    for ev in sensors:
        s = memory.subscriber(ev)
        s.signal.connect(lambda v, ev=ev: touched.append((ev, v)))
        subs.append(s)
    print(f"Touch the front, middle and rear head sensors within {seconds} s ...", flush=True)
    time.sleep(seconds)
    seen = sorted({ev for ev, v in touched if v})
    missing = [ev for ev in sensors if ev not in seen]
    record("extras", not missing, f"head touch events seen: {seen}; missing: {missing}")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--robot", required=True, help="robot IP or hostname")
    p.add_argument("--listen-ip", help="laptop LAN IP the robot can reach (needed for remote audio on some setups)")
    p.add_argument("--behavior", help="installed behavior path to run, e.g. showcase/dance_macarena")
    p.add_argument("--package", help=".pml file to build and deploy with qipkg")
    p.add_argument("--save-wav", help="write the 5 s mic test to this WAV file")
    p.add_argument("--wake-seconds", type=float, default=20)
    p.add_argument("--wake-threshold", type=float, default=0.5)
    p.add_argument("--wake-language", default="German", help="ASR language for the wake word check (SPEC §11.1: German)")
    p.add_argument("--wake-words", nargs="+", default=["Hallo NAO", "Hey NAO"], help="phrases to spot")
    p.add_argument("--faces", type=float, metavar="SECONDS", help="also run the read-only face and people probe")
    p.add_argument("--skip", nargs="*", default=[], help="checks to skip")
    p.add_argument("--no-motion", action="store_true",
                   help="never command the motors: stop unless the robot is at rest, list behaviors without "
                        "running them, leave out ALAnimatedSpeech and the final rest")
    args = p.parse_args()
    if args.no_motion and args.behavior:
        p.error("--behavior runs motors, so it does not work with --no-motion")

    app, session = connect(args)
    proceed = session is None or not args.no_motion or motors_off(session)
    if session is not None and proceed:
        steps = [
            ("speak", lambda: speak(session, animated=not args.no_motion)),
            ("battery", lambda: battery(session)),
            ("behavior", lambda: behavior(session, args.behavior)),
            ("audio", lambda: audio(session, args.save_wav)),
            ("wakeword", lambda: wakeword(session, args.wake_seconds, args.wake_threshold,
                                          args.wake_language, args.wake_words)),
            ("extras", lambda: extras(session, 15)),
        ]
        if args.faces:
            steps.append(("faces", lambda: faces(session, args.faces)))
        for name, fn in steps:
            if name in args.skip:
                record(name, None, "skipped (--skip)")
            else:
                fn()
    if "qipkg" not in args.skip and proceed:
        qipkg(args.robot, args.package)
    if session is not None and not args.no_motion:
        try:
            session.service("ALMotion").rest()
        except Exception:
            pass

    print("\n=== summary (paste into spike/FINDINGS.md) ===")
    for name, ok, detail in RESULTS:
        mark = "PASS" if ok else ("SKIP" if ok is None else "FAIL")
        print(f"| {name} | {mark} | {detail} |")
    return 0 if all(ok is not False for _, ok, _ in RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
