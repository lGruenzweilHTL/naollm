"""Laptop-side wake word with openWakeWord (laptop-wake-word spec, SPEC §11.2).

The robot streams its front microphone while detection is active. This module
turns the robot's 1365-sample chunks into the 1280-sample frames that
openWakeWord expects, and scores each frame. Audio stays in memory only: the
re-framer keeps less than one frame, the model keeps at most 2 s, and both are
cleared on a trigger, a pause and shutdown. Nothing is written, logged or passed
to speech to text.
"""

from __future__ import annotations

import threading
from collections import deque
from pathlib import Path
from typing import Any, Callable, Optional, Protocol

RATE = 16000
FRAME_SAMPLES = 1280            # 80 ms, openWakeWord's frame
FRAME_BYTES = FRAME_SAMPLES * 2
MAX_BUFFER_S = 2.0
MAX_BUFFER_SAMPLES = int(RATE * MAX_BUFFER_S)
# The model's share: the re-framer may hold up to one more frame, and the sum stays within 2 s.
MODEL_BUFFER_SAMPLES = MAX_BUFFER_SAMPLES - FRAME_SAMPLES
OWW_VERSION = "0.6.0"
# After a hit, this many frames (about 1 s) are dropped unscored, so one phrase triggers once.
REFRACTORY_FRAMES = 12


class WakeUnavailable(RuntimeError):
    """The `wake` extra is missing or the model file cannot load."""


class WakeModel(Protocol):
    def score(self, frame: bytes) -> float: ...
    def reset(self) -> None: ...
    def buffered_samples(self) -> int: ...


class OpenWakeWordModel:
    """One custom model on onnxruntime. Its raw audio buffer is capped below 2 s.

    openWakeWord 0.6.0 keeps 10 s of raw audio in `AudioFeatures.raw_data_buffer`,
    and its streaming melspectrogram reads only the newest 1280 + 480 samples, so a
    cap of 2 s minus one frame changes no score. `reset` clears the deque in place, so
    the cap stays.
    """

    def __init__(self, path: str) -> None:
        file = Path(path).expanduser()
        if not file.is_file():
            raise WakeUnavailable(f"wake model not found: {file}")
        try:
            import numpy
            from openwakeword.model import Model
        except ImportError as err:
            raise WakeUnavailable(
                f"the laptop wake engine needs openwakeword ({err}). Install the wake extra: "
                f"pip install 'naollm[wake]' and pip install --no-deps openwakeword=={OWW_VERSION}"
            ) from None
        try:
            self._model: Any = Model(wakeword_models=[str(file)], inference_framework="onnx")
        except Exception as err:
            raise WakeUnavailable(f"cannot load the wake model {file}: {err}") from None
        self._numpy = numpy
        self._cap()

    def _cap(self) -> None:
        pre = self._model.preprocessor
        pre.raw_data_buffer = deque(pre.raw_data_buffer, maxlen=MODEL_BUFFER_SAMPLES)

    def score(self, frame: bytes) -> float:
        scores = self._model.predict(self._numpy.frombuffer(frame, dtype=self._numpy.int16))
        return float(max(scores.values())) if scores else 0.0

    def reset(self) -> None:
        self._model.reset()
        self._cap()

    def buffered_samples(self) -> int:
        pre = self._model.preprocessor
        return len(pre.raw_data_buffer) + len(pre.raw_data_remainder)


class LaptopWake:
    """Scores frames and reports a hit once per trigger. Thread-safe: chunks arrive on qi threads."""

    def __init__(self, model: WakeModel, threshold: float, on_hit: Callable[[float], None]) -> None:
        self.model = model
        self.threshold = threshold
        self.on_hit = on_hit
        self.on_score: Optional[Callable[[float], None]] = None
        self._lock = threading.Lock()
        self._rest = bytearray()
        self._skip = 0
        self.active = False

    def start(self) -> None:
        with self._lock:
            self._clear_locked()
            self.active = True

    def stop(self) -> None:
        with self._lock:
            self.active = False
            self._clear_locked()

    def clear(self) -> None:
        with self._lock:
            self._clear_locked()

    def _clear_locked(self) -> None:
        self._rest = bytearray()
        self._skip = 0
        self.model.reset()

    def buffered_samples(self) -> int:
        with self._lock:
            return len(self._rest) // 2 + self.model.buffered_samples()

    def feed(self, chunk: bytes) -> None:
        hit: Optional[float] = None
        with self._lock:
            if not self.active:
                return  # a late chunk after a pause
            self._rest.extend(chunk)
            offset = 0
            while len(self._rest) - offset >= FRAME_BYTES:
                frame = bytes(self._rest[offset:offset + FRAME_BYTES])
                offset += FRAME_BYTES
                if self._skip:
                    self._skip -= 1
                    continue
                score = self.model.score(frame)
                if self.on_score is not None:
                    self.on_score(score)
                if score >= self.threshold:
                    hit = score
                    break
            if hit is not None:
                # The wake phrase audio ends here: it never reaches the listening stream.
                self._clear_locked()
                self._skip = REFRACTORY_FRAMES
            else:
                del self._rest[:offset]
        if hit is not None:
            self.on_hit(hit)
