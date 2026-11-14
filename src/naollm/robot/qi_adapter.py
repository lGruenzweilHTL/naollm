"""Real robot transport over `qi` (SPEC §6.2).

`qi` is imported only when connecting, so the package and its tests work without it.

The motion class is chosen once, when the adapter connects, from
`motion.motors_enabled`. `QiMotionDisabled` reads and refuses: it has no code that
calls NAOqi for stiffness, posture, behaviors, animations or head tracking, so with
motors off a bug or a wrong config value cannot move the robot. `QiMotion` is the
real transport, with fixed speeds and NAOqi's fall manager, collision protection
and smart stiffness left on. `tests/qi_double.py` fails on any NAOqi call outside
the allowlist of the mode, and on the protection calls in both modes.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from naollm.animations.limits import NAO6_LIMITS
from naollm.robot.adapter import (
    HEAD_SPEED,
    AgeEstimate,
    POSTURE_SPEED,
    EventKind,
    Keyframes,
    MotionFailed,
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
SERVICE_LISTED_S = 2.0  # registerService to the robot listing it took 0.05-0.1 s on Konrad
# NAOqi's own "listening" sound. ALAudioPlayer.playSine is gone in NAOqi 2.8.7.8.
LISTEN_SOUND = "/opt/aldebaran/share/naoqi/wav/begin_reco.wav"
LISTEN_VOLUME = 0.4
# ALBodyTemperature has no methods in NAOqi 2.8.7.8, so the thermal level comes from the
# temperature status of each joint and the battery: 0 ok, 1 hot, 2 very hot, 3 critical.
TEMPERATURE_KEYS = [f"Device/SubDeviceList/{device}/Temperature/Sensor/Status"
                    for device in [*NAO6_LIMITS, "Battery"]]
FRONT_MIC = 3
FACE_LEDS = "FaceLeds"
STATUS_TTL_S = 0.5
BODY_ID_KEY = "Device/DeviceList/ChestBoard/BodyId"
PERCEPTION_CLIENT = "NaollmPerception"
VISIBLE_PEOPLE_KEY = "PeoplePerception/VisiblePeopleList"
# The only value read after analyzeFaceCharacteristics: [age, confidence]. Gender, smile and
# expression keys exist next to it and are never read (age-game spec).
AGE_KEY = "PeoplePerception/Person/{id}/AgeProperties"
RECOGNITION_MEMORY_S = 5.0
HEAD_JOINTS = ["HeadYaw", "HeadPitch"]
POLL_S = 0.05
BEHAVIOR_LIMIT_S = 35.0     # SPEC §9: a behavior finishes within 30 s; 5 s margin
ANIMATION_LIMIT_S = 35.0
KEYFRAME_MARGIN_S = 5.0     # on top of the animation's own duration
MOVE_LIMIT_S = 30.0         # 30 cm or 90 degrees take far less
BALANCE_SUPPORT = "Legs"
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


class _Timeout(Exception):
    pass


def _read_limits(robot: "QiRobot") -> Dict[str, Tuple[float, float, float]]:
    """Radians (hands 0..1): joint -> (min, max, max velocity)."""
    motion = robot.service("ALMotion")
    limits = {}
    for joint in NAO6_LIMITS:
        raw = motion.getLimits(joint)
        # NAOqi 2.8.5.11 returns [[min, max, max_velocity, max_torque]].
        row = raw[0] if raw and isinstance(raw[0], (list, tuple)) else raw
        limits[joint] = (float(row[0]), float(row[1]), float(row[2]))
    return limits


class QiMotion:
    """Real motion (SPEC §6.2). Speeds are the fixed constants above, never a caller's value.

    Long motions run with `_async=True` and are waited on here, so a stop call
    from another thread (the emergency stop) can end them. Each running motion
    has its own token, so the Crouch that follows a stop cannot make an
    interrupted motion look finished.
    """

    def __init__(self, robot: "QiRobot") -> None:
        self._robot = robot
        self._lock = threading.Lock()
        self._active: List[threading.Event] = []
        self._animations: List[Any] = []
        self._keyframe_joints: List[str] = []

    # -- helpers ----------------------------------------------------------
    def _service(self, name: str) -> Any:
        return self._robot.service(name)

    def _begin(self) -> threading.Event:
        token = threading.Event()
        with self._lock:
            self._active.append(token)
        return token

    def _end(self, token: threading.Event) -> None:
        with self._lock:
            if token in self._active:
                self._active.remove(token)
        self._robot.events.invalidate()

    def _interrupt(self) -> None:
        with self._lock:
            for token in self._active:
                token.set()

    def _wait(self, future: Any, token: threading.Event, limit_s: float, what: str) -> Outcome:
        deadline = time.monotonic() + limit_s
        while not future.isFinished():
            if time.monotonic() >= deadline:
                raise _Timeout
            time.sleep(POLL_S)
        if token.is_set():
            return "interrupted"
        if future.hasError():
            raise RuntimeError(f"{what} failed: {future.error()}")
        return "finished"

    # -- reading ------------------------------------------------------------
    def installed_behaviors(self) -> List[str]:
        return list(self._service("ALBehaviorManager").getInstalledBehaviors())

    def joint_limits(self) -> Dict[str, Tuple[float, float, float]]:
        return _read_limits(self._robot)

    # -- behaviors and animations --------------------------------------------
    def run_behavior(self, path: str) -> Outcome:
        manager = self._service("ALBehaviorManager")
        if path not in manager.getInstalledBehaviors():
            raise MotionFailed("not_installed", path)
        token = self._begin()
        try:
            future = manager.runBehavior(path, _async=True)
            try:
                return self._wait(future, token, BEHAVIOR_LIMIT_S, path)
            except _Timeout:
                manager.stopBehavior(path)
                raise MotionFailed("timeout", f"{path} ran longer than {BEHAVIOR_LIMIT_S:g} s") from None
        finally:
            self._end(token)

    def play_animation(self, path: str) -> Outcome:
        token = self._begin()
        future = self._service("ALAnimationPlayer").run(path, _async=True)
        with self._lock:
            self._animations.append(future)
        try:
            try:
                return self._wait(future, token, ANIMATION_LIMIT_S, path)
            except _Timeout:
                future.cancel()
                raise MotionFailed("timeout", f"{path} ran longer than {ANIMATION_LIMIT_S:g} s") from None
        finally:
            with self._lock:
                if future in self._animations:
                    self._animations.remove(future)
            self._end(token)

    def play_keyframes(self, frames: Keyframes) -> Outcome:
        motion = self._service("ALMotion")
        token = self._begin()
        with self._lock:
            self._keyframe_joints = list(frames.names)
        try:
            if frames.bezier:
                future = motion.angleInterpolationBezier(frames.names, frames.times, frames.angles, _async=True)
            else:
                future = motion.angleInterpolation(frames.names, frames.angles, frames.times, True, _async=True)
            try:
                return self._wait(future, token, frames.duration_s + KEYFRAME_MARGIN_S, "keyframes")
            except _Timeout:
                motion.killTasksUsingResources(list(frames.names))
                raise MotionFailed("timeout", "keyframe playback did not end") from None
        finally:
            with self._lock:
                self._keyframe_joints = []
            self._end(token)

    def stop_all_behaviors(self) -> None:
        """Stops behaviors, built-in animations and keyframe playback. Every part runs."""
        self._interrupt()
        with self._lock:
            animations = list(self._animations)
            joints = list(self._keyframe_joints)
        steps: List[Callable[[], Any]] = [lambda: self._service("ALBehaviorManager").stopAllBehaviors()]
        steps += [future.cancel for future in animations]
        if joints:
            steps.append(lambda: self._service("ALMotion").killTasksUsingResources(joints))
        errors = []
        for step in steps:
            try:
                step()
            except Exception as err:
                errors.append(err)
        if errors:
            raise errors[0]

    def set_balancer(self, enabled: bool) -> None:
        motion = self._service("ALMotion")
        if enabled:
            motion.wbEnable(True)
            motion.wbFootState("Fixed", BALANCE_SUPPORT)
            motion.wbEnableBalanceConstraint(True, BALANCE_SUPPORT)
        else:
            motion.wbEnable(False)

    # -- postures, walking, head ----------------------------------------------
    def go_to_posture(self, name: str, speed: float = POSTURE_SPEED) -> bool:
        # `speed` is ignored on purpose: the adapter's constant is the only speed.
        try:
            return bool(self._service("ALRobotPosture").goToPosture(name, POSTURE_SPEED))
        finally:
            self._robot.events.invalidate()  # the next gate check must see the new posture

    def move_to(self, x_m: float, y_m: float, theta_rad: float) -> Outcome:
        motion = self._service("ALMotion")
        token = self._begin()
        try:
            future = motion.moveTo(float(x_m), float(y_m), float(theta_rad), _async=True)
            try:
                return self._wait(future, token, MOVE_LIMIT_S, "moveTo")
            except _Timeout:
                motion.stopMove()
                raise MotionFailed("timeout", f"moveTo did not end within {MOVE_LIMIT_S:g} s") from None
        finally:
            self._end(token)

    def stop_move(self) -> None:
        self._interrupt()
        self._service("ALMotion").stopMove()

    def set_head(self, yaw_rad: float, pitch_rad: float, speed: float = HEAD_SPEED) -> None:
        self._service("ALMotion").setAngles(list(HEAD_JOINTS), [float(yaw_rad), float(pitch_rad)], HEAD_SPEED)

    def rest(self) -> None:
        try:
            self._service("ALMotion").rest()
        finally:
            self._robot.events.invalidate()

    def wake_up(self) -> None:
        try:
            self._service("ALMotion").wakeUp()
        finally:
            self._robot.events.invalidate()


class QiMotionDisabled:
    """Motors off: reads only. Every other method refuses without touching NAOqi."""

    def __init__(self, robot: "QiRobot") -> None:
        self._robot = robot

    def installed_behaviors(self) -> List[str]:
        return list(self._robot.service("ALBehaviorManager").getInstalledBehaviors())

    def joint_limits(self) -> Dict[str, Tuple[float, float, float]]:
        return _read_limits(self._robot)

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
        self._lock = threading.Lock()
        self._animated: Optional[Any] = None

    def say(self, text: str, animated: bool = True) -> None:
        """Blocks until the sentence ends. ALAnimatedSpeech moves the arms, so it needs motors on."""
        if not (animated and self._robot.motors_live):
            self._robot.service("ALTextToSpeech").say(text)
            return
        future = self._robot.service("ALAnimatedSpeech").say(text, _async=True)
        with self._lock:
            self._animated = future
        try:
            while not future.isFinished():
                time.sleep(POLL_S)
            if future.hasError() and not future.isCanceled():
                raise RuntimeError(f"animated speech failed: {future.error()}")
        finally:
            with self._lock:
                if self._animated is future:
                    self._animated = None

    def stop_speech(self) -> None:
        """Stops plain and animated speech; both parts run."""
        with self._lock:
            animated, self._animated = self._animated, None
        try:
            self._robot.service("ALTextToSpeech").stopAll()
        finally:
            if animated is not None:
                animated.cancel()

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


def _wait_until_listed(session: Any, name: str) -> None:
    """`registerService` returns before the robot's service directory lists the service.

    On NAOqi 2.8.7.8, ALAudioDevice asked in that gap fails with "Can't find service".
    """
    deadline = time.monotonic() + SERVICE_LISTED_S
    while not any(entry["name"] == name for entry in session.services()):
        if time.monotonic() >= deadline:
            raise RuntimeError(f"the robot does not list service {name} after {SERVICE_LISTED_S} s")
        time.sleep(POLL_S)


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
                _wait_until_listed(session, AUDIO_CLIENT)
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
        # path, volume 0-1, pan. Returns when the sound has played, so the stream does not pick it up.
        self._robot.service("ALAudioPlayer").playFile(LISTEN_SOUND, LISTEN_VOLUME, 0.0)

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
    def invalidate(self) -> None:
        """After a motion: the next `status()` reads the robot again."""
        self._status_time = 0.0

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
        # A key the robot does not have reads as None.
        statuses = self._robot.service("ALMemory").getListData(TEMPERATURE_KEYS)
        level = max((int(status) for status in statuses if status is not None), default=0)
        return "hot" if level >= 2 else ("warm" if level == 1 else "ok")


class QiPerception:
    """Faces and people through NAOqi. No camera frame leaves the robot, and names stay in memory.

    `start` switches off the tracking that ALFaceDetection reports as on by
    default. Head tracking is a motor command: with motors on, `set_tracking`
    uses ALBasicAwareness in head-only mode, and with motors off it does nothing.
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
        if not self._robot.motors_live:
            return
        awareness = self._robot.service("ALBasicAwareness")
        if enabled:
            awareness.setTrackingMode("Head")
            awareness.setEnabled(True)
        else:
            awareness.setEnabled(False)

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

    # -- age game (SPEC §8.6) ---------------------------------------------
    def probe_age(self) -> bool:
        """The service exists and has the call. Takes no estimate: nobody asked yet."""
        try:
            service = self._robot.service("ALFaceCharacteristics")
        except Exception as err:
            self._robot.log("warning", f"ALFaceCharacteristics unavailable: {err!r}")
            return False
        return callable(getattr(service, "analyzeFaceCharacteristics", None))

    def estimate_age(self) -> Optional[AgeEstimate]:
        """For exactly one visible person, or None. Reads the age and its confidence only."""
        memory = self._robot.service("ALMemory")
        visible = memory.getData(VISIBLE_PEOPLE_KEY) or []
        if len(visible) != 1:
            return None
        person = visible[0]
        if not self._robot.service("ALFaceCharacteristics").analyzeFaceCharacteristics(person):
            return None
        value = memory.getData(AGE_KEY.format(id=person))
        try:
            return AgeEstimate(float(value[0]), float(value[1]))
        except (IndexError, TypeError, ValueError):
            return None


class QiRobot:
    def __init__(self, listen_ip: Optional[str] = None, qi_module: Any = None, log: Optional[Log] = None,
                 motors_enabled: bool = False) -> None:
        self.listen_ip = listen_ip
        self.motors_enabled = motors_enabled
        self.motors_live = False  # True once QiMotion is chosen; never changes back
        self._qi = qi_module
        self._app: Any = None
        self._session: Any = None
        self._signals: List[Tuple[Any, Any]] = []
        self._connecting = False
        self._lost = False
        self.connected = False
        self.log: Log = log or (lambda kind, message: None)
        self.motion: Any = QiMotionDisabled(self)
        self._motion_chosen = False
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

    def _choose_motion(self) -> None:
        """Once per adapter, at the first connect. A reconnect keeps the choice."""
        if self._motion_chosen:
            return
        self._motion_chosen = True
        if self.motors_enabled:
            self.motion = QiMotion(self)
            self.motors_live = True

    def connect(self, url: str) -> None:
        qi = self._qi or _import_qi()
        self._qi = qi
        self._choose_motion()
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

    def set_robot_name(self, name: str) -> None:
        """`naollm init --name` only."""
        self.service("ALSystem").setRobotName(name)
