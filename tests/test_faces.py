"""Consent registry, enrolment, deletion, who_is_here and the spoken notice."""

import asyncio
import json
import os
import stat
import threading
import time
from datetime import date, timedelta

import pytest

from conftest import make_config
from llm_stub import ScriptedClient
from naollm.admin.commands import Commands
from naollm.admin.consent import ConsentRecord, ConsentRegistry, RegistryDamaged
from naollm.admin.faces import Faces, valid_name
from naollm.bridge import Bridge
from naollm.llm.loop import ConversationLoop
from naollm.robot.adapter import EventKind
from naollm.robot.qi_adapter import QiRobot
from naollm.state import State
from qi_double import QiDouble

OFF = {"motors_enabled": False}
TOMORROW = (date.today() + timedelta(days=1)).isoformat()
YESTERDAY = (date.today() - timedelta(days=1)).isoformat()
NEXT_WEEK = (date.today() + timedelta(days=7)).isoformat()
URL = "tcp://10.0.0.135:9559"


def record(name="Lukas", delete_after=NEXT_WEEK, form="F-1"):
    return ConsentRecord(name, form, date.today().isoformat(), delete_after)


@pytest.fixture
def registry(tmp_path):
    return ConsentRegistry(tmp_path / "dir" / "consent.json")


# -- registry --------------------------------------------------------------

def test_missing_file_means_no_records(registry):
    assert registry.load() == []


def test_round_trip_keeps_only_the_four_fields(registry):
    registry.add(record())
    data = json.loads(registry.path.read_text())
    assert data == [{"name": "Lukas", "consent_form_id": "F-1", "enrolled_at": date.today().isoformat(),
                     "delete_after": NEXT_WEEK}]
    assert registry.load() == [record()]


def test_file_mode_is_owner_only(registry):
    registry.add(record())
    assert stat.S_IMODE(registry.path.stat().st_mode) == 0o600


def test_interrupted_write_keeps_the_previous_content(registry, monkeypatch):
    registry.add(record())
    before = registry.path.read_text()

    def fail(*args):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", fail)
    with pytest.raises(OSError):
        registry.add(record("Sam"))
    assert registry.path.read_text() == before
    assert [p.name for p in registry.path.parent.iterdir()] == ["consent.json"]


@pytest.mark.parametrize("content", [
    "{not json", '{"a": 1}', '[{"name": "x"}]', '[1]',
    '[{"name": "x", "consent_form_id": "1", "enrolled_at": "2026-01-01", "delete_after": "someday"}]',
    '[{"name": "x", "consent_form_id": "1", "enrolled_at": "2026-01-01", "delete_after": "2026-01-02", "age": "9"}]',
])
def test_damaged_file_raises_and_is_never_overwritten(registry, content):
    registry.path.parent.mkdir(parents=True)
    registry.path.write_text(content)
    with pytest.raises(RegistryDamaged):
        registry.load()
    for write in (lambda: registry.add(record()), lambda: registry.remove("x"), registry.clear):
        with pytest.raises(RegistryDamaged):
            write()
    assert registry.path.read_text() == content


def test_damage_message_contains_no_record_values(registry):
    registry.path.parent.mkdir(parents=True)
    registry.path.write_text('["Secretname"]')
    with pytest.raises(RegistryDamaged) as err:
        registry.load()
    assert "Secretname" not in str(err.value)


def test_overdue(registry):
    registry.add(record("Old", YESTERDAY))
    registry.add(record("Today", date.today().isoformat()))
    registry.add(record("Later", NEXT_WEEK))
    assert [r.name for r in registry.overdue()] == ["Old"]


# -- harness ---------------------------------------------------------------

class World:
    def __init__(self, robot, registry, answers=(), config=None, conversation=False):
        self.robot = robot
        self.registry = registry
        self.answers = list(answers)
        self.asked = []
        self.lines = []
        self.config = config or make_config(motion=OFF, robot={"url": URL, "language": "English"})
        self.bridge = Bridge(self.config, robot)
        self.bridge.startup(greet=False)
        loop = ConversationLoop(self.bridge, ScriptedClient([["Hi."]])) if conversation else None

        async def confirm(question):
            self.asked.append(question)
            return self.answers.pop(0) if self.answers else ""

        self.commands = Commands(self.bridge, "admin", self.lines.append, confirm, loop)
        self.faces = Faces(self.bridge, registry, self.commands)
        self.commands.faces = self.faces
        robot.clear_actions()

    def run(self, *lines):
        async def go():
            for line in lines:
                await self.commands.execute(line)
        asyncio.run(go())
        return self.lines

    def text(self):
        return "\n".join(self.lines)

    def says(self):
        return [a.args["text"] for a in self.robot.actions if a.name == "say"]


