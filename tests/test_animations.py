from pathlib import Path

import pytest

from conftest import make_config
from naollm.animations.import_choregraphe import ChoregrapheImportError, convert, import_file
from naollm.animations.library import load_library, playable
from naollm.animations.limits import builtin_limits, load_limits
from naollm.animations.model import Animation, AnimationError, load_animation
from naollm.animations.validate import validate
from naollm.main import main
from naollm.state import State

ROOT = Path(__file__).resolve().parents[1]

SPEC_EXAMPLE = """\
name: victory
description: Both arms up, short fist pump.
version: 1
author: human
reviewed_by: ""
posture: Stand
duration_s: 3.2
uses_legs: false
interpolation: bezier
tracks:
  LShoulderPitch: {times: [0.6, 1.4, 2.4, 3.2], angles_deg: [-60, -80, -60, 85]}
  RShoulderPitch: {times: [0.6, 1.4, 2.4, 3.2], angles_deg: [-60, -80, -60, 85]}
  LElbowRoll:     {times: [0.6, 1.4, 3.2],      angles_deg: [-30, -60, -25]}
  RElbowRoll:     {times: [0.6, 1.4, 3.2],      angles_deg: [30, 60, 25]}
  HeadPitch:      {times: [1.4, 3.2],           angles_deg: [-15, 0]}
"""


def write(tmp_path, name, text):
    p = tmp_path / f"{name}.anim.yaml"
    p.write_text(text)
    return p


def anim(**over):
    data = {
        "name": "test", "duration_s": 2.0, "posture": "Stand",
        "tracks": {"HeadYaw": {"times": [0.5, 2.0], "angles_deg": [5, 0]}},
    }
    data.update(over)
    return Animation.model_validate(data)


# -- format ------------------------------------------------------------------

def test_load_spec_example(tmp_path):
    a = load_animation(write(tmp_path, "victory", SPEC_EXAMPLE))
    assert len(a.tracks) == 5
    assert a.tracks["HeadPitch"].angles_deg == [-15, 0]
    assert a.angles_rad("HeadPitch")[0] == pytest.approx(-0.2618, abs=1e-4)


def test_name_mismatch(tmp_path):
    with pytest.raises(AnimationError, match="victory.*wave2"):
        load_animation(write(tmp_path, "wave2", SPEC_EXAMPLE))


def test_duplicate_track_rejected(tmp_path):
    text = SPEC_EXAMPLE + "  HeadPitch:      {times: [1.0], angles_deg: [0]}\n"
    with pytest.raises(AnimationError, match="duplicate"):
        load_animation(write(tmp_path, "victory", text))


# -- validator -----------------------------------------------------------------

def rules(violations):
    return {(v.rule, v.joint) for v in violations}


def test_angle_out_of_limits():
    v = validate(anim(tracks={"HeadYaw": {"times": [0.5, 1.5, 2.0], "angles_deg": [0, 130, 0]}}), builtin_limits())
    hit = [x for x in v if x.rule == "limit"]
    assert hit and hit[0].joint == "HeadYaw" and hit[0].time == 1.5


def test_margin_applies():
    # HeadYaw limit 119.5, margin 5 -> 117 is outside
    v = validate(anim(tracks={"HeadYaw": {"times": [0.5, 2.0, 2.5], "angles_deg": [0, 117, 0]}},
                      duration_s=3.0), builtin_limits())
    assert ("limit", "HeadYaw") in rules(v)


def test_leg_joint_without_uses_legs():
    v = validate(anim(tracks={"LKneePitch": {"times": [1.0], "angles_deg": [40]}}), builtin_limits())
    assert ("legs", "LKneePitch") in rules(v)


def test_uses_legs_needs_review():
    v = validate(anim(uses_legs=True, tracks={"LKneePitch": {"times": [1.0], "angles_deg": [40]}}),
                 builtin_limits())
    assert ("review", None) in rules(v)


def test_too_fast():
    v = validate(anim(tracks={"LShoulderPitch": {"times": [0.5, 0.6, 2.0], "angles_deg": [85, -15, 85]}}),
                 builtin_limits())
    speed = [x for x in v if x.rule == "speed"]
    assert speed and speed[0].joint == "LShoulderPitch" and speed[0].time == 0.6


def test_times_must_increase_and_end_in_posture():
    v = validate(anim(tracks={"HeadPitch": {"times": [1.0, 0.8], "angles_deg": [0, 25]}}), builtin_limits())
    assert ("time", "HeadPitch") in rules(v)
    assert ("posture", "HeadPitch") in rules(v)


def test_too_long():
    v = validate(anim(duration_s=31, tracks={"HeadYaw": {"times": [1.0, 31.0], "angles_deg": [0, 0]}}),
                 builtin_limits())
    assert any(x.rule == "time" and "30" in x.message for x in v)


def test_unknown_joint_and_shape():
    v = validate(anim(tracks={"Tail": {"times": [1.0], "angles_deg": [0]},
                              "HeadYaw": {"times": [1.0, 2.0], "angles_deg": [0]}}), builtin_limits())
    assert ("joint", "Tail") in rules(v) and ("shape", "HeadYaw") in rules(v)


def test_valid_arm_animation():
    a = load_animation(ROOT / "animations" / "victory.anim.yaml")
    assert validate(a, builtin_limits()) == []


def test_limits_source(tmp_path):
    assert "built-in" in load_limits(None).source
    profile = tmp_path / "nao.yaml"
    profile.write_text("joint_limits: {HeadYaw: {min_deg: -10, max_deg: 10, max_velocity_rad_s: 1}}\n")
    limits = load_limits(profile)
    assert "robot profile" in limits.source
    v = validate(anim(tracks={"HeadYaw": {"times": [0.5, 1.0, 2.0], "angles_deg": [0, 8, 0]}}), limits)
    assert ("limit", "HeadYaw") in rules(v)


