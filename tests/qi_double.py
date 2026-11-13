"""A strict fake of the `qi` module: any NAOqi call outside the allowlist fails the test.

This is what proves the motors-off guarantee. A forbidden call raises, and it is
also recorded in `violations`, because adapter code may catch the exception.

`ALLOWED` is the motors-off allowlist. `QiDouble(motors=True)` adds
`MOTION_ALLOWED`, the motion mapping of SPEC §6.2. `DENIED` holds the calls that
switch off NAOqi's protections or set stiffness directly; they fail in both modes.
"""

from __future__ import annotations

import threading
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

ALLOWED: Dict[str, Set[str]] = {
    "ALSystem": {"systemVersion", "robotName"},
    "ALMotion": {"getStiffnesses", "robotIsWakeUp", "getLimits", "moveIsActive"},
    "ALAutonomousLife": {"getState"},  # setState only with allow_life_change
    "ALTextToSpeech": {"say", "stopAll", "setLanguage", "getAvailableLanguages"},
    "ALLeds": {"fadeRGB"},
    "ALAudioDevice": {"setClientPreferences", "subscribe", "unsubscribe"},
    "ALAudioPlayer": {"playSine"},
    "ALSpeechRecognition": {"getAvailableLanguages", "pause", "setLanguage", "setVocabulary", "removeAllContext",
                            "setAudioExpression", "setVisualExpression", "subscribe", "unsubscribe"},
    "ALMemory": {"subscriber", "getData"},
    "ALBattery": {"getBatteryCharge"},
    "ALRobotPosture": {"getPosture", "getPostureFamily"},
    "ALBehaviorManager": {"getInstalledBehaviors"},
    "ALBodyTemperature": {"getTemperatureDiagnosis"},
    "ALFaceDetection": {"setTrackingEnabled", "setRecognitionEnabled", "subscribe", "unsubscribe", "learnFace",
                        "forgetPerson", "clearDatabase", "getLearnedFacesList"},
    "ALPeoplePerception": {"subscribe", "unsubscribe"},
    "ALFaceCharacteristics": {"analyzeFaceCharacteristics"},  # the age game; reads go through ALMemory
}

# Values next to the age that the age game must never read (age-game spec).
FORBIDDEN_PERSON_KEYS = ("GenderProperties", "SmileProperties", "ExpressionProperties", "FacialPartsProperties")


# With motors on, on top of ALLOWED.
MOTION_ALLOWED: Dict[str, Set[str]] = {
    "ALMotion": {"wakeUp", "rest", "moveTo", "stopMove", "setAngles", "angleInterpolationBezier",
                 "angleInterpolation", "killTasksUsingResources", "wbEnable", "wbFootState",
                 "wbEnableBalanceConstraint"},
    "ALRobotPosture": {"goToPosture"},
    "ALBehaviorManager": {"runBehavior", "stopBehavior", "stopAllBehaviors"},
    "ALAnimationPlayer": {"run"},
    "ALAnimatedSpeech": {"say"},
    "ALBasicAwareness": {"setEnabled", "setTrackingMode"},
}

# Never allowed, whatever the mode: NAOqi's protections stay on (qi-motion spec).
DENIED: Dict[str, Set[str]] = {
    "ALMotion": {"setFallManagerEnabled", "setExternalCollisionProtectionEnabled", "setSmartStiffnessEnabled",
                 "setStiffnesses", "stiffnessInterpolation"},
}


class ForbiddenCall(AssertionError):
    pass


class Future:
    """What a call with `_async=True` returns. Finished at once unless the test holds it."""

    def __init__(self, value: Any = None, error: Optional[str] = None, finished: bool = True) -> None:
        self._value = value
        self._error = error
        self._done = threading.Event()
        self._canceled = False
        if finished:
            self._done.set()

    def finish(self, error: Optional[str] = None) -> None:
        self._error = error
        self._done.set()

    def cancel(self) -> None:
        self._canceled = True
        self._done.set()

    def isFinished(self) -> bool:  # noqa: N802
        return self._done.is_set()

    def isCanceled(self) -> bool:  # noqa: N802
        return self._canceled

    def hasError(self) -> bool:  # noqa: N802
        return self._error is not None

    def error(self) -> str:
        return self._error or ""

    def value(self) -> Any:
        return self._value


