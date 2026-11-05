"""The real adapter against a strict `qi` double. No robot, and `qi` itself is not needed."""

import subprocess
import sys

import pytest

from naollm.robot.adapter import (
    Audio,
    EventKind,
    Events,
    Motion,
    MotorsDisabled,
    Perception,
    RobotAdapter,
    RobotOffline,
    Speech,
    WakeHit,
)
from naollm.robot.qi_adapter import QiRobot, QiUnavailable, RobotConnectionError
from qi_double import ForbiddenCall, QiDouble

URL = "tcp://10.0.0.135:9559"


@pytest.fixture
def double():
    return QiDouble()


@pytest.fixture
def logs():
    return []


@pytest.fixture
def adapter(double, logs):
    robot = QiRobot(qi_module=double, log=lambda kind, message: logs.append((kind, message)))
    robot.connect(URL)
    yield robot
    assert double.violations == []


def collect(adapter):
    seen = []
    adapter.events.subscribe(seen.append)
    return seen


# -- double --------------------------------------------------------------

def test_double_fails_on_a_forbidden_call(adapter, double):
    with pytest.raises(ForbiddenCall):
        adapter.service("ALMotion").wakeUp()
    assert double.violations == ["ALMotion.wakeUp()"]
    double.violations.clear()


def test_double_fails_on_an_unknown_service(adapter, double):
    with pytest.raises(ForbiddenCall):
        adapter.service("ALAnimatedSpeech")
    double.violations.clear()


# -- connection ----------------------------------------------------------

def test_satisfies_every_protocol(adapter):
    assert isinstance(adapter, RobotAdapter)
    assert isinstance(adapter.motion, Motion)
    assert isinstance(adapter.speech, Speech)
    assert isinstance(adapter.audio, Audio)
    assert isinstance(adapter.events, Events)
    assert isinstance(adapter.perception, Perception)


def test_connection_and_info(adapter, double):
    assert double.connects == [URL]
    info = adapter.info()
    assert (info.naoqi_version, info.name, info.robot_id) == ("2.8.5.11", "nao", "P0000TEST")


def test_application_arguments(double):
    robot = QiRobot(listen_ip="10.0.0.165", qi_module=double)
    robot.connect(URL)
    app = double.applications[0]
    assert app.args == ["naollm", f"--qi-url={URL}", "--qi-listen-url=tcp://10.0.0.165:0"]
    assert app.auto_exit is False


def test_no_listen_url_without_listen_ip(adapter, double):
    assert not any("listen" in a for a in double.applications[0].args)


def test_failed_connection_raises_and_can_retry(double):
    double.connect_error = "no route"
    robot = QiRobot(qi_module=double)
    with pytest.raises(RobotConnectionError, match="no route"):
        robot.connect(URL)
    double.connect_error = None
    robot.connect(URL)
    assert robot.connected and len(double.applications) == 1


def test_importing_naollm_does_not_import_qi():
    code = ("import sys; import naollm.main, naollm.bridge, naollm.robot.qi_adapter, naollm.deploy.init; "
            "sys.exit('qi' in sys.modules)")
    assert subprocess.run([sys.executable, "-c", code]).returncode == 0


def test_missing_module_names_the_extra(monkeypatch):
    monkeypatch.setitem(sys.modules, "qi", None)
    with pytest.raises(QiUnavailable, match=r"naollm\[robot\]"):
        QiRobot().connect(URL)


def test_calls_without_a_session_raise_offline(double):
    robot = QiRobot(qi_module=double)
    with pytest.raises(RobotOffline):
        robot.speech.say("x")
    assert robot.events.status().connected is False


# -- speech and eyes -----------------------------------------------------

def test_say_never_animates(adapter, double):
    adapter.speech.say("Hello", animated=True)
    assert double.called("ALTextToSpeech", "say") == [("Hello",)]


def test_stop_speech(adapter, double):
    adapter.speech.stop_speech()
    assert double.called("ALTextToSpeech", "stopAll") == [()]


def test_language_not_installed(adapter, double):
    with pytest.raises(RuntimeError, match="German"):
        adapter.speech.set_language("German")
    assert double.called("ALTextToSpeech", "setLanguage") == []
    assert double.tts_language == "English"