@pytest.fixture
def world(off_robot, registry):
    def factory(answers=(), **kwargs):
        return World(off_robot, registry, answers, **kwargs)
    return factory


ENROL = ["F-7", "", ""]  # form ID, default date, head positioned


# -- enrolment: consent and validation -------------------------------------

def test_success_writes_the_record_and_says_the_name_once(world, off_robot, registry):
    w = world(ENROL)
    w.run("faces enroll Lukas")
    assert off_robot.faces == ["Lukas"]
    saved = registry.load()[0]
    assert (saved.name, saved.consent_form_id, saved.delete_after) == ("Lukas", "F-7", TOMORROW)
    assert w.says() == ["Please look at me.", "Hello, Lukas."]
    assert sum("Lukas" in s for s in w.says()) == 1


def test_green_eyes_and_restored_after(world, off_robot):
    w = world(ENROL)
    w.run("faces enroll Lukas")
    colours = [a.args["rgb"] for a in off_robot.actions if a.name == "set_eyes"]
    assert colours[0] == "#00FF00" and colours[-1] == "#FFFFFF"


def test_operator_positions_the_head_by_hand(world, off_robot):
    w = world(ENROL)
    w.run("faces enroll Lukas")
    assert any("position the robot's head by hand" in q for q in w.asked)
    assert "set_tracking" not in off_robot.action_names()


def test_motors_on_turns_tracking_on_with_no_hand_prompt(robot, registry):
    w = World(robot, registry, ["F-7", ""], config=make_config())
    w.run("faces enroll Lukas")
    assert not any("by hand" in q for q in w.asked)
    assert any(a.name == "set_tracking" and a.args["enabled"] for a in robot.actions)


def test_consent_comes_before_anything_on_the_robot(world, off_robot, registry):
    w = world([""])
    w.run("faces enroll Lukas")
    assert "aborted" in w.text()
    assert w.says() == [] and "learn_face" not in off_robot.action_names()
    assert "set_eyes" not in off_robot.action_names()
    assert not registry.path.exists()


def test_past_date_asks_again(world, registry):
    w = world(["F-7", YESTERDAY, "not a date", NEXT_WEEK, ""])
    w.run("faces enroll Lukas")
    assert "in the past" in w.text() and "not a date" in w.text()
    assert registry.load()[0].delete_after == NEXT_WEEK
    assert len([q for q in w.asked if "Deletion date" in q]) == 3


@pytest.mark.parametrize("name,ok", [("Lukas", True), ("Anna-Maria", True), ("Max 2", True), ("Jürgen", True),
                                     ("", False), (" ", False), ("A" * 33, False), ("A" * 32, True),
                                     ("rm;x", False), ("a/b", False), (" Lukas", False), ("-", False)])
def test_name_rules(name, ok):
    assert valid_name(name) is ok


def test_bad_name_refused_before_any_question(world):
    w = world()
    w.run("faces enroll 'a/b'")
    assert "refused" in w.text() and w.asked == []


def test_only_form_id_and_date_are_asked(world):
    w = world(ENROL)
    w.run("faces enroll Lukas")
    assert len(w.asked) == 3
    assert not any(word in " ".join(w.asked).lower() for word in ("age", "birth", "address", "school"))


def test_duplicate_is_refused_and_names_delete(world, registry, off_robot):
    registry.add(record())
    w = world()
    w.run("faces enroll Lukas")
    assert "refused" in w.text() and "faces delete" in w.text()
    assert "learn_face" not in off_robot.action_names()


def test_face_without_a_record_is_refused(world, off_robot):
    off_robot.faces = ["Lukas"]
    w = world()
    w.run("faces enroll Lukas")
    assert "faces delete" in w.text() and w.asked == []