# -- importer ------------------------------------------------------------------

EXPORT = """\
# Choregraphe bezier export file
names = list()
times = list()
keys = list()

names.append("HeadYaw")
times.append([0.96, 1.96])
keys.append([[0.0872665, [3, -0.333333, 0], [3, 0.333333, 0]], [0, [3, -0.333333, 0], [3, 0, 0]]])

names.append("RShoulderPitch")
times.append([0.96, 1.96])
keys.append([1.48353, 1.39626])

try:
    motion = ALProxy("ALMotion")
    motion.angleInterpolationBezier(names, times, keys)
except BaseException as err:
    print(err)
"""


def test_import_timeline(tmp_path):
    out = import_file_text(tmp_path, EXPORT, "nodding")
    a = load_animation(out)
    assert a.tracks["HeadYaw"].angles_deg == [5.0, 0.0]
    assert a.tracks["RShoulderPitch"].angles_deg == [85.0, 80.0]
    assert a.uses_legs is False and a.reviewed_by == "" and a.author == "human"
    assert a.duration_s == 1.96


def import_file_text(tmp_path, text, name):
    src = tmp_path / "export.py"
    src.write_text(text)
    return import_file(src, name, tmp_path / "anims")


def test_export_is_not_executed(tmp_path):
    marker = tmp_path / "pwned"
    text = EXPORT + f"\nopen({str(marker)!r}, 'w').write('x')\n"
    import_file_text(tmp_path, text, "safe")
    assert not marker.exists()


def test_non_literal_rejected():
    with pytest.raises(ChoregrapheImportError, match="literal"):
        convert('names = list()\nnames.append(__import__("os").getcwd())\n', "x")


def test_leg_joints_set_uses_legs():
    a = convert('names=["LKneePitch"]\ntimes=[[1.0]]\nkeys=[[0.5]]\n', "kick")
    assert a.uses_legs is True


# -- CLI ---------------------------------------------------------------------

def test_validate_exit_code(tmp_path, capsys):
    good = (ROOT / "animations" / "victory.anim.yaml").read_text()
    write(tmp_path, "victory", good)
    write(tmp_path, "victory2", good.replace("name: victory", "name: victory2"))
    write(tmp_path, "broken", good.replace("name: victory", "name: broken").replace("[0, -15, -15, 0]",
                                                                                     "[0, -45, -15, 0]"))
    code = main(["anim", "validate", "--all", "--dir", str(tmp_path)])
    out = capsys.readouterr().out
    assert code == 1
    assert "broken" in out and "HeadPitch" in out
    assert "victory:" not in out and "2 valid, 1 invalid" in out
    assert "built-in NAO 6" in out


def test_anim_list_and_import(tmp_path, capsys):
    src = tmp_path / "export.py"
    src.write_text(EXPORT)
    assert main(["anim", "import", str(src), "--name", "nodding", "--dir", str(tmp_path)]) in (0, 1)
    assert (tmp_path / "nodding.anim.yaml").exists()
    main(["anim", "list", "--dir", str(tmp_path)])
    assert "NOT reviewed" in capsys.readouterr().out


def test_sample_validates():
    assert main(["anim", "validate", "victory", "--dir", str(ROOT / "animations")]) == 0


# -- library filtering and playback -----------------------------------------------

def reviewed_copy(tmp_path):
    text = (ROOT / "animations" / "victory.anim.yaml").read_text().replace('reviewed_by: ""', "reviewed_by: lukas")
    write(tmp_path, "victory", text)
    return tmp_path


def test_unreviewed_dropped_in_event_mode():
    entries = load_library(ROOT / "animations")
    ok, problems = playable(entries, event_mode=True)
    assert "victory" not in ok and "not reviewed" in problems["victory"]
    ok, _ = playable(entries, event_mode=False)
    assert "victory" in ok


def test_unreviewed_warning_names_animation(make_bridge):
    cfg = make_config(event_mode=True, gestures={"wave": "animations/Stand/Gestures/Hey_1",
                                                 "victory": "anim:victory"})
    ok, problems = playable(load_library(ROOT / "animations"), event_mode=True)
    b = make_bridge(cfg, keyframes=ok, problems=problems)
    assert "victory" not in b.registry.catalogue.gestures
    assert any("victory" in e.message and "not reviewed" in e.message for e in b.log.entries("warning"))


def test_upper_body_playback(make_bridge, robot, tmp_path):
    ok, _ = playable(load_library(reviewed_copy(tmp_path)), event_mode=True)
    cfg = make_config(gestures={"victory": "anim:victory"})
    b = make_bridge(cfg, keyframes=ok)
    assert b.gate.check_and_run("play_gesture", {"name": "victory"}).ok
    names = [a.name for a in robot.actions if a.name in ("set_balancer", "play_keyframes")]
    assert names == ["set_balancer", "play_keyframes", "set_balancer"]
    assert robot.balancer is False


def test_interrupted_playback(make_bridge, robot, tmp_path):
    import threading
    import time

    ok, _ = playable(load_library(reviewed_copy(tmp_path)), event_mode=True)
    b = make_bridge(make_config(gestures={"victory": "anim:victory"}), keyframes=ok)
    robot.time_scale = 1.0
    out = {}
    t = threading.Thread(target=lambda: out.setdefault("r", b.gate.check_and_run("play_gesture", {"name": "victory"})))
    t.start()
    while "play_keyframes" not in robot.action_names():
        time.sleep(0.005)
    b.estop("hotkey")
    t.join(3)
    assert out["r"].result == "interrupted"
    assert robot.balancer is False
    assert b.state.state == State.STOPPED
    assert robot.posture == "Crouch"
