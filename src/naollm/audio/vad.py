"""End-of-utterance detection on 16 kHz mono 16-bit PCM."""

from __future__ import annotations

from collections import deque
from enum import Enum
from typing import Callable, Deque, Iterator, Optional

RATE = 16000
FRAME_MS = 30
FRAME_SAMPLES = RATE * FRAME_MS // 1000
FRAME_BYTES = FRAME_SAMPLES * 2

# Set on the robot; fan noise near the mic is the main risk (design D7).
VAD_AGGRESSIVENESS = 2
# Consecutive speech frames that count as the start of speech. A single noisy frame does not.
SPEECH_START_FRAMES = 3
# Audio kept from before the start of speech, so the first syllable is not cut off.
PRE_ROLL_FRAMES = 10


class VoiceUnavailable(RuntimeError):
    """The `voice` extra is missing or the speech model cannot load."""


class End(str, Enum):
    SILENCE = "silence"
    MAX_LENGTH = "max_length"
    NO_SPEECH = "no_speech"


SpeechTest = Callable[[bytes], bool]


def webrtc_speech_test(aggressiveness: int = VAD_AGGRESSIVENESS) -> SpeechTest:
    try:
        import webrtcvad
    except ImportError as err:
        raise VoiceUnavailable(
            f"voice activity detection needs webrtcvad ({err}). Install the voice extra: pip install 'naollm[voice]'"
        ) from None
    vad = webrtcvad.Vad(aggressiveness)
    return lambda frame: vad.is_speech(frame, RATE)


class Rechunker:
    """Turns chunks of any length (the robot sends 1365 samples) into 30 ms frames."""

    def __init__(self) -> None:
        self._rest = bytearray()

    def feed(self, chunk: bytes) -> Iterator[bytes]:
        self._rest.extend(chunk)
        offset = 0
        while len(self._rest) - offset >= FRAME_BYTES:
            yield bytes(self._rest[offset:offset + FRAME_BYTES])
            offset += FRAME_BYTES
        del self._rest[:offset]

    def clear(self) -> None:
        self._rest.clear()


class UtteranceDetector:
    """Feed audio; `feed` returns why the utterance ended, once it has. Times are audio time."""

    def __init__(self, is_speech: SpeechTest, silence_s: float, max_utterance_s: float,
                 no_speech_s: float, start_frames: int = SPEECH_START_FRAMES,
                 pre_roll_frames: int = PRE_ROLL_FRAMES) -> None:
        self.is_speech = is_speech
        self.silence_frames = self._frames(silence_s)
        self.max_frames = self._frames(max_utterance_s)
        self.no_speech_frames = self._frames(no_speech_s)
        self.start_frames = start_frames
        self._chunker = Rechunker()
        self._pre_roll: Deque[bytes] = deque(maxlen=max(pre_roll_frames, start_frames))
        self._speech = bytearray()
        self._waited = 0
        self._run = 0
        self._started = False
        self._since_start = 0
        self._silent = 0
        self.speech_frames = 0
        self.end: Optional[End] = None

    @staticmethod
    def _frames(seconds: float) -> int:
        return max(1, int(round(seconds * 1000 / FRAME_MS)))

    @property
    def speech_s(self) -> float:
        """Length from the start of speech to the last speech frame."""
        return self.speech_frames * FRAME_MS / 1000

    @property
    def audio(self) -> bytes:
        return bytes(self._speech)

    def feed(self, chunk: bytes) -> Optional[End]:
        if self.end is not None:
            return self.end
        for frame in self._chunker.feed(chunk):
            self.end = self._frame(frame)
            if self.end is not None:
                break
        return self.end

    def _frame(self, frame: bytes) -> Optional[End]:
        speech = self.is_speech(frame)
        if not self._started:
            self._waited += 1
            self._pre_roll.append(frame)
            self._run = self._run + 1 if speech else 0
            if self._run >= self.start_frames:
                self._started = True
                for old in self._pre_roll:
                    self._speech.extend(old)
                self._pre_roll.clear()
                self._since_start = self.speech_frames = self._run
            elif self._waited >= self.no_speech_frames:
                return End.NO_SPEECH
            return None
        self._speech.extend(frame)
        self._since_start += 1
        if speech:
            self._silent = 0
            self.speech_frames = self._since_start
        else:
            self._silent += 1
            if self._silent >= self.silence_frames:
                return End.SILENCE
        if self._since_start >= self.max_frames:
            return End.MAX_LENGTH
        return None

    def clear(self) -> None:
        """Discards all audio this detector holds."""
        self._speech = bytearray()
        self._pre_roll.clear()
        self._chunker.clear()