def test_privacy_switch_refuses(world, off_robot):
    w = world(config=make_config(motion=OFF, privacy={"face_recognition_enabled": False}))
    w.run("faces enroll Lukas")
    assert "face_recognition_enabled" in w.text() and "learn_face" not in off_robot.action_names()


@pytest.mark.parametrize("state", [State.THINKING, State.STOPPED])
def test_only_in_idle(world, off_robot, state):
    w = world()
    if state == State.STOPPED:
        w.bridge.estop("command")
    else:
        w.bridge.state.transition(state)
    w.run("faces enroll Lukas")
    assert "needs IDLE" in w.text() and "learn_face" not in off_robot.action_names()


def test_unavailable_perception_refuses(world, off_robot):
    off_robot.available = False
    w = world()
    w.run("faces enroll Lukas")
    assert "not available" in w.text()


def test_damaged_registry_refuses_enrolment(world, registry, off_robot):
    registry.path.parent.mkdir(parents=True)
    registry.path.write_text("{broken")
    w = world()
    w.run("faces enroll Lukas")
    assert "damaged" in w.text() and "learn_face" not in off_robot.action_names()
    assert registry.path.read_text() == "{broken"


# -- enrolment: tries ------------------------------------------------------

def test_learned_on_second_try(world, off_robot, registry):
    off_robot.learn_results = [False, True]
    w = world(ENROL)
    w.run("faces enroll Lukas")
    assert w.says() == ["Please look at me.", "Please look straight at me and hold still.", "Hello, Lukas."]
    assert registry.load()[0].name == "Lukas"


def test_all_tries_fail(world, off_robot, registry):
    off_robot.learn_results = [False, False, False]
    w = world(ENROL)
    w.run("faces enroll Lukas")
    assert off_robot.action_names().count("learn_face") == 3
    assert not registry.path.exists() and off_robot.faces == []
    assert "nothing stored" in w.text()
    assert off_robot.eyes == "#FFFFFF"


def test_a_raising_try_counts_as_failed(world, off_robot, registry):
    off_robot.learn_results = [RuntimeError("no face"), True]
    w = world(ENROL)
    w.run("faces enroll Lukas")
    assert registry.load()[0].name == "Lukas"


def test_all_tries_fail_with_a_face_left_behind_forgets_it(world, off_robot, registry):
    off_robot.learn_results = [False, False, False]
    real = off_robot.learn_face

    def leaves_face(name):
        result = real(name)
        if name not in off_robot.faces:
            off_robot.faces.append(name)
        return result

    off_robot.learn_face = leaves_face
    w = world(ENROL)
    w.run("faces enroll Lukas")
    assert off_robot.faces == []


def test_record_write_failure_forgets_the_face(world, off_robot, registry, monkeypatch):
    w = world(ENROL)

    def fail(*args):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", fail)
    w.run("faces enroll Lukas")
    assert off_robot.faces == [] and not registry.path.exists()
    assert "could not be written" in w.text() and "forgotten" in w.text()
    assert "Hello, Lukas." not in w.says()


# -- enrolment: emergency stop ---------------------------------------------

def stop_during_learning(world_obj, robot, finish_after_stop=True):
    """Runs an enrolment whose learn_face blocks until the emergency stop has fired."""
    started = threading.Event()
    release = threading.Event()
    real = robot.learn_face

    def blocking(name):
        started.set()
        release.wait(5)
        return real(name)

    robot.learn_face = blocking

    async def go():
        task = asyncio.ensure_future(world_obj.commands.execute("faces enroll Lukas"))
        while not started.is_set():
            await asyncio.sleep(0.005)
        robot.inject(EventKind.REAR_HEAD)
        await asyncio.sleep(0.01)
        release.set()
        await task
    asyncio.run(go())


def test_stop_during_a_try_stores_nothing(world, off_robot, registry):
    w = world(ENROL)
    stop_during_learning(w, off_robot)
    assert w.bridge.state.state == State.STOPPED
    assert off_robot.faces == []  # the try finished, then the face was forgotten
    assert "forget_face" in off_robot.action_names()
    assert not registry.path.exists()
    assert "nothing stored" in w.text()
    assert off_robot.eyes == "#FF0000"
    assert w.bridge.exclusive is None and w.bridge._turn_canceller is None


