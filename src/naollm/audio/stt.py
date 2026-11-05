"""Speech to text with faster-whisper, imported only when loading (SPEC §11.1)."""

from __future__ import annotations

import importlib.util
from dataclasses import dataclass
from typing import Any, Iterable, List, Optional, Protocol, Tuple

from naollm.audio.vad import VoiceUnavailable
from naollm.config import STTConfig

# With several allowed languages, a choice this weak or this close counts as unsure.
MIN_LANGUAGE_PROBABILITY = 0.15
MIN_LANGUAGE_MARGIN = 0.1


@dataclass(frozen=True)
class Transcript:
    text: str
    language: str


class Engine(Protocol):
    def transcribe(self, pcm: bytes, language: Optional[str]) -> Tuple[Iterable[Any], Any]: ...


class WhisperEngine:
    def __init__(self, model_name: str) -> None:
        from faster_whisper import WhisperModel

        self._model = WhisperModel(model_name, device="cpu", compute_type="int8")

    def transcribe(self, pcm: bytes, language: Optional[str]) -> Tuple[Iterable[Any], Any]:
        import numpy as np

        audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
        return self._model.transcribe(audio, language=language, beam_size=1, condition_on_previous_text=False)


def check_voice_extra() -> None:
    for module in ("faster_whisper", "webrtcvad"):
        if importlib.util.find_spec(module) is None:
            raise VoiceUnavailable(
                f"listening needs {module}. Install the voice extra: pip install 'naollm[voice]'")


class SpeechToText:
    def __init__(self, config: STTConfig, engine: Optional[Engine] = None) -> None:
        self.config = config
        self._engine = engine

    def ensure_loaded(self) -> None:
        if self._engine is not None:
            return
        try:
            self._engine = WhisperEngine(self.config.model)
        except ImportError as err:
            raise VoiceUnavailable(
                f"speech to text needs faster-whisper ({err}). Install the voice extra: pip install 'naollm[voice]'"
            ) from None
        except Exception as err:
            raise VoiceUnavailable(f"cannot load the speech model {self.config.model}: {err}") from err

    def transcribe(self, pcm: bytes) -> Transcript:
        """Blocks; run on a worker thread."""
        self.ensure_loaded()
        assert self._engine is not None
        allowed = self.config.languages
        if len(allowed) == 1:
            segments, _ = self._engine.transcribe(pcm, allowed[0])
            return Transcript(self._join(segments), allowed[0])
        segments, info = self._engine.transcribe(pcm, None)
        language = self.choose_language(getattr(info, "all_language_probs", None))
        if language != getattr(info, "language", None):
            # Segments decode lazily, so the first call has done only the detection.
            segments, _ = self._engine.transcribe(pcm, language)
        return Transcript(self._join(segments), language)

    def choose_language(self, probabilities: Optional[List[Tuple[str, float]]]) -> str:
        allowed = {code: 0.0 for code in self.config.languages}
        for code, probability in probabilities or []:
            if code in allowed:
                allowed[code] = float(probability)
        ranked = sorted(allowed.items(), key=lambda item: item[1], reverse=True)
        best, best_p = ranked[0]
        runner_up = ranked[1][1] if len(ranked) > 1 else 0.0
        if best_p < MIN_LANGUAGE_PROBABILITY or best_p - runner_up < MIN_LANGUAGE_MARGIN:
            return self.config.fallback_language
        return best

    @staticmethod
    def _join(segments: Iterable[Any]) -> str:
        return " ".join(s.text.strip() for s in segments if s.text.strip()).strip()
