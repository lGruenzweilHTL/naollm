"""End-of-utterance detection and speech to text, on generated audio and a stub engine."""

import math
import struct
import sys
from types import SimpleNamespace

import pytest

from naollm.audio.stt import SpeechToText, Transcript, check_voice_extra
from naollm.audio.vad import (
    FRAME_BYTES,
    End,
    Rechunker,
    UtteranceDetector,
    VoiceUnavailable,
    webrtc_speech_test,
)
from naollm.config import STTConfig

RATE = 16000
ROBOT_CHUNK = 1365 * 2


def silence(seconds):
    return b"\0" * (int(RATE * seconds) * 2)


def tone(seconds, amplitude=9000):
    n = int(RATE * seconds)
    return struct.pack(f"<{n}h", *(int(amplitude * math.sin(2 * math.pi * 220 * i / RATE)) for i in range(n)))


def energy_test(frame):
    return any(frame)


def detector(silence_s=0.8, max_s=15.0, no_speech_s=5.0, test=energy_test):
    return UtteranceDetector(test, silence_s, max_s, no_speech_s)


def feed_all(d, audio, chunk=ROBOT_CHUNK):
    for i in range(0, len(audio), chunk):
        if d.feed(audio[i:i + chunk]) is not None:
            break
    return d.end


# -- re-chunker ------------------------------------------------------------

@pytest.mark.parametrize("size", [1, 7, 960, 961, ROBOT_CHUNK, 5000])
def test_rechunker_keeps_every_byte_in_order(size):
    data = bytes(i % 251 for i in range(FRAME_BYTES * 7))
    r = Rechunker()
    frames = []
    for i in range(0, len(data), size):
        frames.extend(r.feed(data[i:i + size]))
    assert all(len(f) == FRAME_BYTES for f in frames)
    assert b"".join(frames) == data


def test_rechunker_holds_a_partial_frame():
    r = Rechunker()
    assert list(r.feed(b"\0" * 100)) == []
    assert len(list(r.feed(b"\0" * 860))) == 1


# -- end conditions --------------------------------------------------------

def test_silence_after_speech_ends_at_0_8_s():
    d = detector()
    assert feed_all(d, tone(1.0)) is None
    assert feed_all(d, silence(0.75)) is None
    assert feed_all(d, silence(0.1)) == End.SILENCE


def test_long_speech_ends_at_the_limit():
    d = detector()
    assert feed_all(d, tone(14.9)) is None
    assert feed_all(d, tone(0.2)) == End.MAX_LENGTH
    assert d.speech_s == pytest.approx(15.0, abs=0.1)


def test_nobody_speaks():
    d = detector(no_speech_s=5.0)
    assert feed_all(d, silence(4.9)) is None
    assert feed_all(d, silence(0.2)) == End.NO_SPEECH
    assert d.audio == b""


def test_robot_chunk_size_same_result_as_any_other():
    audio = silence(0.5) + tone(1.0) + silence(1.0)
    results = []
    for chunk in (ROBOT_CHUNK, 960, 333):
        d = detector()
        results.append((feed_all(d, audio, chunk), d.speech_s))
    assert results[0] == results[1] == results[2]
    assert results[0][0] == End.SILENCE


def test_a_click_is_not_speech():
    d = detector(no_speech_s=1.0)
    click = tone(0.03)  # one frame
    assert feed_all(d, click + silence(0.5)) is None
    assert feed_all(d, silence(0.6)) == End.NO_SPEECH


def test_pre_roll_keeps_the_start():
    d = detector()
    feed_all(d, silence(1.0) + tone(1.0) + silence(1.0))
    assert len(d.audio) < len(silence(2.1))
    assert d.audio[:FRAME_BYTES] == b"\0" * FRAME_BYTES  # a little lead-in
    assert any(d.audio)


def test_pre_roll_length_can_be_set():
    lead = {}
    for frames in (2, 4, 10):
        d = UtteranceDetector(energy_test, 0.8, 15.0, 5.0, pre_roll_frames=frames)
        feed_all(d, silence(1.0) + tone(1.0) + silence(1.0))
        lead[frames] = len(d.audio) - len(d.audio.lstrip(b"\0"))
    assert lead[10] - lead[4] == 6 * FRAME_BYTES
    # The pre-roll always holds the start frames, so a shorter one keeps the start too.
    assert lead[2] == lead[4] - FRAME_BYTES


def test_start_frames_can_be_set():
    burst = tone(0.09) + silence(1.0)  # three frames of sound
    strict = UtteranceDetector(energy_test, 0.5, 15.0, 1.0, start_frames=4)
    assert feed_all(strict, burst) == End.NO_SPEECH
    loose = UtteranceDetector(energy_test, 0.5, 15.0, 1.0, start_frames=3)
    assert feed_all(loose, burst) == End.SILENCE