def test_language_set_and_listed(adapter, double):
    adapter.speech.set_language("Chinese")
    assert double.tts_language == "Chinese"
    assert adapter.speech.installed_languages() == ["Japanese", "Chinese", "English"]


def test_eyes(adapter, double):
    adapter.speech.set_eyes("#0000FF")
    assert double.called("ALLeds", "fadeRGB") == [("FaceLeds", 0x0000FF, 0.2)]


# -- motion --------------------------------------------------------------

COMMANDING = [
    ("run_behavior", ("x/y",)), ("stop_all_behaviors", ()), ("play_animation", ("a/b",)),
    ("play_keyframes", (None,)), ("set_balancer", (True,)), ("go_to_posture", ("Stand",)),
    ("move_to", (0.1, 0.0, 0.0)), ("stop_move", ()), ("set_head", (0.0, 0.0)), ("rest", ()), ("wake_up", ()),
]


@pytest.mark.parametrize("method,args", COMMANDING)
def test_motion_commands_raise_without_calling_naoqi(adapter, double, method, args):
    before = len(double.calls)
    with pytest.raises(MotorsDisabled):
        getattr(adapter.motion, method)(*args)
    assert len(double.calls) == before


def test_installed_behaviors(adapter):
    assert adapter.motion.installed_behaviors() == ["animations/Stand/Gestures/Hey_1"]


def test_joint_limits_shape(adapter):
    limits = adapter.motion.joint_limits()
    assert limits["HeadYaw"] == (-1.0, 1.0, 8.27)
    assert "RHand" in limits and len(limits) == 26


def test_joint_limits_flat_shape(adapter, double):
    double.results[("ALMotion", "getLimits")] = [-2.0, 2.0, 8.0, 1.0]
    assert adapter.motion.joint_limits()["HeadYaw"] == (-2.0, 2.0, 8.0)


# -- at rest -------------------------------------------------------------

def test_at_rest(adapter):
    assert adapter.rest_problem() is None


def test_stiff_is_a_problem(adapter, double):
    double.stiffness = [0.0, 0.7]
    assert "stiff" in adapter.rest_problem()


def test_awake_is_a_problem(adapter, double):
    double.awake = True
    assert "awake" in adapter.rest_problem()


def test_life_is_a_problem_only_when_asked(adapter, double):
    double.life = "solitary"
    assert "naollm init" in adapter.rest_problem()
    assert adapter.rest_problem(check_life=False) is None


def test_set_life_needs_the_init_permission(adapter, double):
    with pytest.raises(ForbiddenCall):
        adapter.disable_autonomous_life()
    double.violations.clear()
    double.allow_life_change = True
    double.life = "solitary"
    adapter.disable_autonomous_life()
    assert double.life == "disabled"


# -- events --------------------------------------------------------------

@pytest.mark.parametrize("event,kind", [("FrontTactilTouched", EventKind.FRONT_HEAD),
                                        ("MiddleTactilTouched", EventKind.MIDDLE_HEAD),
                                        ("RearTactilTouched", EventKind.REAR_HEAD)])
def test_one_event_per_press(adapter, double, event, kind):
    seen = collect(adapter)
    double.fire(event, 1.0)
    double.fire(event, 0.0)
    assert [e.kind for e in seen] == [kind]
    double.fire(event, 1.0)
    double.fire(event, 1.0)
    double.fire(event, 0.0)
    assert [e.kind for e in seen] == [kind, kind]


def test_other_events(adapter, double):
    seen = collect(adapter)
    double.posture = double.posture_family = "LyingBelly"
    double.fire("BatteryChargeChanged", 42)
    double.fire("HotDeviceDetected", ["HeadYaw"])
    double.fire("HeadProcessorIsCriticallyHot", 1)
    double.fire("robotHasFallen", 1)
    assert [(e.kind, e.value) for e in seen[:3]] == [(EventKind.BATTERY, 42), (EventKind.THERMAL, "warm"),
                                                     (EventKind.THERMAL, "hot")]
    assert seen[3].kind == EventKind.FALL
    assert adapter.events.status().fallen


