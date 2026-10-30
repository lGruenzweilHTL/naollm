from __future__ import annotations

import pytest

from naollm.config import Config, parse_config
from naollm.robot.fake_adapter import FakeRobot

FAST = 0.01  # fake robot time scale: a 6 s behavior takes 60 ms


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
