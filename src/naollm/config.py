"""Configuration model (SPEC §15) and loading."""

from __future__ import annotations

import ipaddress
import re
import socket
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional
from urllib.parse import urlparse

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator


class ConfigError(Exception):
    """Raised when a configuration file cannot be loaded or is invalid."""


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


Language = Literal["German", "English"]


class RobotConfig(_Strict):
    url: str
    language: Language = "German"
    battery_reliable: bool = True
    listen_ip: Optional[str] = None

    @field_validator("listen_ip")
    @classmethod
    def _check_listen_ip(cls, value: Optional[str]) -> Optional[str]:
        if value is not None:
            try:
                ipaddress.ip_address(value)
            except ValueError:
                raise ValueError(f"{value!r} is not an IP address") from None
        return value


class LLMConfig(_Strict):
    base_url: str = "http://localhost:11434/v1"
    api_key: str = "ollama"
    model: str = "gemma4:e4b"
    temperature: float = Field(0.4, ge=0.0, le=2.0)
    max_tokens: int = Field(200, ge=16, le=4096)
    timeout_s: float = Field(20.0, gt=0)
    max_tool_rounds: int = Field(3, ge=1, le=10)
    # Sent as `reasoning_effort`. "none" turns thinking off, which a small
    # model on a CPU needs: thinking costs seconds and can use up max_tokens.
    # null leaves the field out, for endpoints that do not accept it.
    reasoning_effort: Optional[Literal["none", "low", "medium", "high"]] = "none"


class ConversationConfig(_Strict):
    history_turns: int = Field(6, ge=0, le=50)
    idle_reset_s: float = Field(60.0, gt=0)
    school_name: str = "unserer Schule"


class STTConfig(_Strict):
    engine: Literal["faster-whisper"] = "faster-whisper"
    model: str = "small"
    languages: List[Literal["de", "en"]] = Field(default_factory=lambda: ["de", "en"])
    fallback_language: Literal["de", "en"] = "de"


class AudioConfig(_Strict):
    source: Literal["robot", "robot_recorder_fallback"] = "robot"
    wake_words: List[str] = Field(default_factory=lambda: ["Hallo NAO", "Hey NAO"])
    wake_language: Language = "German"
    wake_threshold: float = Field(0.5, ge=0.0, le=1.0)
    front_head_tap: bool = True
    operator_key: str = "space"
    vad_silence_s: float = Field(0.8, gt=0)
    max_utterance_s: float = Field(15.0, gt=0)
    no_speech_timeout_s: float = Field(5.0, gt=0)


class MotionConfig(_Strict):
    motors_enabled: bool = True
    walking_enabled: bool = False
    max_turn_deg: int = Field(90, ge=1, le=90)
    max_step_cm: int = Field(30, ge=5, le=30)
    max_total_displacement_m: float = Field(1.0, gt=0, le=1.0)
    battery_motion_min_pct: int = Field(20, ge=0, le=100)
    battery_dance_min_pct: int = Field(30, ge=0, le=100)
    idle_crouch_s: float = Field(180.0, gt=0)


class PrivacyConfig(_Strict):
    face_recognition_enabled: bool = True
    age_game_enabled: bool = False
    persist_transcripts: bool = False
    debug_logging: bool = False


# Built-in NAOqi animation paths are verified on the robot in phase 0
# (`behaviors` in the admin console lists what is installed).
DEFAULT_GESTURES: Dict[str, str] = {
    "wave": "animations/Stand/Gestures/Hey_1",
    "bow": "showcase/gesture_bow",
    "nod": "animations/Stand/Gestures/Yes_1",
    "shake_head": "animations/Stand/Gestures/No_3",
    "shrug": "animations/Stand/Gestures/IDontKnow_1",
    "think": "animations/Stand/Gestures/Thinking_1",
    "celebrate": "animations/Stand/Gestures/Enthusiastic_4",
}

NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")
ANIM_REF_RE = re.compile(r"^anim:([a-z][a-z0-9_]*)$")
PATH_RE = re.compile(r"^[A-Za-z0-9_.\-]+(/[A-Za-z0-9_.\-]+)+$")


def _check_catalogue(value: Dict[str, str]) -> Dict[str, str]:
    for name, target in value.items():
        if not NAME_RE.match(name):
            raise ValueError(f"invalid action name {name!r}: use lowercase letters, digits and _")
        if not isinstance(target, str) or not (ANIM_REF_RE.match(target) or PATH_RE.match(target)):
            raise ValueError(
                f"invalid target {target!r} for {name!r}: use a behavior path, "
                "'animations/...' or 'anim:<name>'"
            )
    return value


class Config(_Strict):
    robot: RobotConfig
    llm: LLMConfig = Field(default_factory=LLMConfig)
    conversation: ConversationConfig = Field(default_factory=ConversationConfig)
    stt: STTConfig = Field(default_factory=STTConfig)
    audio: AudioConfig = Field(default_factory=AudioConfig)
    motion: MotionConfig = Field(default_factory=MotionConfig)
    dances: Dict[str, str] = Field(default_factory=dict)
    gestures: Dict[str, str] = Field(default_factory=lambda: dict(DEFAULT_GESTURES))
    robot_profile_dir: str = "~/.naollm/robots"
    animations_dir: str = "animations"
    privacy: PrivacyConfig = Field(default_factory=PrivacyConfig)
    event_mode: bool = False

    @field_validator("dances", "gestures")
    @classmethod
    def _check_catalogues(cls, value: Dict[str, str]) -> Dict[str, str]:
        return _check_catalogue(value)


def _format_error(err: ValidationError) -> str:
    lines = []
    for e in err.errors():
        loc = ".".join(str(p) for p in e["loc"])
        msg = e["msg"]
        if e["type"] == "extra_forbidden":
            msg = "unknown key"
        lines.append(f"{loc}: {msg}")
    return "invalid configuration:\n  " + "\n  ".join(lines)


def parse_config(data: Any) -> Config:
    if not isinstance(data, dict):
        raise ConfigError("invalid configuration: top level must be a mapping")
    try:
        return Config.model_validate(data)
    except ValidationError as err:
        raise ConfigError(_format_error(err)) from None


def load_config(path: str | Path) -> Config:
    p = Path(path)
    try:
        text = p.read_text(encoding="utf-8")
    except OSError as err:
        raise ConfigError(f"cannot read config {p}: {err.strerror}") from None
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as err:
        raise ConfigError(f"cannot parse config {p}: {err}") from None
    return parse_config(data)


def startup_problems(config: Config) -> List[str]:
    """Settings that block start-up (SPEC §12.4)."""
    problems = []
    if config.event_mode:
        if config.privacy.persist_transcripts:
            problems.append("event_mode is true but privacy.persist_transcripts is true")
        if config.privacy.debug_logging:
            problems.append("event_mode is true but privacy.debug_logging is true")
    return problems


def _is_local_ip(addr: str) -> bool:
    ip = ipaddress.ip_address(addr)
    return ip.is_loopback or ip.is_private or ip.is_link_local


def is_local_endpoint(base_url: str, resolve: bool = True) -> bool:
    """True if the LLM endpoint is on this machine or a private network."""
    host = urlparse(base_url).hostname
    if not host:
        return False
    if host == "localhost":
        return True
    try:
        return _is_local_ip(host)
    except ValueError:
        pass
    if not resolve:
        return False
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return False
    addrs = {info[4][0] for info in infos}
    return bool(addrs) and all(_is_local_ip(a) for a in addrs)


def cloud_warning(config: Config) -> Optional[str]:
    if is_local_endpoint(config.llm.base_url):
        return None
    return (
        f"LLM endpoint {config.llm.base_url} is not local: conversation data leaves "
        "the local network. Update the signage."
    )