def test_handler_error_is_logged_and_later_events_arrive(adapter, double, logs):
    seen = []

    def bad(event):
        raise ValueError("boom")

    adapter.events.subscribe(bad)
    adapter.events.subscribe(seen.append)
    double.fire("RearTactilTouched", 1.0)
    double.fire("RearTactilTouched", 0.0)
    double.fire("FrontTactilTouched", 1.0)
    assert len(seen) == 2
    assert any("boom" in m for k, m in logs)


def test_connection_events(adapter, double):
    seen = collect(adapter)
    double.drop_connection()
    assert [e.kind for e in seen] == [EventKind.CONNECTION_LOST]
    assert adapter.events.status().connected is False
    double.is_open = True
    double.session.connected.fire()
    assert seen[-1].kind == EventKind.CONNECTION_RESTORED
    assert adapter.connected


def test_own_disconnect_is_not_a_loss(adapter, double):
    seen = collect(adapter)
    adapter.disconnect()
    double.session.disconnected.fire("closed")
    assert seen == []


def test_unsubscribe_all_stops_delivery(adapter, double):
    seen = collect(adapter)
    subscriber = double.subscribers["RearTactilTouched"]
    adapter.events.unsubscribe_all()
    assert subscriber.signal.callbacks == {}
    subscriber.signal.fire(1.0)
    assert seen == []


def test_reconnect_restores_subscriptions(adapter, double):
    seen = collect(adapter)
    adapter.disconnect()
    adapter.connect(URL)
    assert double.connects == [URL, URL] and len(double.applications) == 1
    double.fire("RearTactilTouched", 1.0)
    assert [e.kind for e in seen] == [EventKind.REAR_HEAD]


# -- audio stream --------------------------------------------------------

def test_stream_delivers_bytes(adapter, double):
    chunks = []
    adapter.audio.start_stream(chunks.append)
    assert double.called("ALAudioDevice", "setClientPreferences") == [("NaollmAudio", 16000, 3, 0)]
    assert double.called("ALAudioDevice", "subscribe") == [("NaollmAudio",)]
    double.send_audio(bytearray(b"\x01\x02"))
    assert chunks == [b"\x01\x02"] and isinstance(chunks[0], bytes)


def test_stop_is_safe_twice(adapter, double):
    adapter.audio.start_stream(lambda c: None)
    adapter.audio.stop_stream()
    adapter.audio.stop_stream()
    assert len(double.called("ALAudioDevice", "unsubscribe")) == 1
    assert double.registered == {}


def test_failed_start_unregisters_and_raises(adapter, double):
    double.fail[("ALAudioDevice", "subscribe")] = RuntimeError("busy")
    with pytest.raises(RuntimeError, match="busy"):
        adapter.audio.start_stream(lambda c: None)
    assert double.registered == {}


def test_chunk_handler_error_is_logged(adapter, double, logs):
    def bad(chunk):
        raise ValueError("boom")
    adapter.audio.start_stream(bad)
    double.send_audio(b"\0\0")
    assert any("boom" in m for k, m in logs)


def test_listen_sound(adapter, double):
    adapter.audio.play_listen_sound()
    assert len(double.called("ALAudioPlayer", "playSine")) == 1


# -- wake word -----------------------------------------------------------

def wake_setup(adapter, threshold=0.4):
    adapter.audio.configure_wake_words(["Hey NAO"], threshold, "English")


def test_setup_order(adapter, double):
    wake_setup(adapter)
    calls = [m for s, m, a in double.calls if s == "ALSpeechRecognition"]
    assert calls.index("pause") < calls.index("removeAllContext") < calls.index("setVocabulary")
    assert double.called("ALSpeechRecognition", "setVocabulary") == [(["Hey NAO"], True)]
    assert double.called("ALSpeechRecognition", "pause") == [(True,), (False,)]


def test_language_rejected_resumes_and_raises(adapter, double):
    with pytest.raises(RuntimeError, match="German"):
        adapter.audio.configure_wake_words(["Hallo NAO"], 0.4, "German")
    assert double.called("ALSpeechRecognition", "pause")[-1] == (False,)


