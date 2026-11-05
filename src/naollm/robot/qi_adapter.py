"""Real robot transport over `qi` (SPEC §6.2), with no motor code.

`qi` is imported only when connecting, so the package and its tests work without it.

Motion stays out on purpose: `QiMotion` reads and refuses. Calling NAOqi for
stiffness, posture, behaviors, animations or head tracking is not possible from
here, so a bug or a wrong config value cannot move the robot. `tests/qi_double.py`
fails on any NAOqi call outside its allowlist.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from naollm.animations.limits import NAO6_LIMITS
from naollm.robot.adapter import (
    HEAD_SPEED,
    POSTURE_SPEED,
    EventKind,
    Keyframes,
    MotorsDisabled,
    Outcome,
    RobotEvent,
    RobotInfo,
    RobotOffline,
    RobotStatus,
    ThermalLevel,
    WakeHit,
)

AUDIO_CLIENT = "NaollmAudio"
ASR_CLIENT = "NaollmWake"
AUDIO_RATE = 16000
FRONT_MIC = 3
FACE_LEDS = "FaceLeds"
STATUS_TTL_S = 0.5
BODY_ID_KEY = "Device/DeviceList/ChestBoard/BodyId"
PERCEPTION_CLIENT = "NaollmPerception"
VISIBLE_PEOPLE_KEY = "PeoplePerception/VisiblePeopleList"
RECOGNITION_MEMORY_S = 5.0
TOUCH_EVENTS = {
    "FrontTactilTouched": EventKind.FRONT_HEAD,
    "MiddleTactilTouched": EventKind.MIDDLE_HEAD,
    "RearTactilTouched": EventKind.REAR_HEAD,
}


class QiUnavailable(RuntimeError):
    """The `qi` module is not installed."""


class RobotConnectionError(RuntimeError):
    """The robot cannot be reached."""


Log = Callable[[str, str], None]


def _import_qi() -> Any:
    try:
        import qi
    except ImportError as err:
        raise QiUnavailable(
            f"the real robot needs the qi module ({err}). Install the robot extra: pip install 'naollm[robot]'"
        ) from None
    return qi


def _clean_phrase(raw: str) -> str:
    return raw.replace("<...>", "").strip()


def face_labels(value: Any) -> List[str]:
    """Names in a FaceDetected value, per the NAOqi docs. Checked against no real data yet.

    [timestamp, [face, face, ..., time_filter]], with face = [shape, [id, score, label, ...]]
    and time_filter = [status, [labels]].
    """
    names: List[str] = []
    try:
        entries = value[1]
        for face in entries[:-1]:
            label = face[1][2]
            if isinstance(label, str) and label:
                names.append(label)
        for label in entries[-1][1]:
            if isinstance(label, str) and label:
                names.append(label)
    except (IndexError, TypeError):
        pass
    return names


class QiMotion:
    """Reads only. Every other method refuses without touching NAOqi."""

    def __init__(self, robot: "QiRobot") -> None:
        self._robot = robot

    def installed_behaviors(self) -> List[str]:
        return list(self._robot.service("ALBehaviorManager").getInstalledBehaviors())

    def joint_limits(self) -> Dict[str, Tuple[float, float, float]]:
        """Radians (hands 0..1): joint -> (min, max, max velocity)."""
        motion = self._robot.service("ALMotion")
        limits = {}
        for joint in NAO6_LIMITS:
            raw = motion.getLimits(joint)
            # NAOqi 2.8.5.11 returns [[min, max, max_velocity, max_torque]].
            row = raw[0] if raw and isinstance(raw[0], (list, tuple)) else raw
            limits[joint] = (float(row[0]), float(row[1]), float(row[2]))
        return limits

    def run_behavior(self, path: str) -> Outcome:
        raise MotorsDisabled("run_behavior")

    def stop_all_behaviors(self) -> None:
        raise MotorsDisabled("stop_all_behaviors")

    def play_animation(self, path: str) -> Outcome:
        raise MotorsDisabled("play_animation")

    def play_keyframes(self, frames: Keyframes) -> Outcome:
        raise MotorsDisabled("play_keyframes")

    def set_balancer(self, enabled: bool) -> None:
        raise MotorsDisabled("set_balancer")

    def go_to_posture(self, name: str, speed: float = POSTURE_SPEED) -> bool:
        raise MotorsDisabled("go_to_posture")

    def move_to(self, x_m: float, y_m: float, theta_rad: float) -> Outcome:
        raise MotorsDisabled("move_to")

    def stop_move(self) -> None:
        raise MotorsDisabled("stop_move")

    def set_head(self, yaw_rad: float, pitch_rad: float, speed: float = HEAD_SPEED) -> None:
        raise MotorsDisabled("set_head")

    def rest(self) -> None:
        raise MotorsDisabled("rest")

    def wake_up(self) -> None:
        raise MotorsDisabled("wake_up")


class QiSpeech:
    def __init__(self, robot: "QiRobot") -> None:
        self._robot = robot

    def say(self, text: str, animated: bool = True) -> None:
        # `animated` is ignored: ALAnimatedSpeech moves the arms.
        self._robot.service("ALTextToSpeech").say(text)

    def stop_speech(self) -> None:
        self._robot.service("ALTextToSpeech").stopAll()

    def set_language(self, language: str) -> None:
        tts = self._robot.service("ALTextToSpeech")
        if language not in tts.getAvailableLanguages():
            raise RuntimeError(f"TTS language not installed: {language}")
        tts.setLanguage(language)

    def installed_languages(self) -> List[str]:
        return list(self._robot.service("ALTextToSpeech").getAvailableLanguages())

    def set_eyes(self, rgb_hex: str, duration_s: float = 0.2) -> None:
        self._robot.service("ALLeds").fadeRGB(FACE_LEDS, int(rgb_hex.lstrip("#"), 16), float(duration_s))


class _AudioClient:
    """Registered as a service, so the robot can call back with audio."""

    def __init__(self, on_chunk: Callable[[bytes], None], log: Log) -> None:
        self._on_chunk = on_chunk
        self._log = log

    def processRemote(self, nbOfChannels, nbOfSamplesByChannel, timeStamp, inputBuffer):  # noqa: N802
        try:
            self._on_chunk(bytes(inputBuffer))
        except Exception as err:
            self._log("error", f"audio chunk handling failed: {err!r}")


class QiAudio:
    def __init__(self, robot: "QiRobot") -> None:
        self._robot = robot
        self._lock = threading.Lock()
        self._client_id: Optional[int] = None
        self._client: Optional[_AudioClient] = None
        self._subscribed = False
        self._asr_subscribed = False
        self.threshold = 0.5

    # -- remote stream --------------------------------------------------
    def start_stream(self, on_chunk: Callable[[bytes], None]) -> None:
        with self._lock:
            self._stop_locked()
            session = self._robot.session()
            device = self._robot.service("ALAudioDevice")
            self._client = _AudioClient(on_chunk, self._robot.log)
            self._client_id = session.registerService(AUDIO_CLIENT, self._client)
            try:
                device.setClientPreferences(AUDIO_CLIENT, AUDIO_RATE, FRONT_MIC, 0)
                device.subscribe(AUDIO_CLIENT)
                self._subscribed = True
            except Exception:
                self._stop_locked()
                raise

    def stop_stream(self) -> None:
        with self._lock:
            self._stop_locked()

    def _stop_locked(self) -> None:
        """Safe to call twice. A lost session cannot deliver audio, so failures are only logged."""
        if self._subscribed:
            self._subscribed = False
            try:
                self._robot.service("ALAudioDevice").unsubscribe(AUDIO_CLIENT)
            except Exception as err:
                self._robot.log("warning", f"audio unsubscribe failed: {err!r}")
        if self._client_id is not None:
            client_id, self._client_id = self._client_id, None
            self._client = None
            try:
                self._robot.session().unregisterService(client_id)
            except Exception as err:
                self._robot.log("warning", f"audio client unregister failed: {err!r}")

    # -- sound ----------------------------------------------------------
    def play_listen_sound(self) -> None:
        # frequency Hz, gain %, pan, duration s. Needs no sound file on the robot.
        self._robot.service("ALAudioPlayer").playSine(880, 40, 0, 0.12)

    # -- wake word ------------------------------------------------------
    def wake_languages(self) -> List[str]:
        return list(self._robot.service("ALSpeechRecognition").getAvailableLanguages())

    def configure_wake_words(self, words: List[str], threshold: float, language: str) -> None:
        asr = self._robot.service("ALSpeechRecognition")
        self.threshold = threshold
        asr.pause(True)
        try:
            # Not tested on this robot, and only prevents a duplicate-vocabulary error.
            for optional in (asr.removeAllContext, lambda: asr.setAudioExpression(False),
                             lambda: asr.setVisualExpression(False)):
                try:
                    optional()
                except Exception as err:
                    self._robot.log("warning", f"ASR setup step failed: {err!r}")
            asr.setLanguage(language)
            asr.setVocabulary(list(words), True)  # True = word spotting
        finally:
            asr.pause(False)  # never leave the robot's ASR paused

    def set_wake_enabled(self, enabled: bool) -> None:
        asr = self._robot.service("ALSpeechRecognition")
        if enabled:
            if self._asr_subscribed:
                asr.pause(False)
            else:
                asr.subscribe(ASR_CLIENT)
                self._asr_subscribed = True
        elif self._asr_subscribed:
            asr.pause(True)

    def release(self) -> None:
        """Before the session closes: no stream and no ASR client stay behind."""
        self.stop_stream()
        if self._asr_subscribed:
            self._asr_subscribed = False
            try:
                self._robot.service("ALSpeechRecognition").unsubscribe(ASR_CLIENT)
            except Exception as err:
                self._robot.log("warning", f"ASR unsubscribe failed: {err!r}")


class QiEvents:
    def __init__(self, robot: "QiRobot") -> None:
        self._robot = robot
        self._lock = threading.Lock()
        self._handlers: List[Callable[[RobotEvent], None]] = []
        self._subscribers: List[Tuple[Any, Any]] = []
        self._pressed: Dict[str, bool] = {}
        self._fallen = False
        self._status: Optional[RobotStatus] = None
        self._status_time = 0.0

    # -- subscriptions ---------------------------------------------------
    def subscribe(self, handler: Callable[[RobotEvent], None]) -> None:
        with self._lock:
            self._handlers.append(handler)
        if self._robot.connected:
            self.hook()

    def unsubscribe_all(self) -> None:
        with self._lock:
            self._handlers.clear()
        self.unhook()

    def hook(self) -> None:
        """Subscribes once per session. Called again after a reconnect."""
        with self._lock:
            if self._subscribers:
                return
            memory = self._robot.service("ALMemory")
            table: List[Tuple[str, Callable[[Any], None]]] = [
                (name, self._touch_callback(name, kind)) for name, kind in TOUCH_EVENTS.items()
            ]
            table += [
                ("robotHasFallen", self._on_fall),
                ("BatteryChargeChanged", self._on_battery),
                ("HotDeviceDetected", lambda value: self._on_hot(value, "warm")),
                ("HeadProcessorIsCriticallyHot", lambda value: self._on_hot(value, "hot")),
                ("WordRecognized", self._on_word),
            ]
            for name, callback in table:
                try:
                    subscriber = memory.subscriber(name)
                    link = subscriber.signal.connect(callback)
                    self._subscribers.append((subscriber, link))
                except Exception as err:
                    self._robot.log("error", f"cannot subscribe to {name}: {err!r}")

    def unhook(self) -> None:
        with self._lock:
            subscribers, self._subscribers = self._subscribers, []
            self._pressed.clear()
        for subscriber, link in subscribers:
            try:
                subscriber.signal.disconnect(link)
            except Exception as err:
                self._robot.log("warning", f"event unsubscribe failed: {err!r}")

    def emit(self, kind: EventKind, value: Any = None) -> None:
        with self._lock:
            handlers = list(self._handlers)
        event = RobotEvent(kind, value)
        for handler in handlers:
            try:
                handler(event)
            except Exception as err:
                self._robot.log("error", f"event handler failed for {kind.value}: {err!r}")

    # -- callbacks, on qi threads ----------------------------------------
    def _touch_callback(self, name: str, kind: EventKind) -> Callable[[Any], None]:
        def callback(value: Any) -> None:
            pressed = bool(value)
            with self._lock:
                changed = self._pressed.get(name, False) != pressed
                self._pressed[name] = pressed
            if pressed and changed:
                self.emit(kind)
        return callback

    def _on_fall(self, value: Any) -> None:
        self._fallen = True
        self.emit(EventKind.FALL, value)

    def _on_battery(self, value: Any) -> None:
        self.emit(EventKind.BATTERY, int(value))

    def _on_hot(self, value: Any, level: ThermalLevel) -> None:
        self.emit(EventKind.THERMAL, level)

    def _on_word(self, value: Any) -> None:
        # [phrase, confidence, phrase, confidence, ...]; a spotted phrase looks like "<...> Hey NAO <...>"
        hits = []
        for i in range(0, len(value) - 1, 2):
            phrase = _clean_phrase(str(value[i]))
            if phrase:
                hits.append(WakeHit(phrase, round(float(value[i + 1]), 2)))
        threshold = self._robot.audio.threshold
        for hit in hits:
            self.emit(EventKind.WAKE_CANDIDATE, hit)
        for hit in hits:
            if hit.confidence >= threshold:
                self.emit(EventKind.WAKE_WORD, hit)
                break

    # -- status ----------------------------------------------------------
    def status(self) -> RobotStatus:
        """Read-only. The console polls this every second, so results are kept briefly."""
        if not self._robot.connected:
            old = self._status
            return RobotStatus(old.battery_pct if old else 0, old.posture if old else "Unknown",
                               self._fallen, old.thermal if old else "ok", False, connected=False)
        now = time.monotonic()
        if self._status is not None and now - self._status_time < STATUS_TTL_S:
            return RobotStatus(self._status.battery_pct, self._status.posture, self._fallen,
                               self._status.thermal, self._status.moving, connected=True)
        old = self._status
        posture = self._read(lambda: str(self._robot.service("ALRobotPosture").getPosture()),
                             old.posture if old else "Unknown")
        if self._fallen:
            family = self._read(lambda: str(self._robot.service("ALRobotPosture").getPostureFamily()), "Unknown")
            if family != "Unknown" and not family.startswith("Lying"):
                self._fallen = False  # placed upright again
        self._status = RobotStatus(
            battery_pct=self._read(lambda: int(self._robot.service("ALBattery").getBatteryCharge()),
                                   old.battery_pct if old else 0),
            posture=posture,
            fallen=self._fallen,
            thermal=self._read(self._thermal, old.thermal if old else "ok"),
            moving=self._read(lambda: bool(self._robot.service("ALMotion").moveIsActive()), False),
            connected=True,
        )
        self._status_time = now
        return self._status

    def _read(self, fn: Callable[[], Any], default: Any) -> Any:
        try:
            return fn()
        except Exception as err:
            self._robot.log("warning", f"status read failed: {err!r}")
            return default

    def _thermal(self) -> ThermalLevel:
        # NAOqi 2.8.5.11 returns None when nothing is hot, otherwise [level, [devices]].
        diagnosis = self._robot.service("ALBodyTemperature").getTemperatureDiagnosis()
        level = int(diagnosis[0]) if diagnosis else 0
        return "hot" if level >= 2 else ("warm" if level == 1 else "ok")


class QiPerception:
    """Faces and people through NAOqi. No camera frame leaves the robot, and names stay in memory.

    Head tracking is a motor command, so `set_tracking` does nothing and `start`
    switches off the tracking that ALFaceDetection reports as on by default.
    """

    def __init__(self, robot: "QiRobot") -> None:
        self._robot = robot
        self._lock = threading.Lock()
        self.available = False
        self._recognition = False
        self._subscribed: List[str] = []
        self._face_link: Optional[Tuple[Any, Any]] = None
        self._seen: Dict[str, float] = {}
        self.clock: Callable[[], float] = time.monotonic

    def start(self, recognition: bool) -> None:
        """Raises when a service is missing or unusable, and then stays unavailable."""
        self.stop()
        self.available = False
        detection = self._robot.service("ALFaceDetection")
        people = self._robot.service("ALPeoplePerception")
        detection.setTrackingEnabled(False)
        detection.setRecognitionEnabled(recognition)
        people.subscribe(PERCEPTION_CLIENT)
        self._subscribed.append("ALPeoplePerception")
        if recognition:
            subscriber = self._robot.service("ALMemory").subscriber("FaceDetected")
            self._face_link = (subscriber, subscriber.signal.connect(self._on_face))
            detection.subscribe(PERCEPTION_CLIENT)
            self._subscribed.append("ALFaceDetection")
        self._recognition = recognition
        self.available = True

    def stop(self) -> None:
        for service in self._subscribed:
            try:
                self._robot.service(service).unsubscribe(PERCEPTION_CLIENT)
            except Exception as err:
                self._robot.log("warning", f"{service} unsubscribe failed: {err!r}")
        self._subscribed = []
        if self._face_link is not None:
            subscriber, link = self._face_link
            self._face_link = None
            try:
                subscriber.signal.disconnect(link)
            except Exception as err:
                self._robot.log("warning", f"FaceDetected unsubscribe failed: {err!r}")
        with self._lock:
            self._seen.clear()
        self._recognition = False

    def forget_session(self) -> None:
        """The session is gone, and its subscriptions with it."""
        self._subscribed = []
        self._face_link = None
        self.available = False
        self._recognition = False
        with self._lock:
            self._seen.clear()

    def _on_face(self, value: Any) -> None:
        now = self.clock()
        names = face_labels(value)
        with self._lock:
            for name in names:
                self._seen[name] = now

    def set_tracking(self, enabled: bool) -> None:
        pass

    def people_count(self) -> int:
        visible = self._robot.service("ALMemory").getData(VISIBLE_PEOPLE_KEY)
        return len(visible) if visible else 0

    def recognized_names(self) -> List[str]:
        if not self._recognition:
            return []
        now = self.clock()
        with self._lock:
            self._seen = {n: t for n, t in self._seen.items() if now - t <= RECOGNITION_MEMORY_S}
            return sorted(self._seen)

    def learned_names(self) -> List[str]:
        return list(self._robot.service("ALFaceDetection").getLearnedFacesList())

    def learn_face(self, name: str) -> bool:
        return bool(self._robot.service("ALFaceDetection").learnFace(name))

    def forget_face(self, name: str) -> bool:
        return bool(self._robot.service("ALFaceDetection").forgetPerson(name))

    def forget_all_faces(self) -> bool:
        detection = self._robot.service("ALFaceDetection")
        detection.clearDatabase()
        return not detection.getLearnedFacesList()


class QiRobot:
    def __init__(self, listen_ip: Optional[str] = None, qi_module: Any = None, log: Optional[Log] = None) -> None:
        self.listen_ip = listen_ip
        self._qi = qi_module
        self._app: Any = None
        self._session: Any = None
        self._signals: List[Tuple[Any, Any]] = []
        self._connecting = False
        self._lost = False
        self.connected = False
        self.log: Log = log or (lambda kind, message: None)
        self.motion = QiMotion(self)
        self.speech = QiSpeech(self)
        self.audio = QiAudio(self)
        self.events = QiEvents(self)
        self.perception = QiPerception(self)

    # -- session ---------------------------------------------------------
    def session(self) -> Any:
        if self._session is None or not self.connected:
            raise RobotOffline("no session to the robot")
        return self._session

    def service(self, name: str) -> Any:
        return self.session().service(name)

    def connect(self, url: str) -> None:
        qi = self._qi or _import_qi()
        self._qi = qi
        self._connecting = True
        try:
            if self._app is None:
                args = ["naollm", f"--qi-url={url}"]
                if self.listen_ip:
                    args.append(f"--qi-listen-url=tcp://{self.listen_ip}:0")
                # One Application per process; autoExit=False reports a failed connection here.
                self._app = qi.Application(args, autoExit=False)
                self._app.start()
                self._session = self._app.session
            else:
                self._session = self._app.session
                self._session.connect(url)
                if self.listen_ip:
                    self._session.listen(f"tcp://{self.listen_ip}:0")
        except Exception as err:
            raise RobotConnectionError(f"cannot connect to {url}: {err}") from err
        finally:
            self._connecting = False
        self.connected = True
        self._lost = False
        self._watch_session()
        self.events.hook()

    def _watch_session(self) -> None:
        if self._signals:
            return
        session = self._session
        self._signals = [
            (session.disconnected, session.disconnected.connect(self._on_disconnected)),
            (session.connected, session.connected.connect(self._on_connected)),
        ]

    def _on_disconnected(self, *reason: Any) -> None:
        if not self.connected:  # our own disconnect
            return
        self.connected = False
        self._lost = True
        self.perception.forget_session()
        self.events.emit(EventKind.CONNECTION_LOST)

    def _on_connected(self, *args: Any) -> None:
        if self._connecting or not self._lost:
            return
        self.connected = True
        self._lost = False
        self.events.hook()
        self.events.emit(EventKind.CONNECTION_RESTORED)

    def disconnect(self) -> None:
        if self.connected:
            self.audio.release()
            self.perception.stop()
        else:
            self.perception.forget_session()
            self.audio._asr_subscribed = False
            self.audio.stop_stream()
        self.events.unhook()
        self.perception.forget_session()
        self.connected = False
        if self._session is not None:
            self._session.close()

    def info(self) -> RobotInfo:
        system = self.service("ALSystem")
        name = str(system.robotName())
        try:
            robot_id = str(self.service("ALMemory").getData(BODY_ID_KEY)) or name
        except Exception as err:
            self.log("warning", f"cannot read the robot id: {err!r}")
            robot_id = name
        return RobotInfo(naoqi_version=str(system.systemVersion()), robot_id=robot_id, name=name)

    # -- at rest ---------------------------------------------------------
    def rest_problem(self, check_life: bool = True) -> Optional[str]:
        motion = self.service("ALMotion")
        stiffness = max(motion.getStiffnesses("Body"), default=0.0)
        if motion.robotIsWakeUp() or stiffness > 0:
            return f"the robot is awake or stiff (max stiffness {stiffness:.2f})"
        if check_life:
            state = self.autonomous_life()
            if state != "disabled":
                return f"Autonomous Life is {state}; run 'naollm init'"
        return None

    def autonomous_life(self) -> str:
        return str(self.service("ALAutonomousLife").getState())

    def disable_autonomous_life(self) -> None:
        """`naollm init` only, after the operator confirms a safe position."""
        self.service("ALAutonomousLife").setState("disabled")