class Signal:
    def __init__(self) -> None:
        self.callbacks: Dict[int, Callable[..., None]] = {}
        self._next = 1

    def connect(self, callback: Callable[..., None]) -> int:
        link = self._next
        self._next += 1
        self.callbacks[link] = callback
        return link

    def disconnect(self, link: int) -> None:
        del self.callbacks[link]

    def fire(self, *args: Any) -> None:
        for callback in list(self.callbacks.values()):
            callback(*args)


class Subscriber:
    def __init__(self) -> None:
        self.signal = Signal()


class Service:
    def __init__(self, double: "QiDouble", name: str) -> None:
        self._double = double
        self._name = name

    def __getattr__(self, method: str) -> Callable[..., Any]:
        def call(*args: Any, _async: bool = False, **kwargs: Any) -> Any:
            if kwargs:
                raise TypeError(f"unexpected keyword arguments {kwargs}")
            if not _async:
                return self._double.handle(self._name, method, args)
            held = self._double.hold.get((self._name, method))
            result = self._double.handle(self._name, method, args)
            if held is not None:
                self._double.futures.append(held)
                return held
            future = Future(result)
            self._double.futures.append(future)
            return future
        return call


class Session:
    def __init__(self, double: "QiDouble") -> None:
        self._double = double
        self.connected = Signal()
        self.disconnected = Signal()
        self._next_service = 100

    def service(self, name: str) -> Service:
        if name in self._double.missing_services:
            raise RuntimeError(f"Cannot find service '{name}'")
        if name not in self._double.allowed and name not in self._double.extra_services:
            self._double.violate(f"service {name}")
        return Service(self._double, name)

    def registerService(self, name: str, obj: Any) -> int:  # noqa: N802
        self._next_service += 1
        self._double.registered[self._next_service] = (name, obj)
        return self._next_service

    def unregisterService(self, service_id: int) -> None:  # noqa: N802
        del self._double.registered[service_id]

    def connect(self, url: str) -> None:
        if self._double.connect_error:
            raise RuntimeError(self._double.connect_error)
        self._double.connects.append(url)
        self._double.is_open = True

    def listen(self, url: str) -> None:
        self._double.listens.append(url)

    def close(self) -> None:
        self._double.is_open = False


class Application:
    def __init__(self, double: "QiDouble", args: List[str], autoExit: bool = True) -> None:  # noqa: N803
        self._double = double
        self.args = args
        self.auto_exit = autoExit
        self.session = double.session

    def start(self) -> None:
        if self._double.connect_error:
            raise RuntimeError(self._double.connect_error)
        self._double.connects.append(self.args[1].split("=", 1)[1])
        self._double.is_open = True