def test_interactive_console_stays_available_while_learning(world, off_robot, registry):
    """The prompt, and Ctrl+E with it, must not wait for learn_face."""
    w = world(ENROL)
    started = threading.Event()
    release = threading.Event()
    real = off_robot.learn_face

    def blocking(name):
        started.set()
        release.wait(5)
        return real(name)

    off_robot.learn_face = blocking
    jobs = []

    async def go():
        w.commands.spawn = lambda job: jobs.append(asyncio.ensure_future(job))
        await asyncio.wait_for(w.commands.execute("faces enroll Lukas"), 2)  # returns while learning
        while not started.is_set():
            await asyncio.sleep(0.005)
        assert w.bridge.exclusive == "enrolment"
        assert not jobs[0].done()
        w.bridge.estop("hotkey")
        await asyncio.sleep(0.01)
        release.set()
        await asyncio.wait_for(jobs[0], 5)

    asyncio.run(go())
    assert w.bridge.state.state == State.STOPPED and w.bridge.exclusive is None
    assert off_robot.faces == [] and not registry.path.exists()


def test_faces_runs_inline_so_it_can_ask():
    from naollm.admin.cli import INLINE
    assert "faces" in INLINE and "resume" in INLINE


def test_state_change_between_steps_aborts(world, off_robot, registry):
    w = world(["F-7", ""])
    original = w.commands.confirm

    async def stop_at_head_prompt(question):
        if "by hand" in question:
            w.bridge.estop("command")
            return ""
        return await original(question)

    w.commands.confirm = stop_at_head_prompt
    w.run("faces enroll Lukas")
    assert "learn_face" not in off_robot.action_names()
    assert not registry.path.exists()
    assert "nothing stored" in w.text()


def test_stop_while_answering_consent_aborts(world, off_robot, registry):
    w = world()

    async def stop_then_answer(question):
        if "Deletion" in question:
            w.bridge.estop("command")
        return "F-7" if "Consent" in question else ""

    w.commands.confirm = stop_then_answer
    w.run("faces enroll Lukas")
    assert "aborted" in w.text() and off_robot.action_names().count("learn_face") == 0


def test_no_turn_can_start_during_enrolment(world, off_robot):
    w = world(ENROL, conversation=True)
    seen = {}
    real = off_robot.learn_face

    def check(name):
        seen["exclusive"] = w.bridge.exclusive
        return real(name)

    off_robot.learn_face = check
    w.run("faces enroll Lukas")
    assert seen["exclusive"] == "enrolment"
    assert w.bridge.exclusive is None


def test_turn_is_refused_while_exclusive(world):
    w = world(conversation=True)
    w.bridge.exclusive = "enrolment"
    w.run("chat hello")
    assert "enrolment is running" in w.text()


def test_wake_word_stays_paused_during_enrolment(off_robot, registry):
    from naollm.audio.listener import Listener

    config = make_config(motion=OFF, robot={"url": URL, "language": "English"})
    bridge = Bridge(config, off_robot)
    client = ScriptedClient([["Hi."]])
    conversation = ConversationLoop(bridge, client)

    class STT:
        def ensure_loaded(self):
            pass

    listener = Listener(bridge, conversation, STT(), speech_test=lambda f: True)
    listener.wake_resume_s = 0.01
    answers = list(ENROL)

    async def confirm(question):
        return answers.pop(0) if answers else ""

    lines = []
    commands = Commands(bridge, "run", lines.append, confirm, conversation, listener=listener)
    faces = Faces(bridge, registry, commands, listener)
    commands.faces = faces
    seen = {}
    real = off_robot.learn_face

    def check(name):
        seen["wake"] = off_robot.wake_enabled
        seen["trigger"] = listener._ignore_reason("touch")
        return real(name)

    off_robot.learn_face = check

    async def go():
        bridge.startup(greet=False)
        await listener.prepare()
        assert off_robot.wake_enabled
        await commands.execute("faces enroll Lukas")
        end = time.monotonic() + 2
        while not off_robot.wake_enabled and time.monotonic() < end:
            await asyncio.sleep(0.005)

    asyncio.run(go())
    assert seen["wake"] is False
    assert seen["trigger"] == "enrolment running"
    assert off_robot.wake_enabled


