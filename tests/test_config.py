from pathlib import Path

import pytest

from naollm.config import (
    ConfigError,
    cloud_warning,
    is_local_endpoint,
    load_config,
    parse_config,
    startup_problems,
)

ROOT = Path(__file__).resolve().parents[1]


def minimal(**extra):
    data = {"robot": {"url": "tcp://10.0.0.2:9559"}}
    data.update(extra)
    return data


def test_example_config_loads():
    cfg = load_config(ROOT / "config.example.yaml")
    assert cfg.llm.model == "gemma4:e2b"
    assert cfg.gestures["victory"] == "anim:victory"
    assert cfg.audio.wake_language == "English"
    assert cfg.event_mode is True


def test_unknown_key_is_named():
    with pytest.raises(ConfigError, match=r"motion\.max_step_meters"):
        parse_config(minimal(motion={"max_step_meters": 2}))


def test_wrong_type_is_named():
    with pytest.raises(ConfigError, match=r"llm\.temperature"):
        parse_config(minimal(llm={"temperature": "warm"}))


def test_minimal_config_has_safe_defaults():
    cfg = parse_config(minimal())
    assert cfg.motion.walking_enabled is False
    assert cfg.privacy.age_game_enabled is False
    assert cfg.privacy.persist_transcripts is False
    assert cfg.privacy.debug_logging is False
    assert cfg.llm.max_tool_rounds == 3
    assert cfg.conversation.history_turns == 6


def test_keyframe_gesture_reference():
    cfg = parse_config(minimal(gestures={"victory": "anim:victory"}))
    assert cfg.gestures["victory"] == "anim:victory"


@pytest.mark.parametrize("bad", [{"Macarena Dance": "showcase/dance_macarena"}, {"robot": "not a path"}])
def test_invalid_catalogue_entry(bad):
    with pytest.raises(ConfigError) as err:
        parse_config(minimal(dances=bad))
    assert list(bad)[0] in str(err.value) or list(bad.values())[0] in str(err.value)


def test_event_mode_refuses_debug_logging():
    cfg = parse_config(minimal(event_mode=True, privacy={"debug_logging": True}))
    problems = startup_problems(cfg)
    assert problems and "privacy.debug_logging" in problems[0]


def test_event_mode_refuses_persist_transcripts():
    cfg = parse_config(minimal(event_mode=True, privacy={"persist_transcripts": True}))
    assert any("privacy.persist_transcripts" in p for p in startup_problems(cfg))


def test_development_config_may_debug():
    cfg = parse_config(minimal(event_mode=False, privacy={"debug_logging": True}))
    assert startup_problems(cfg) == []


def test_local_ollama_has_no_cloud_warning():
    cfg = parse_config(minimal())
    assert cloud_warning(cfg) is None


@pytest.mark.parametrize("url", ["http://127.0.0.1:11434/v1", "http://192.168.1.5:11434/v1", "http://[::1]:8000/v1"])
def test_private_addresses_are_local(url):
    assert is_local_endpoint(url)


def test_public_endpoint_warns():
    cfg = parse_config(minimal(llm={"base_url": "https://api.example.com/v1"}))
    assert "not local" in cloud_warning(cfg)
    assert not is_local_endpoint("https://8.8.8.8/v1")


def test_new_key_defaults():
    cfg = parse_config(minimal())
    assert cfg.motion.motors_enabled is True
    assert cfg.robot.battery_reliable is True
    assert cfg.robot.listen_ip is None
    assert cfg.audio.no_speech_timeout_s == 5.0
    assert cfg.robot.language == "German" and cfg.stt.languages == ["de", "en"]


def test_new_keys_set():
    data = minimal()
    data["robot"].update(battery_reliable=False, listen_ip="10.0.0.165")
    data["motion"] = {"motors_enabled": False}
    data["audio"] = {"no_speech_timeout_s": 3}
    cfg = parse_config(data)
    assert cfg.motion.motors_enabled is False and cfg.robot.battery_reliable is False
    assert cfg.robot.listen_ip == "10.0.0.165" and cfg.audio.no_speech_timeout_s == 3


def test_invalid_listen_address_names_the_key():
    data = minimal()
    data["robot"]["listen_ip"] = "laptop"
    with pytest.raises(ConfigError, match=r"robot\.listen_ip"):
        parse_config(data)


def test_no_speech_timeout_must_be_positive():
    data = minimal()
    data["audio"] = {"no_speech_timeout_s": 0}
    with pytest.raises(ConfigError, match="no_speech_timeout_s"):
        parse_config(data)


def test_example_targets_this_robot():
    cfg = load_config(ROOT / "config.example.yaml")
    assert cfg.motion.motors_enabled is False
    assert cfg.robot.language == "English" and cfg.robot.battery_reliable is False
    assert cfg.stt.languages == ["en"] and cfg.stt.fallback_language == "en"
    assert cfg.audio.wake_language == "English" and cfg.audio.wake_words == ["Hey NAO"]
    assert cfg.audio.wake_threshold == 0.4
    assert cfg.privacy.face_recognition_enabled is False
    assert "German" in (ROOT / "config.example.yaml").read_text()


def test_only_url_keeps_german_and_motors():
    cfg = parse_config(minimal())
    assert cfg.robot.language == "German" and cfg.audio.wake_language == "German"
    assert cfg.motion.motors_enabled is True
