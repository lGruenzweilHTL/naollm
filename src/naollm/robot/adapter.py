"""Robot access contract (SPEC §6.1).

Every transport (pip `qi`, Python 2 shim, ROS 2) implements the full set of
sub-interfaces. Nothing outside `naollm.robot` talks to a transport directly.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Dict, List, Literal, Optional, Protocol, Tuple, runtime_checkable

Outcome = Literal["finished", "interrupted"]
ThermalLevel = Literal["ok", "warm", "hot"]

# Fixed speeds. Never exposed to the LLM.
POSTURE_SPEED = 0.6
HEAD_SPEED = 0.15


@dataclass(frozen=True)
class RobotStatus:
    battery_pct: int
    posture: str
    fallen: bool
    thermal: ThermalLevel
    moving: bool
    connected: bool = True


@dataclass(frozen=True)
class RobotInfo:
    naoqi_version: str
    robot_id: str
    name: str


class EventKind(str, Enum):
    FRONT_HEAD = "front_head"
    MIDDLE_HEAD = "middle_head"
    REAR_HEAD = "rear_head"
    FALL = "fall"
    BATTERY = "battery"
    THERMAL = "thermal"
    CONNECTION_LOST = "connection_lost"
    CONNECTION_RESTORED = "connection_restored"
    WAKE_WORD = "wake_word"
    WAKE_CANDIDATE = "wake_candidate"


@dataclass(frozen=True)
class RobotEvent:
    kind: EventKind
    value: Any = None


@dataclass(frozen=True)
class WakeHit:
    """Value of WAKE_WORD and WAKE_CANDIDATE events."""

    phrase: str
    confidence: float


class MotorsDisabled(RuntimeError):
    """A motion command reached an adapter that has no motor code."""


class RobotOffline(RuntimeError):
    """There is no session to the robot."""


@dataclass(frozen=True)
class Keyframes:
    """Joint trajectories ready for playback: angles in radians (hands: 0..1)."""

    names: List[str]
    times: List[List[float]]
    angles: List[List[float]]
    bezier: bool = True
    duration_s: float = 0.0


@runtime_checkable
class Motion(Protocol):
    def run_behavior(self, path: str) -> Outcome: ...
    def stop_all_behaviors(self) -> None: ...
    def installed_behaviors(self) -> List[str]: ...
    def play_animation(self, path: str) -> Outcome: ...
    def play_keyframes(self, frames: Keyframes) -> Outcome: ...
    def set_balancer(self, enabled: bool) -> None: ...
    def go_to_posture(self, name: str, speed: float = POSTURE_SPEED) -> bool: ...
    def move_to(self, x_m: float, y_m: float, theta_rad: float) -> Outcome: ...
    def stop_move(self) -> None: ...
    def set_head(self, yaw_rad: float, pitch_rad: float, speed: float = HEAD_SPEED) -> None: ...
    def joint_limits(self) -> Dict[str, Tuple[float, float, float]]: ...
    def rest(self) -> None: ...
    def wake_up(self) -> None: ...


@runtime_checkable
class Speech(Protocol):
    def say(self, text: str, animated: bool = True) -> None: ...
    def stop_speech(self) -> None: ...
    def set_language(self, language: str) -> None: ...
    def installed_languages(self) -> List[str]: ...
    def set_eyes(self, rgb_hex: str, duration_s: float = 0.2) -> None: ...


@runtime_checkable
class Audio(Protocol):
    def start_stream(self, on_chunk: Callable[[bytes], None]) -> None: ...
    def stop_stream(self) -> None: ...
    def configure_wake_words(self, words: List[str], threshold: float, language: str) -> None: ...
    def set_wake_enabled(self, enabled: bool) -> None: ...
    def wake_languages(self) -> List[str]: ...
    def play_listen_sound(self) -> None: ...


@runtime_checkable
class Events(Protocol):
    def subscribe(self, handler: Callable[[RobotEvent], None]) -> None: ...
    def unsubscribe_all(self) -> None: ...
    def status(self) -> RobotStatus: ...


@runtime_checkable
class Perception(Protocol):
    available: bool

    def start(self, recognition: bool) -> None: ...
    def set_tracking(self, enabled: bool) -> None: ...
    def people_count(self) -> int: ...
    def recognized_names(self) -> List[str]: ...
    def learned_names(self) -> List[str]: ...
    def learn_face(self, name: str) -> bool: ...
    def forget_face(self, name: str) -> bool: ...
    def forget_all_faces(self) -> bool: ...


@runtime_checkable
class RobotAdapter(Protocol):
    motion: Motion
    speech: Speech
    audio: Audio
    events: Events
    perception: Perception

    def connect(self, url: str) -> None: ...
    def disconnect(self) -> None: ...
    def info(self) -> RobotInfo: ...
    def rest_problem(self, check_life: bool = True) -> Optional[str]: ...
    def autonomous_life(self) -> str: ...
    def disable_autonomous_life(self) -> None: ...