def test_remove_all_context_failure_is_only_logged(adapter, double, logs):
    double.fail[("ALSpeechRecognition", "removeAllContext")] = RuntimeError("untested")
    wake_setup(adapter)
    assert double.called("ALSpeechRecognition", "setVocabulary")
    assert any("untested" in m for k, m in logs)


def test_above_threshold(adapter, double):
    seen = collect(adapter)
    wake_setup(adapter)
    double.fire("WordRecognized", ["<...> Hey NAO <...>", 0.44])
    assert [e.kind for e in seen] == [EventKind.WAKE_CANDIDATE, EventKind.WAKE_WORD]
    assert seen[0].value == seen[1].value == WakeHit("Hey NAO", 0.44)


def test_below_threshold(adapter, double):
    seen = collect(adapter)
    wake_setup(adapter)
    double.fire("WordRecognized", ["<...> Hey NAO <...>", 0.3])
    assert [e.kind for e in seen] == [EventKind.WAKE_CANDIDATE]


def test_empty_recognition_is_ignored(adapter, double):
    seen = collect(adapter)
    double.fire("WordRecognized", ["", 0.0])
    assert seen == []


def test_enable_pause_resume(adapter, double):
    adapter.audio.set_wake_enabled(False)  # not subscribed yet
    assert double.called("ALSpeechRecognition", "pause") == []
    adapter.audio.set_wake_enabled(True)
    adapter.audio.set_wake_enabled(False)
    adapter.audio.set_wake_enabled(True)
    assert double.called("ALSpeechRecognition", "subscribe") == [("NaollmWake",)]
    assert double.called("ALSpeechRecognition", "pause") == [(True,), (False,)]


def test_disconnect_releases_stream_and_asr(adapter, double):
    adapter.audio.set_wake_enabled(True)
    adapter.audio.start_stream(lambda c: None)
    adapter.disconnect()
    assert double.called("ALSpeechRecognition", "unsubscribe") == [("NaollmWake",)]
    assert double.registered == {}
    assert not double.is_open


def test_disconnect_after_a_lost_session_does_not_raise(adapter, double):
    adapter.audio.start_stream(lambda c: None)
    double.drop_connection()
    adapter.disconnect()
    assert double.registered == {} or adapter.connected is False


# -- status and perception -----------------------------------------------

def test_status_on_a_seated_robot(adapter, double):
    s = adapter.events.status()
    assert (s.battery_pct, s.posture, s.fallen, s.thermal, s.moving, s.connected) == (
        100, "Sitting", False, "ok", False, True)
    used = {(service, method) for service, method, _ in double.calls}
    assert ("ALMotion", "moveIsActive") in used


def test_status_is_kept_briefly(adapter, double):
    adapter.events.status()
    count = len(double.calls)
    adapter.events.status()
    assert len(double.calls) == count


def test_status_thermal_levels(adapter, double):
    double.results[("ALBodyTemperature", "getTemperatureDiagnosis")] = [2, ["HeadYaw"]]
    assert adapter.events.status().thermal == "hot"


def test_status_survives_a_failing_read(adapter, double, logs):
    double.fail[("ALBattery", "getBatteryCharge")] = RuntimeError("no battery")
    s = adapter.events.status()
    assert s.battery_pct == 0 and s.posture == "Sitting"
    assert any("no battery" in m for k, m in logs)


def test_lying_at_rest_is_not_fallen(adapter, double):
    double.posture = double.posture_family = "LyingBack"
    assert adapter.events.status().fallen is False


def test_fall_clears_when_placed_upright(adapter, double):
    double.posture = double.posture_family = "LyingBack"
    double.fire("robotHasFallen", 1)
    assert adapter.events.status().fallen
    adapter.events._status = None
    double.posture = double.posture_family = "Sitting"
    assert adapter.events.status().fallen is False


def test_perception_is_unavailable_before_it_starts(adapter):
    p = adapter.perception
    assert p.available is False
    p.set_tracking(True)
    assert p.recognized_names() == []