# -- listing, deleting, mismatches -----------------------------------------

def test_list_shows_records(world, registry, off_robot):
    registry.add(record())
    off_robot.faces = ["Lukas"]
    w = world()
    w.run("faces list")
    assert "Lukas" in w.text() and "F-1" in w.text() and NEXT_WEEK in w.text()
    assert "NO " not in w.text()


def test_list_reports_a_face_without_consent(world, off_robot):
    off_robot.faces = ["Alex"]
    w = world()
    w.run("faces list")
    assert "Alex  NO CONSENT RECORD" in w.text()
    w.run("faces delete Alex")
    assert off_robot.faces == []


def test_list_reports_a_record_without_a_face(world, registry):
    registry.add(record())
    w = world()
    w.run("faces list")
    assert "NO FACE ON ROBOT" in w.text()
    w.run("faces delete Lukas")
    assert registry.load() == []


def test_list_with_nothing(world):
    w = world()
    w.run("faces list")
    assert "no faces enrolled" in w.text()


def test_list_when_the_robot_cannot_answer(world, registry, off_robot):
    registry.add(record())
    w = world()
    off_robot.fail_on["learned_names"] = RuntimeError("offline")
    w.run("faces list")
    assert "cannot read" in w.text() and "Lukas" in w.text()


def test_delete_removes_face_and_record(world, registry, off_robot):
    registry.add(record())
    off_robot.faces = ["Lukas"]
    w = world()
    w.run("faces delete Lukas")
    assert off_robot.faces == [] and registry.load() == []


@pytest.mark.parametrize("failure", ["raise", "false"])
def test_robot_failure_keeps_the_record(world, registry, off_robot, failure):
    registry.add(record())
    off_robot.faces = ["Lukas"]
    w = world()
    if failure == "raise":
        off_robot.fail_on["forget_face"] = RuntimeError("unreachable")
    else:
        off_robot.forget_face = lambda name: False
    w.run("faces delete Lukas")
    assert registry.find("Lukas") is not None
    assert "record stays" in w.text()
    off_robot.fail_on.clear()
    if failure == "false":
        del off_robot.forget_face
    w.run("faces delete Lukas")
    assert registry.load() == []


def test_delete_unknown_name(world):
    w = world()
    w.run("faces delete Nobody")
    assert "neither" in w.text()


@pytest.mark.parametrize("answer", ["delete", "", "y", "DELETE "])
def test_delete_all_needs_the_exact_word(world, registry, off_robot, answer):
    registry.add(record())
    off_robot.faces = ["Lukas"]
    w = world([answer])
    w.run("faces delete --all")
    assert off_robot.faces == ["Lukas"] and registry.find("Lukas") is not None
    assert "nothing deleted" in w.text()


def test_delete_all(world, registry, off_robot):
    registry.add(record())
    off_robot.faces = ["Lukas", "Alex"]
    w = world(["DELETE"])
    w.run("faces delete --all")
    assert off_robot.faces == [] and registry.load() == []


def test_delete_all_robot_failure_keeps_records(world, registry, off_robot):
    registry.add(record())
    off_robot.faces = ["Lukas"]
    off_robot.fail_on["forget_all_faces"] = RuntimeError("unreachable")
    w = world(["DELETE"])
    w.run("faces delete --all")
    assert registry.find("Lukas") is not None and "records stay" in w.text()


def test_delete_refused_with_a_damaged_registry(world, registry, off_robot):
    registry.path.parent.mkdir(parents=True)
    registry.path.write_text("{broken")
    off_robot.faces = ["Lukas"]
    w = world()
    w.run("faces delete Lukas", "faces delete --all")
    assert off_robot.faces == ["Lukas"] and registry.path.read_text() == "{broken"


def test_usage(world):
    w = world()
    w.run("faces", "faces enroll", "faces wipe x")
    assert all("usage" in line for line in w.lines)


# -- overdue at startup ----------------------------------------------------

def run_check(w):
    asyncio.run(w.faces.startup_check())


