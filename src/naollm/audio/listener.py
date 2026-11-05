"""The voice turn: trigger, listen, end of utterance, transcript, conversation loop (SPEC §11.1)."""

from __future__ import annotations

import asyncio
import threading
import time
from typing import TYPE_CHECKING, Callable, Optional

from naollm.audio.stt import SpeechToText, check_voice_extra
from naollm.audio.vad import End, SpeechTest, UtteranceDetector, VoiceUnavailable, webrtc_speech_test
from naollm.robot.adapter import EventKind, RobotEvent
from naollm.state import State

if TYPE_CHECKING:
    from naollm.bridge import Bridge
    from naollm.llm.loop import ConversationLoop

MIN_UTTERANCE_S = 0.5
WAKE_RESUME_S = 0.5
EARS_TEST_S = 30.0
# Extra wall-clock time before giving up on a stream that stopped delivering audio.
WATCHDOG_MARGIN_S = 2.0
WAKE_TIMER = "wake"


class _Capture:
    """One listening attempt. Runs on the event loop."""

    def __init__(self, detector: UtteranceDetector) -> None:
        self.detector = detector
        self.done = asyncio.Event()
        self.chunks = 0

    def feed(self, chunk: bytes) -> None:
        self.chunks += 1
        if self.detector.feed(chunk) is not None:
            self.done.set()


