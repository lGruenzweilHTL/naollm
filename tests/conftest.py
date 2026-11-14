from __future__ import annotations

import socket

import pytest

from naollm.config import Config, parse_config
from naollm.robot.fake_adapter import FakeRobot

# Example names stand for a cloud endpoint. A network with a search domain and
# wildcard DNS resolves any unknown name to a private address, which would make
# them look local, so the tests answer for them with a public address.
PUBLIC_TEST_IP = "93.184.215.14"


@pytest.fixture(autouse=True)
def public_example_dns(monkeypatch):
    real = socket.getaddrinfo

    def getaddrinfo(host, *args, **kwargs):
        if isinstance(host, str) and (host == "example.com" or host.endswith(".example.com")):
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (PUBLIC_TEST_IP, 0))]
        return real(host, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)


FAST = 0.01  # fake robot time scale: a 6 s behavior takes 60 ms

# A valid arms-only animation for tests, so they never depend on the real files in animations/
# or on their review status.
TEST_ANIMATION = """\
name: {name}
description: Test animation, both arms up, a short pump, back to Stand.
version: 1
author: test
reviewed_by: "{reviewed_by}"
posture: Stand
duration_s: 3.0
uses_legs: false
interpolation: bezier
tracks:
  LShoulderPitch: {{times: [0.3, 1.1, 1.6, 2.1, 2.9], angles_deg: [85, -70, -85, -70, 85]}}
  RShoulderPitch: {{times: [0.3, 1.1, 1.6, 2.1, 2.9], angles_deg: [85, -70, -85, -70, 85]}}
  LElbowRoll:     {{times: [0.3, 1.1, 1.6, 2.1, 2.9], angles_deg: [-25, -40, -70, -40, -25]}}
  RElbowRoll:     {{times: [0.3, 1.1, 1.6, 2.1, 2.9], angles_deg: [25, 40, 70, 40, 25]}}
  HeadPitch:      {{times: [0.3, 1.1, 2.1, 2.9],      angles_deg: [0, -15, -15, 0]}}
"""


def animation_text(name: str = "cheer", reviewed_by: str = "") -> str:
    return TEST_ANIMATION.format(name=name, reviewed_by=reviewed_by)


def write_animation(directory, name: str = "cheer", reviewed_by: str = ""):
    """Writes the test animation as `<directory>/<name>.anim.yaml` and returns the path."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{name}.anim.yaml"
    path.write_text(animation_text(name, reviewed_by))
    return path


def make_config(**overrides) -> Config:
    data = {
        "robot": {"url": "tcp://10.0.0.2:9559"},
        "dances": {"macarena": "showcase/dance_macarena", "robot": "showcase/dance_robot"},
        "gestures": {
            "wave": "animations/Stand/Gestures/Hey_1",
            "bow": "showcase/gesture_bow",
            "nod": "animations/Stand/Gestures/Yes_1",
        },
        "event_mode": False,
    }
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(data.get(key), dict) and key not in ("dances", "gestures"):
            data[key] = {**data[key], **value}
        else:
            data[key] = value
    return parse_config(data)


def installed_for(config: Config) -> list[str]:
    targets = list(config.dances.values()) + list(config.gestures.values())
    return [t for t in targets if not t.startswith("anim:")]


@pytest.fixture
def config() -> Config:
    return make_config()


@pytest.fixture
def robot(config) -> FakeRobot:
    r = FakeRobot(installed_behaviors=installed_for(config), time_scale=FAST)
    r.connect(config.robot.url)
    r.posture = "Stand"
    r.stiff = True
    r.clear_actions()
    return r


@pytest.fixture
def make_bridge(robot):
    from naollm.bridge import Bridge

    def factory(config=None, keyframes=None, problems=None):
        cfg = config or make_config()
        loader = (lambda: (keyframes or {}, problems or {})) if (keyframes or problems) else None
        b = Bridge(cfg, robot, keyframe_loader=loader)
        b.look_hold_s = 0.05
        b.eyes_hold_s = 0.05
        b.startup(greet=False)
        robot.clear_actions()
        return b

    return factory


@pytest.fixture
def bridge(make_bridge):
    return make_bridge()


@pytest.fixture
def off_robot(robot) -> FakeRobot:
    """A fake robot at rest, as motors-off startup requires."""
    robot.stiff = False
    return robot


@pytest.fixture
def off_bridge(off_robot, make_bridge):
    return make_bridge(make_config(motion={"motors_enabled": False}))


@pytest.fixture(autouse=True)
def eval_results_in_tmp(tmp_path, monkeypatch):
    """No test writes to the operator's ~/.naollm/eval."""
    import naollm.eval

    monkeypatch.setattr(naollm.eval, "RESULTS_DIR", tmp_path / "eval-results")