def test_perception_start_turns_tracking_off_first(adapter, double):
    adapter.perception.start(True)
    calls = [(s, m, a) for s, m, a in double.calls if s in ("ALFaceDetection", "ALPeoplePerception")]
    assert calls[0] == ("ALFaceDetection", "setTrackingEnabled", (False,))
    assert ("ALFaceDetection", "setRecognitionEnabled", (True,)) in calls
    assert ("ALFaceDetection", "subscribe", ("NaollmPerception",)) in calls
    assert adapter.perception.available


def test_recognition_off_starts_no_face_detection(adapter, double):
    adapter.perception.start(False)
    assert double.called("ALFaceDetection", "setRecognitionEnabled") == [(False,)]
    assert double.called("ALFaceDetection", "subscribe") == []
    assert double.called("ALPeoplePerception", "subscribe") == [("NaollmPerception",)]
    assert "FaceDetected" not in double.subscribers
    assert adapter.perception.recognized_names() == []
    assert adapter.perception.available


def test_failed_tracking_switch_leaves_perception_unavailable(adapter, double):
    double.fail[("ALFaceDetection", "setTrackingEnabled")] = RuntimeError("no service")
    with pytest.raises(RuntimeError):
        adapter.perception.start(True)
    assert adapter.perception.available is False
    assert double.called("ALPeoplePerception", "subscribe") == []


FACE = [[0, 0], [[[0, 0.1, 0.1, 0.2, 0.2], [1, 0.7, "Alex", [], [], [], [], []]], [2, ["Alex"]]], [], [], [], 0]


def test_recently_recognized_names_only(adapter, double):
    p = adapter.perception
    now = [100.0]
    p.clock = lambda: now[0]
    p.start(True)
    double.fire("FaceDetected", FACE)
    assert p.recognized_names() == ["Alex"]
    now[0] += 4.0
    assert p.recognized_names() == ["Alex"]
    now[0] += 6.0
    assert p.recognized_names() == []


@pytest.mark.parametrize("value", [[], None, [[0, 0]], [[0, 0], []], [[0, 0], [[0, []]]], "bad"])
def test_unexpected_face_data_is_ignored(adapter, double, value):
    adapter.perception.start(True)
    double.fire("FaceDetected", value)
    assert adapter.perception.recognized_names() == []


def test_unknown_face_has_no_name(adapter, double):
    adapter.perception.start(True)
    double.fire("FaceDetected", [[0, 0], [[[], [1, 0.0, "", [], [], [], [], []]], [0, []]]])
    assert adapter.perception.recognized_names() == []


def test_people_count(adapter, double):
    adapter.perception.start(False)
    assert adapter.perception.people_count() == 0
    double.visible_people = [11, 12]
    assert adapter.perception.people_count() == 2
    double.results[("ALMemory", "getData")] = None
    assert adapter.perception.people_count() == 0


def test_learn_fails(adapter, double):
    double.learn_results = [False]
    assert adapter.perception.learn_face("Alex") is False
    assert adapter.perception.learned_names() == []


def test_learn_forget_and_forget_all(adapter, double):
    p = adapter.perception
    assert p.learn_face("Alex") and p.learn_face("Sam")
    assert p.learned_names() == ["Alex", "Sam"]
    assert p.forget_face("Alex") and p.learned_names() == ["Sam"]
    assert p.forget_all_faces() and p.learned_names() == []


def test_forget_all_reports_a_face_that_stays(adapter, double):
    double.learned = ["Alex"]
    double.results[("ALFaceDetection", "getLearnedFacesList")] = ["Alex"]
    assert adapter.perception.forget_all_faces() is False


def test_disconnect_stops_perception(adapter, double):
    adapter.perception.start(True)
    adapter.disconnect()
    assert double.called("ALFaceDetection", "unsubscribe") == [("NaollmPerception",)]
    assert double.called("ALPeoplePerception", "unsubscribe") == [("NaollmPerception",)]
    assert adapter.perception.available is False


def test_lost_session_makes_perception_unavailable(adapter, double):
    adapter.perception.start(True)
    double.drop_connection()
    assert adapter.perception.available is False