class Listener:
    def __init__(self, bridge: "Bridge", conversation: Optional["ConversationLoop"] = None,
                 stt: Optional[SpeechToText] = None, speech_test: Optional[SpeechTest] = None) -> None:
        self.bridge = bridge
        self.conversation = conversation
        self.stt = stt
        self.speech_test = speech_test
        self.available = False
        self.unavailable_reason = "not started"
        self.ears_enabled = False
        self.wake_resume_s = WAKE_RESUME_S
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._capture: Optional[_Capture] = None
        self._wake_lock = threading.Lock()
        self._wake_ready = False
        self._wake_on = False
        self._wake_holds = 0
        self._testing = False
        self._test_out: Optional[Callable[[str], None]] = None
        self._test_end: Optional[asyncio.Event] = None
        bridge.listen_handler = self.trigger
        bridge.state.add_listener(self._on_state)
        bridge.robot.events.subscribe(self._on_event)
        bridge.shutdown_hooks.append(self._on_shutdown)
        bridge.reconnect_hooks.append(self._after_reconnect)

    # -- setup -----------------------------------------------------------
    async def prepare(self, load_model: bool = True) -> None:
        """After the bridge has connected. A failure leaves typed chat working (voice-turn spec)."""
        self._loop = asyncio.get_running_loop()
        if self.conversation is None or self.stt is None:
            self.unavailable_reason = "no listening in admin mode"
            return
        try:
            if self.speech_test is None:
                check_voice_extra()
                self.speech_test = webrtc_speech_test()
            if load_model:
                self.bridge.log.log("listen", "loading the speech model")
                await asyncio.to_thread(self.stt.ensure_loaded)
        except VoiceUnavailable as err:
            self.unavailable_reason = str(err)
            self.bridge.log.log("alert", f"LISTENING UNAVAILABLE: {err}. Typed chat still works")
            return
        self.available = True
        self.ears_enabled = True
        await asyncio.to_thread(self._set_up_wake)
        await asyncio.to_thread(self._apply_wake)

    def _set_up_wake(self) -> bool:
        cfg = self.bridge.config.audio
        try:
            self.bridge.robot.audio.configure_wake_words(cfg.wake_words, cfg.wake_threshold, cfg.wake_language)
        except Exception as err:
            self._wake_ready = False
            self.bridge.log.log("alert", f"wake word unavailable: {err}. Head tap and key still work")
            return False
        self._wake_ready = True
        return True

    # -- triggers --------------------------------------------------------
    def trigger(self, source: str) -> None:
        """From any thread."""
        if self._loop is None:
            self.bridge.log.log("listen", "trigger ignored: not ready", source=source)
            return
        self._loop.call_soon_threadsafe(self.start, source)

    def start(self, source: str) -> Optional[asyncio.Task]:
        """On the event loop. Returns the running flow, or None when the trigger is ignored."""
        b = self.bridge
        why = self._ignore_reason(source)
        if why is None and not b.state.transition_from(State.IDLE, State.LISTENING):
            why = f"state {b.state.state.value}"
        if why is not None:
            b.log.log("listen", f"trigger ignored: {why}", source=source)
            return None
        assert self._loop is not None and self.conversation is not None
        b.log.log("listen", "listening", source=source)
        return self.conversation.track(self._loop.create_task(self._run()), self._loop)

    def _ignore_reason(self, source: str) -> Optional[str]:
        b = self.bridge
        if not self.available:
            return self.unavailable_reason
        if self._testing:
            return "ears test running"
        if b.exclusive:
            return f"{b.exclusive} running"
        if source == "wake_word" and not self.ears_enabled:
            return "ears off"
        if not b.llm_enabled:
            return "LLM off"
        if b.state.state != State.IDLE:
            return f"state {b.state.state.value}"
        assert self.conversation is not None
        if self.conversation.busy:
            return "a turn is still running"
        return None

    # -- the flow --------------------------------------------------------
    async def _run(self) -> None:
        b = self.bridge
        try:
            audio = await self._listen()
            if audio is None:
                return
            if not b.state.transition_from(State.LISTENING, State.THINKING):
                return
            try:
                assert self.stt is not None
                transcript = await asyncio.to_thread(self.stt.transcribe, audio)
            except Exception as err:
                b.log.log("error", f"speech to text failed: {err!r}")
                return
            finally:
                audio = None  # noqa: F841  the only copy of the audio ends here
            if not transcript.text:
                b.log.log("listen", "empty transcript ignored")
                return
            assert self.conversation is not None
            await self.conversation.run_turn(transcript.text, transcript.language, prepared=True)
        except asyncio.CancelledError:
            b.log.log("listen", "listening abandoned")
            raise
        except Exception as err:
            b.log.log("error", f"listening failed: {err!r}")
        finally:
            b.state.transition_from(State.LISTENING, State.IDLE)
            b.state.transition_from(State.THINKING, State.IDLE)
            b.touch()

    async def _listen(self) -> Optional[bytes]:
        """The audio of one utterance, or None. The stream is stopped and the buffer gone on every path."""
        b = self.bridge
        cfg = b.config.audio
        audio = b.robot.audio
        assert self.speech_test is not None and self.stt is not None and self._loop is not None
        detector = UtteranceDetector(self.speech_test, cfg.vad_silence_s, cfg.max_utterance_s,
                                     cfg.no_speech_timeout_s)
        capture = _Capture(detector)
        self._capture = capture
        loop = self._loop

        def on_chunk(chunk: bytes) -> None:  # on a qi thread
            try:
                loop.call_soon_threadsafe(capture.feed, chunk)
            except RuntimeError:
                pass  # the event loop has closed

        try:
            await asyncio.to_thread(self._apply_wake)
            await asyncio.to_thread(self.stt.ensure_loaded)
            try:
                await asyncio.to_thread(audio.play_listen_sound)
            except Exception as err:
                b.log.log("error", f"listening sound failed: {err!r}")
            start = asyncio.ensure_future(asyncio.to_thread(audio.start_stream, on_chunk))
            try:
                await asyncio.shield(start)
            except asyncio.CancelledError:
                await asyncio.wait([start])  # stop_stream must come after the start has finished
                raise
            except Exception as err:
                b.log.log("alert", f"audio stream failed: {err}")
                return None
            if not await self._wait_for_end(capture):
                b.log.log("alert", "no audio from the robot: listening stopped")
                return None
            if detector.end == End.NO_SPEECH:
                b.log.log("listen", "nobody spoke")
                return None
            if detector.speech_s < MIN_UTTERANCE_S:
                b.log.log("listen", "utterance too short: ignored")
                return None
            b.log.log("listen", f"utterance ended: {detector.end.value}", seconds=round(detector.speech_s, 1))
            return detector.audio
        finally:
            self._capture = None
            detector.clear()
            try:
                await asyncio.shield(asyncio.to_thread(audio.stop_stream))
            except Exception as err:
                b.log.log("warning", f"audio stream stop failed: {err!r}")

    async def _wait_for_end(self, capture: _Capture) -> bool:
        """False when the stream stops delivering. VAD timing is audio time, so this needs a clock."""
        cfg = self.bridge.config.audio
        started = time.monotonic()
        while True:
            try:
                await asyncio.wait_for(capture.done.wait(), 0.25)
                return True
            except asyncio.TimeoutError:
                pass
            elapsed = time.monotonic() - started
            if capture.chunks == 0 and elapsed > cfg.no_speech_timeout_s + WATCHDOG_MARGIN_S:
                return False
            if elapsed > cfg.no_speech_timeout_s + cfg.max_utterance_s + WATCHDOG_MARGIN_S:
                return False

    # -- wake word rules -------------------------------------------------
    def _apply_wake(self) -> None:
        """Idempotent. Decides from the current state, so a late timer cannot resume wrongly."""
        with self._wake_lock:
            wanted = (self._wake_ready and (self.ears_enabled or self._testing) and self._wake_holds == 0
                      and self.bridge.state.state == State.IDLE)
            if wanted == self._wake_on:
                return
            try:
                self.bridge.robot.audio.set_wake_enabled(wanted)
                self._wake_on = wanted
            except Exception as err:
                self.bridge.log.log("warning", f"wake word switch failed: {err!r}")

    def _on_state(self, old: State, new: State) -> None:
        if new == State.OFFLINE:
            self._wake_on = False  # the session is gone, and the engine with it
            self.bridge.cancel_timer(WAKE_TIMER)
        elif self._wake_ready:
            self.bridge.schedule(WAKE_TIMER, self.wake_resume_s if new == State.IDLE else 0.0, self._apply_wake)
        if self._test_end is not None and new != State.IDLE and self._loop is not None:
            self._loop.call_soon_threadsafe(self._test_end.set)

    def hold_wake(self) -> None:
        """Keeps the wake word paused until `release_wake` (face enrolment)."""
        self._wake_holds += 1
        self._apply_wake()

    def release_wake(self) -> None:
        self._wake_holds -= 1
        self._apply_wake()

    def set_ears(self, enabled: bool) -> Optional[str]:
        """Returns a problem to show, or None."""
        if enabled and not self.available:
            return f"wake word triggering is not available: {self.unavailable_reason}"
        if enabled and not self._wake_ready and not self._set_up_wake():
            return "wake word setup failed; see the alert"
        self.ears_enabled = enabled
        self.bridge.log.log("admin", f"ears {'ON' if enabled else 'OFF'}", source="admin")
        self._apply_wake()
        return None

    async def ears_test(self, out: Callable[[str], None], seconds: float = EARS_TEST_S) -> None:
        b = self.bridge
        if b.state.state != State.IDLE:
            out(f"ears test needs IDLE (state {b.state.state.value})")
            return
        if self._testing:
            out("ears test is already running")
            return
        if self._loop is None:
            self._loop = asyncio.get_running_loop()
        if not self._wake_ready and not await asyncio.to_thread(self._set_up_wake):
            out("ears test failed: the wake word engine cannot start; see the alert")
            return
        self._test_end = asyncio.Event()
        self._test_out = out
        self._testing = True
        try:
            await asyncio.to_thread(self._apply_wake)
            out(f"ears test: say the wake phrase; showing every phrase for {seconds:g} s "
                f"(threshold {b.config.audio.wake_threshold:g})")
            try:
                await asyncio.wait_for(self._test_end.wait(), seconds)
                out(f"ears test ended early: state {b.state.state.value}")
            except asyncio.TimeoutError:
                out("ears test finished")
        finally:
            self._testing = False
            self._test_end = None
            self._test_out = None
            await asyncio.to_thread(self._apply_wake)

    def _on_event(self, event: RobotEvent) -> None:
        if event.kind == EventKind.WAKE_CANDIDATE and self._testing and self._test_out is not None:
            self._test_out(f"  {event.value.phrase}  {event.value.confidence:.2f}")

    # -- lifecycle -------------------------------------------------------
    def _after_reconnect(self) -> None:
        with self._wake_lock:
            self._wake_on = False
        if self._wake_ready:
            self._set_up_wake()
        self._apply_wake()

    def _on_shutdown(self) -> None:
        self._wake_ready = False
        self.bridge.cancel_timer(WAKE_TIMER)