def test_short_speech_length_is_reported():
    d = detector()
    feed_all(d, tone(0.3) + silence(1.0))
    assert d.end == End.SILENCE and d.speech_s < 0.5


def test_feed_after_the_end_changes_nothing():
    d = detector(no_speech_s=0.1)
    feed_all(d, silence(0.2))
    size = len(d.audio)
    assert d.feed(tone(1.0)) == End.NO_SPEECH
    assert len(d.audio) == size


def test_clear_discards_all_audio():
    d = detector()
    feed_all(d, tone(1.0))
    d.clear()
    assert d.audio == b""


def voiced(seconds):
    """Speech-like: a 120 Hz harmonic source, modulated like syllables."""
    n = int(RATE * seconds)
    out = []
    for i in range(n):
        t = i / RATE
        source = sum(math.sin(2 * math.pi * 120 * k * t) / k for k in range(1, 12))
        envelope = 0.4 + 0.6 * abs(math.sin(2 * math.pi * 3 * t))
        out.append(int(max(-1, min(1, source * envelope / 2.5)) * 14000))
    return struct.pack(f"<{n}h", *out)


def test_real_webrtcvad_on_generated_audio():
    pytest.importorskip("webrtcvad")
    d = detector(test=webrtc_speech_test())
    assert feed_all(d, silence(0.5) + voiced(1.5) + silence(1.2)) == End.SILENCE
    assert d.speech_s > 0.5


def test_real_webrtcvad_on_silence():
    pytest.importorskip("webrtcvad")
    d = detector(no_speech_s=1.0, test=webrtc_speech_test())
    assert feed_all(d, silence(1.2)) == End.NO_SPEECH


def test_missing_webrtcvad_names_the_extra(monkeypatch):
    monkeypatch.setitem(sys.modules, "webrtcvad", None)
    with pytest.raises(VoiceUnavailable, match=r"naollm\[voice\]"):
        webrtc_speech_test()


def test_missing_extra_is_named(monkeypatch):
    monkeypatch.setattr("importlib.util.find_spec", lambda name: None)
    with pytest.raises(VoiceUnavailable, match=r"naollm\[voice\]"):
        check_voice_extra()


# -- speech to text --------------------------------------------------------

class StubEngine:
    def __init__(self, text=" Hello there. ", language="en", probabilities=None):
        self.text = text
        self.language = language
        self.probabilities = probabilities
        self.calls = []

    def transcribe(self, pcm, language):
        self.calls.append((pcm, language))
        info = SimpleNamespace(language=language or self.language, all_language_probs=self.probabilities)
        return iter([SimpleNamespace(text=self.text)]), info


def stt(engine, languages=("de", "en"), fallback="de"):
    return SpeechToText(STTConfig(languages=list(languages), fallback_language=fallback), engine=engine)


def test_one_language_is_forced():
    engine = StubEngine()
    result = stt(engine, ["en"], "en").transcribe(b"pcm")
    assert result == Transcript("Hello there.", "en")
    assert engine.calls == [(b"pcm", "en")]


def test_detected_language_not_allowed_takes_the_best_allowed():
    engine = StubEngine(language="fr", probabilities=[("fr", 0.6), ("en", 0.3), ("de", 0.05)])
    result = stt(engine).transcribe(b"pcm")
    assert result.language == "en"
    assert engine.calls[-1] == (b"pcm", "en")


def test_allowed_detection_decodes_once():
    engine = StubEngine(language="de", probabilities=[("de", 0.9), ("en", 0.05)])
    assert stt(engine).transcribe(b"pcm").language == "de"
    assert engine.calls == [(b"pcm", None)]


@pytest.mark.parametrize("probabilities", [
    [("fr", 0.95), ("en", 0.02), ("de", 0.01)],   # nothing allowed is likely
    [("en", 0.45), ("de", 0.42)],                 # too close
    None,                                         # no detection result
])
def test_unsure_uses_the_fallback(probabilities):
    engine = StubEngine(language="en", probabilities=probabilities)
    assert stt(engine, fallback="de").transcribe(b"pcm").language == "de"


def test_empty_transcript():
    assert stt(StubEngine(text="  "), ["en"], "en").transcribe(b"pcm").text == ""


def test_engine_loads_lazily_and_names_the_extra(monkeypatch):
    monkeypatch.setitem(sys.modules, "faster_whisper", None)
    s = SpeechToText(STTConfig())
    with pytest.raises(VoiceUnavailable, match=r"naollm\[voice\]"):
        s.ensure_loaded()


def test_model_failure_is_reported(monkeypatch):
    class Broken:
        def __init__(self, *a, **k):
            raise OSError("no disk")
    monkeypatch.setitem(sys.modules, "faster_whisper", SimpleNamespace(WhisperModel=Broken))
    with pytest.raises(VoiceUnavailable, match="no disk"):
        SpeechToText(STTConfig()).ensure_loaded()