def test_overdue_declined_keeps_everything_and_warns_again(world, registry, off_robot):
    registry.add(record("Old", YESTERDAY))
    off_robot.faces = ["Old"]
    w = world(["n"])
    run_check(w)
    assert off_robot.faces == ["Old"] and registry.find("Old") is not None
    assert "Old" in w.text()
    w2 = World(off_robot, registry, ["n"])
    run_check(w2)
    assert any("past their deletion date" in e.message for e in w2.bridge.log.entries("alert"))


def test_overdue_accepted_deletes_both(world, registry, off_robot):
    registry.add(record("Old", YESTERDAY))
    registry.add(record("Later", NEXT_WEEK))
    off_robot.faces = ["Old", "Later"]
    w = world(["y"])
    run_check(w)
    assert off_robot.faces == ["Later"]
    assert [r.name for r in registry.load()] == ["Later"]


def test_overdue_robot_failure_keeps_the_record(world, registry, off_robot):
    registry.add(record("Old", YESTERDAY))
    off_robot.faces = ["Old"]
    off_robot.fail_on["forget_face"] = RuntimeError("offline")
    w = world(["y"])
    run_check(w)
    assert registry.find("Old") is not None


def test_nothing_overdue_asks_nothing(world, registry):
    registry.add(record("Later", NEXT_WEEK))
    w = world()
    run_check(w)
    assert w.asked == []


def test_damaged_file_at_startup_alerts_and_stays(world, registry):
    registry.path.parent.mkdir(parents=True)
    registry.path.write_text("{broken")
    w = world()
    run_check(w)
    assert any("DAMAGED" in e.message for e in w.bridge.log.entries("alert"))
    assert registry.path.read_text() == "{broken"


# -- event mode ------------------------------------------------------------

EVENT = dict(motion=OFF, event_mode=True, robot={"url": URL, "language": "English"})


def test_event_log_has_no_names_or_form_ids(world, registry, off_robot, monkeypatch):
    w = world(config=make_config(**EVENT))
    w.answers = ["FORM-SECRET", "", ""]
    w.run("faces enroll Secretname", "faces list", "faces delete Secretname")
    off_robot.faces = ["Secretname"]
    off_robot.learn_results = [False, False, False]
    w.answers = ["FORM-SECRET", "", ""]
    w.run("faces list", "faces delete Secretname")
    w.answers = ["FORM-SECRET", "", ""]
    off_robot.fail_on["forget_face"] = RuntimeError("Secretname")
    off_robot.faces = ["Secretname"]
    w.run("faces delete Secretname")
    off_robot.fail_on.clear()
    w.answers = ["DELETE"]
    w.run("faces delete --all")
    # refusals: a duplicate name and a bad one
    registry.add(record("Secretname", form="FORM-SECRET"))
    w.run("faces enroll Secretname", "faces enroll 'Secret/name'")
    registry.remove("Secretname")
    # a failed record write
    w.answers = ["FORM-SECRET", "", ""]
    monkeypatch.setattr(os, "replace", lambda *a: (_ for _ in ()).throw(OSError("Secretname")))
    w.run("faces enroll Secretname")
    rendered = "\n".join(e.render() for e in w.bridge.log.entries())
    assert "Secret" not in rendered and "FORM" not in rendered
    assert any("enrolled" in e.message for e in w.bridge.log.entries("admin"))
    assert any("deleted" in e.message for e in w.bridge.log.entries("admin"))


def test_outside_event_mode_the_name_is_logged(world, off_robot):
    w = world(ENROL)
    w.run("faces enroll Lukas")
    assert any(e.text == "Lukas" for e in w.bridge.log.entries("admin"))


# -- who_is_here -----------------------------------------------------------

def test_who_is_here_offered_when_available(off_bridge):
    assert "who_is_here" in {s["function"]["name"] for s in off_bridge.registry.schemas(False)}
    assert off_bridge.gate.check_and_run("who_is_here", {}).ok


def test_who_is_here_with_recognition_off(off_robot, make_bridge):
    b = make_bridge(make_config(motion=OFF, privacy={"face_recognition_enabled": False}))
    off_robot.people = 2
    off_robot.seen_faces = ["Alex"]
    assert b.gate.check_and_run("who_is_here", {}, lang="en").result == "2 people"
    assert off_robot.recognition is False


def build_real(config_overrides=None):
    double = QiDouble()
    robot = QiRobot(qi_module=double)
    bridge = Bridge(make_config(motion=OFF, **(config_overrides or {})), robot)
    bridge.startup(greet=False)
    return double, robot, bridge