class QiDouble:
    """Pass as `QiRobot(qi_module=double)`. Tests set the attributes to change what the robot reports."""

    def __init__(self, motors: bool = False) -> None:
        self.motors = motors
        self.allowed: Dict[str, Set[str]] = {k: set(v) for k, v in ALLOWED.items()}
        if motors:
            for service, methods in MOTION_ALLOWED.items():
                self.allowed.setdefault(service, set()).update(methods)
        self.allow_name_change = False
        self.missing_services: Set[str] = set()
        self.ages: Dict[int, List[float]] = {}  # person id -> [age, confidence] after an analysis
        self.hold: Dict[Tuple[str, str], Future] = {}  # an async call returns this future, unfinished
        self.futures: List[Future] = []
        self.robot_name_set: Optional[str] = None
        self.calls: List[Tuple[str, str, Tuple[Any, ...]]] = []
        self.violations: List[str] = []
        self.extra_services: Dict[str, Set[str]] = {}
        self.fail: Dict[Tuple[str, str], Exception] = {}
        self.results: Dict[Tuple[str, str], Any] = {}
        self.allow_life_change = False
        self.connect_error: Optional[str] = None
        self.connects: List[str] = []
        self.listens: List[str] = []
        self.applications: List[Application] = []
        self.registered: Dict[int, Tuple[str, Any]] = {}
        self.subscribers: Dict[str, Subscriber] = {}
        self.is_open = False
        self.lock = threading.Lock()
        self.session = Session(self)

        self.version = "2.8.5.11"
        self.name = "nao"
        self.awake = False
        self.stiffness = [0.0, 0.0]
        self.life = "disabled"
        self.tts_languages = ["Japanese", "Chinese", "English"]
        self.tts_language = "English"
        self.asr_languages = ["Chinese", "English", "Japanese"]
        self.battery = 100
        self.posture = "Sitting"
        self.posture_family = "Sitting"
        self.behaviors = ["animations/Stand/Gestures/Hey_1"]
        self.learned: List[str] = []
        self.visible_people: List[int] = []
        self.learn_results: List[bool] = []
        self.posture_results: List[bool] = []  # one per goToPosture; True when empty

    # -- the `qi` module surface -------------------------------------------
    def Application(self, args: List[str], autoExit: bool = True) -> Application:  # noqa: N802,N803
        app = Application(self, args, autoExit)
        self.applications.append(app)
        return app

    # -- test helpers ------------------------------------------------------
    def violate(self, what: str) -> None:
        self.violations.append(what)
        raise ForbiddenCall(f"forbidden NAOqi call: {what}")

    def fire(self, event: str, value: Any) -> None:
        self.subscribers[event].signal.fire(value)

    def called(self, service: str, method: str) -> List[Tuple[Any, ...]]:
        with self.lock:
            return [args for s, m, args in self.calls if s == service and m == method]

    def send_audio(self, chunk: bytes) -> None:
        for _, obj in list(self.registered.values()):
            obj.processRemote(1, len(chunk) // 2, [0, 0], chunk)

    def drop_connection(self) -> None:
        self.is_open = False
        self.session.disconnected.fire("network lost")

    # -- behaviour ---------------------------------------------------------
    def handle(self, service: str, method: str, args: Tuple[Any, ...]) -> Any:
        allowed = self.allowed.get(service, set()) | self.extra_services.get(service, set())
        if method in DENIED.get(service, set()):
            self.violate(f"protection call {service}.{method}{args!r}")
        if service == "ALAutonomousLife" and method == "setState" and self.allow_life_change:
            pass
        elif service == "ALSystem" and method == "setRobotName" and self.allow_name_change:
            pass
        elif method not in allowed:
            self.violate(f"{service}.{method}{args!r}")
        if not self.is_open:
            raise RuntimeError("session closed")
        with self.lock:
            self.calls.append((service, method, args))
        failure = self.fail.get((service, method))
        if failure is not None:
            raise failure
        if (service, method) in self.results:
            return self.results[(service, method)]
        fn = getattr(self, f"_{service}_{method}", None)
        return fn(*args) if fn else None

    def motor_calls(self) -> List[Tuple[str, str, Tuple[Any, ...]]]:
        """Calls from the motion allowlist, in order."""
        with self.lock:
            return [(s, m, a) for s, m, a in self.calls if m in MOTION_ALLOWED.get(s, set())]

    def _ALSystem_systemVersion(self): return self.version  # noqa: E704,N802

    def _ALSystem_setRobotName(self, name):  # noqa: N802
        self.robot_name_set = name
        self.name = name

    def _ALMotion_wakeUp(self):  # noqa: N802
        self.awake = True
        self.stiffness = [1.0] * len(self.stiffness)

    def _ALMotion_rest(self):  # noqa: N802
        self.awake = False
        self.stiffness = [0.0] * len(self.stiffness)
        self.posture = self.posture_family = "Crouch"

    def _ALRobotPosture_goToPosture(self, name, speed):  # noqa: N802
        result = self.posture_results.pop(0) if self.posture_results else True
        if result:
            self.posture = self.posture_family = name
        return result

    def _ALBehaviorManager_stopAllBehaviors(self):  # noqa: N802
        self._finish_held("ALBehaviorManager", "runBehavior")
        self._finish_held("ALAnimationPlayer", "run")

    def _ALBehaviorManager_stopBehavior(self, path):  # noqa: N802
        self._finish_held("ALBehaviorManager", "runBehavior")

    def _ALMotion_stopMove(self):  # noqa: N802
        self._finish_held("ALMotion", "moveTo")

    def _ALMotion_killTasksUsingResources(self, names):  # noqa: N802
        self._finish_held("ALMotion", "angleInterpolationBezier")
        self._finish_held("ALMotion", "angleInterpolation")

    def _ALTextToSpeech_stopAll(self):  # noqa: N802
        self._finish_held("ALAnimatedSpeech", "say")

    def _finish_held(self, service: str, method: str) -> None:
        future = self.hold.pop((service, method), None)
        if future is not None:
            future.finish()
    def _ALSystem_robotName(self): return self.name  # noqa: E704,N802
    def _ALMotion_getStiffnesses(self, names): return list(self.stiffness)  # noqa: E704,N802
    def _ALMotion_robotIsWakeUp(self): return self.awake  # noqa: E704,N802
    def _ALMotion_moveIsActive(self): return False  # noqa: E704,N802
    def _ALMotion_getLimits(self, joint): return [[-1.0, 1.0, 8.27, 1.5]]  # noqa: E704,N802
    def _ALAutonomousLife_getState(self): return self.life  # noqa: E704,N802

    def _ALAutonomousLife_setState(self, state):  # noqa: N802
        self.life = state

    def _ALTextToSpeech_getAvailableLanguages(self): return list(self.tts_languages)  # noqa: E704,N802

    def _ALTextToSpeech_setLanguage(self, language):  # noqa: N802
        if language not in self.tts_languages:
            raise RuntimeError("language not available")
        self.tts_language = language

    def _ALSpeechRecognition_getAvailableLanguages(self): return list(self.asr_languages)  # noqa: E704,N802

    def _ALSpeechRecognition_setLanguage(self, language):  # noqa: N802
        if language not in self.asr_languages:
            raise RuntimeError(f"This engine does not support the following language: {language}")

    def _ALMemory_subscriber(self, event):  # noqa: N802
        sub = self.subscribers.get(event)
        if sub is None:
            sub = self.subscribers[event] = Subscriber()
        return sub

    def _ALMemory_getData(self, key):  # noqa: N802
        if key == "Device/DeviceList/ChestBoard/BodyId":
            return "P0000TEST"
        if key == "PeoplePerception/VisiblePeopleList":
            return list(self.visible_people)
        if key.startswith("PeoplePerception/Person/"):
            if any(key.endswith(part) for part in FORBIDDEN_PERSON_KEYS):
                self.violate(f"forbidden person value ALMemory.getData({key!r})")
            if key.endswith("/AgeProperties"):
                return self.ages.get(int(key.split("/")[2]), [0.0, 0.0])
        self.violate(f"ALMemory.getData({key!r})")

    def _ALFaceCharacteristics_analyzeFaceCharacteristics(self, person):  # noqa: N802
        return person in self.ages

    def _ALFaceDetection_getLearnedFacesList(self): return list(self.learned)  # noqa: E704,N802

    def _ALFaceDetection_learnFace(self, name):  # noqa: N802
        ok = self.learn_results.pop(0) if self.learn_results else True
        if ok:
            self.learned.append(name)
        return ok

    def _ALFaceDetection_forgetPerson(self, name):  # noqa: N802
        if name in self.learned:
            self.learned.remove(name)
            return True
        return False

    def _ALFaceDetection_clearDatabase(self):  # noqa: N802
        self.learned.clear()

    def _ALBattery_getBatteryCharge(self): return self.battery  # noqa: E704,N802
    def _ALRobotPosture_getPosture(self): return self.posture  # noqa: E704,N802
    def _ALRobotPosture_getPostureFamily(self): return self.posture_family  # noqa: E704,N802
    def _ALBehaviorManager_getInstalledBehaviors(self): return list(self.behaviors)  # noqa: E704,N802
    def _ALBodyTemperature_getTemperatureDiagnosis(self): return None  # noqa: E704,N802