FACE = [[0, 0], [[[0, 0.1, 0.1, 0.2, 0.2], [1, 0.7, "Alex", [], [], [], [], []]], [2, ["Alex"]]], [], [], [], 0]


def test_who_is_here_on_the_real_adapter(tmp_path):
    double, robot, bridge = build_real()
    double.visible_people = [1, 2]
    double.fire("FaceDetected", FACE)
    result = bridge.gate.check_and_run("who_is_here", {}, lang="en", source="admin")
    assert result.result == "2 people; known club members: Alex"
    bridge.shutdown()
    assert double.violations == [] and not double.is_open


def test_who_is_here_event_mode_log_has_no_name():
    double, robot, bridge = build_real({"event_mode": True})
    double.fire("FaceDetected", FACE)
    assert "Alex" in bridge.gate.check_and_run("who_is_here", {}, lang="en").result
    assert not any("Alex" in e.render() for e in bridge.log.entries())
    bridge.shutdown()


def test_recognition_off_on_the_real_adapter():
    double, robot, bridge = build_real({"privacy": {"face_recognition_enabled": False}})
    assert double.called("ALFaceDetection", "subscribe") == []
    double.visible_people = [1]
    assert bridge.gate.check_and_run("who_is_here", {}, lang="en").result == "1 people"
    bridge.shutdown()


def test_full_enrolment_on_the_real_adapter_commands_no_motor(registry):
    double, robot, bridge = build_real()
    answers = list(ENROL) + ["DELETE"]

    async def confirm(question):
        return answers.pop(0)

    lines = []
    commands = Commands(bridge, "admin", lines.append, confirm)
    commands.faces = Faces(bridge, registry, commands)

    async def go():
        await commands.execute("faces enroll Alex")
        assert double.learned == ["Alex"] and registry.find("Alex")
        await commands.execute("faces list")
        await commands.execute("faces delete Alex")
        assert double.learned == [] and registry.load() == []
        await commands.execute("faces delete --all")

    asyncio.run(go())
    bridge.shutdown()
    assert double.violations == []
    assert not any(line.startswith("error") for line in lines), lines


def test_reconnect_restarts_perception():
    double, robot, bridge = build_real()
    double.drop_connection()
    assert robot.perception.available is False
    bridge.reconnect()
    assert robot.perception.available is True
    assert "who_is_here" in {s["function"]["name"] for s in bridge.registry.schemas(False)}
    bridge.shutdown()


# -- notice ----------------------------------------------------------------

def test_notice_in_english_with_motors_off(world, off_robot):
    w = world(config=make_config(motion=OFF, robot={"url": URL, "language": "English"},
                                 audio={"wake_words": ["Hey NAO"], "wake_language": "English"}))
    w.run("notice")
    text = w.says()[0]
    assert "Hey NAO" in text and "tap the front of my head" in text
    assert "not stored" in text and "locally" in text and "club members who consented" in text
    assert "head to you" not in text and "turn" not in text
    assert not {"go_to_posture", "set_head", "set_tracking", "run_behavior"} & set(off_robot.action_names())


def test_notice_in_german(world):
    w = world(config=make_config(motion=OFF))
    w.run("notice")
    text = w.says()[0]
    assert "Hallo NAO" in text and "nicht gespeichert" in text and "Einwilligung" in text
    assert "drehe" not in text


def test_notice_with_motors_mentions_the_head(robot, registry):
    w = World(robot, registry, config=make_config())
    w.run("notice")
    assert "drehe den Kopf" in w.says()[0]


def test_notice_refused_during_a_turn(world, off_robot):
    w = world()
    w.bridge.state.transition(State.THINKING)
    w.run("notice")
    assert "refused" in w.text() and w.says() == []


def test_notice_refused_when_stopped(world):
    w = world()
    w.bridge.estop("command")
    w.run("notice")
    assert "refused" in w.text()


def test_notice_is_logged_without_its_text(world):
    w = world(config=make_config(motion=OFF, event_mode=True))
    w.run("notice")
    entries = [e for e in w.bridge.log.entries("admin") if "notice" in e.message]
    assert entries and entries[0].text is None
