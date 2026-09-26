"""`VoiceRuntime` on Pipecat: builds one pipeline per call and sees the call through to its record.

    MediaLink -> MediaLinkInputTransport -> [VadObserver] -> recognizer -> [VadCorrelationTap] -> SessionProcessor
                                                                            -> SpeakerProcessor -> MediaLinkOutputTransport -> MediaLink

VadObserver and VadCorrelationTap are present only with VOICE_PIPECAT_VAD=observe|interrupt (off by default), and
both only watch (see vad_observer.py): the observer sees audio and reports Silero's own speech boundaries; the tap
sees the recognizer's own frames (never their text) and reports when a transcript arrived and when the recognizer
ended the utterance, so the two can be compared. Neither decides anything. With VOICE_PIPECAT_VAD=interrupt only,
the observer also calls `SessionProcessor.note_vad_speech_hint` when speech starts - itself inert (see there).

The recognizer is one of two things, chosen once, before the pipeline is built, never mid-call:

    VOICE_PIPECAT_STT=compat (default)   RecognizerProcessor, wrapping the existing Deepgram Listener, unchanged
    VOICE_PIPECAT_STT=native             Pipecat's own DeepgramSTTService + NativeSttAdapter (native_stt.py)

Either way `SessionProcessor` receives exactly the same three kinds of frame (see frames.py), and everything it does
with them - turn-taking, digit normalisation, barge-in, cancellation - is unchanged. If native STT cannot be built
(no deepgram-sdk, bad settings), this falls back to `compat` here, before any audio; the compatibility recognizer's
Listener is opened by the route either way, so that fallback needs nothing further. If native STT is chosen and
succeeds, that now-unneeded Listener is closed at once, before the pipeline runs.

The pipeline is configured, not defaulted: 8 kHz in and out, no RTVI, no idle timeout (the service's own sweeper
ends idle calls), no signal handlers (the server owns SIGINT), no Pipecat-side hang-up. Ending a call is this
runtime's, in the same order as the legacy one: stop the pipeline, close the recognizer, hang up the link, then have
the service record the result. Recording is always detached so it completes even if this is cancelled.
"""

import asyncio
import logging
import re
from typing import Any

from loguru import logger
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.workers.runner import WorkerRunner

from app.media import MediaLink
from app.pipecat_runtime.playout import Playout
from app.pipecat_runtime.recognizer import RecognizerProcessor
from app.pipecat_runtime.session_processor import SessionProcessor
from app.pipecat_runtime.speaker import SpeakerProcessor
from app.pipecat_runtime.transport import (
    CHANNELS,
    SAMPLE_RATE,
    MediaLinkInputTransport,
    MediaLinkOutputTransport,
)
from app.pipecat_runtime.vad_observer import (
    VadCorrelationTap,
    VadDiagnostics,
    VadObservation,
    VadObserver,
    VADTurnObservation,
    create_silero_analyzer,
)

log = logging.getLogger("pipecat")

SHUTDOWN_WAIT_SECONDS = 5  # how long to wait for a cancelled pipeline to finish before carrying on regardless

_TEXT = re.compile(r"\b(text|transcript|content|message)\b(\s*[:=]\s*)\[.*?\]", re.IGNORECASE | re.DOTALL)
_logging_configured = False


def configure_pipecat_logging() -> None:
    """Route Pipecat's loguru output into `logging`, WARNING and up, with the text of any frame taken out. Frame
    descriptions carry the words they hold, and a transcript must not reach the logs."""
    global _logging_configured

    if _logging_configured:
        return

    _logging_configured = True
    logger.remove()

    def forward(message) -> None:
        record = message.record
        log.log(record["level"].no, _TEXT.sub(r"\1\2[redacted]", record["message"]))

    logger.add(forward, level="WARNING", format="{message}")


configure_pipecat_logging()


class _Call:
    """Closing one call. The same steps, in the same order, as `PhoneCall` takes."""

    def __init__(self, service: Any, session: Any, link: MediaLink, listener: Any, native_stt: Any = None) -> None:
        self.service, self.session, self.link, self.listener = service, session, link, listener
        self.native_stt = native_stt  # set only in native STT mode: which processor's failure means "can't hear"
        self.worker: PipelineWorker | None = None
        self.closed = False
        self.finished = asyncio.Event()  # the pipeline has ended
        self._closing: asyncio.Task | None = None

    def _later(self, coro) -> None:
        # Not awaited where it is asked for: closing stops the very task that may be asking.
        self._closing = asyncio.create_task(coro)

    # --- what ends a call ---------------------------------------------------------------------------------------------

    async def callee_gone(self) -> None:
        if not self.closed:
            self._later(self.close("hangup", hang_up=False))

    async def listener_lost(self) -> None:
        if not self.closed:
            # Speech recognition dropped. An agent that cannot hear should not stay on the line.
            self.session.log("listener_lost")
            self._later(self.close("listener_lost"))

    def agent_ended(self, reason: str) -> None:
        if not self.closed:
            self._later(self.close(reason))

    def transport_failed(self, error: Exception) -> None:
        self.session.log("pipeline_error", reason=type(error).__name__)

    async def pipeline_error(self, frame: Any) -> None:
        # The one processor whose failure means the same thing listener_lost always has: an agent that cannot hear
        # should not stay on the line. `is_usable` is already False by the time this frame arrives (Pipecat sets it
        # before pushing the frame that reports it), so a permanent failure and a merely-reported one are told apart
        # by the same flag the frame's own processor would be asked about anywhere else.
        if self.native_stt is not None and frame.processor is self.native_stt and not frame.processor.is_usable:
            await self.listener_lost()
            return

        exception = getattr(frame, "exception", None)
        self.session.log("pipeline_error", reason=type(exception).__name__ if exception else "ErrorFrame")

    # --- closing ------------------------------------------------------------------------------------------------------

    async def close(self, reason: str, *, hang_up: bool = True) -> None:
        """End the call and record its result. Safe to call from anywhere, any number of times."""
        if self.closed:
            return

        self.closed = True

        if self.session.end_reason == "client":
            self.session.end_reason = reason

        await self._teardown(hang_up)
        await self.service.finalize(self.session)

    async def service_hangup(self) -> None:
        """`session.hangup`: something else (the service) is ending the call and will record it."""
        if self.closed:
            return

        self.closed = True
        await self._teardown(hang_up=True)

    async def _teardown(self, hang_up: bool) -> None:
        if self.worker is not None and not self.finished.is_set():
            await self.worker.cancel(reason="call ended")

            try:
                await asyncio.wait_for(self.finished.wait(), SHUTDOWN_WAIT_SECONDS)
            except asyncio.TimeoutError:
                self.session.log("pipeline_error", reason="ShutdownTimeout")

        await self.listener.aclose()

        if hang_up:
            try:
                await self.link.close()
            except Exception:
                self.session.log("hangup_failed")

    async def settled(self) -> None:
        """Wait for a close that is already under way (a hang-up asked for from inside the pipeline)."""
        if self._closing is not None:
            await asyncio.shield(self._closing)


class PipecatVoiceRuntime:
    """`VoiceRuntime` implemented on a Pipecat pipeline."""

    def __init__(
        self,
        *,
        service: Any,
        listener: Any,
        speaker: Any,
        barge_in_min_words: int = 2,
        vad: str = "off",
        stt: str = "compat",
        deepgram_api_key: str | None = None,
        deepgram_stt_model: str = "nova-3",
        deepgram_endpointing_ms: int = 400,
        deepgram_utterance_end_ms: int = 1000,
    ) -> None:
        if vad not in ("off", "observe", "interrupt"):
            raise ValueError(f"unknown VAD mode {vad!r}")

        if stt not in ("compat", "native"):
            raise ValueError(f"unknown STT mode {stt!r}")

        self._service = service  # what PhoneCall already needs: finalize(), and run_detached() for the close
        self._listener = listener
        self._speaker = speaker
        self._barge_in_min_words = barge_in_min_words
        # "observe" and "interrupt" both watch; only "interrupt" also wires the inert speech-hint (see vad_observer.py
        # and SessionProcessor.note_vad_speech_hint). The analyzer is created HERE, before any audio, so that failing
        # to create it raises out of the constructor and the factory falls the call back to the legacy runtime. "off"
        # creates nothing.
        self._vad_mode = vad
        self._vad_analyzer = create_silero_analyzer() if vad in ("observe", "interrupt") else None
        self._vad_observer: VadObserver | None = None

        # "native": built HERE too, before any audio, but unlike VAD a call cannot go without a recognizer, so a
        # failure here does not propagate (the factory would fall this call all the way back to LegacyVoiceRuntime):
        # it falls back to `compat` instead, within this same Pipecat call, using the Listener the route already
        # opened for it. Nothing here talks to the network; that happens later, once the pipeline runs.
        self._stt_mode = stt
        self._native_stt = None
        self._native_adapter = None

        if stt == "native":
            try:
                from app.pipecat_runtime.native_stt import build_native_stt

                self._native_stt, self._native_adapter = build_native_stt(
                    api_key=deepgram_api_key,
                    model=deepgram_stt_model,
                    endpointing_ms=deepgram_endpointing_ms,
                    utterance_end_ms=deepgram_utterance_end_ms,
                )
            except Exception as error:
                log.warning("native STT unavailable (%s: %s), using the compatibility recognizer", type(error).__name__, error)
                self._stt_mode = "compat"

    @property
    def vad_observations(self) -> tuple[VadObservation, ...]:
        """What VAD saw during this call: empty unless it is on and the call has run."""
        return self._vad_observer.observations if self._vad_observer is not None else ()

    @property
    def vad_turn_observations(self) -> tuple[VADTurnObservation, ...]:
        """VAD's boundaries, correlated against the recognizer's own end-of-utterance signal, per utterance."""
        return self._vad_observer.turn_observations if self._vad_observer is not None else ()

    @property
    def vad_diagnostics(self) -> VadDiagnostics:
        return self._vad_observer.diagnostics if self._vad_observer is not None else VadDiagnostics()

    async def run(self, session, link: MediaLink, greeting: str) -> None:
        call = _Call(self._service, session, link, self._listener, native_stt=self._native_stt)
        playout = Playout()
        link.on_playout_reached(playout.reached)

        # Built first so "interrupt" mode can hand VAD a bound method of it; built either way before the pipeline.
        session_processor = SessionProcessor(
            session=session,
            playout=playout,
            greeting=greeting,
            on_end=call.agent_ended,
            barge_in_min_words=self._barge_in_min_words,
        )

        if self._vad_analyzer is not None:
            self._vad_observer = VadObserver(
                self._vad_analyzer,
                on_speech_hint=session_processor.note_vad_speech_hint if self._vad_mode == "interrupt" else None,
                is_bot_speaking=lambda: playout.speaking,
            )

        if self._stt_mode == "native":
            recognizer = [self._native_stt, self._native_adapter]
            await self._listener.aclose()  # the compat Listener the route opened as a fallback target is not needed
        else:
            recognizer = [RecognizerProcessor(self._listener, call.listener_lost)]

        worker = PipelineWorker(
            Pipeline(
                [
                    MediaLinkInputTransport(link, call.callee_gone),
                    *([self._vad_observer] if self._vad_observer is not None else []),  # sees the audio, changes nothing
                    *recognizer,
                    # Correlates VAD's boundaries against the recognizer's own end-of-utterance signal (Stage A/C);
                    # sees frame TYPES only, never their text, and decides nothing (see vad_observer.py).
                    *([VadCorrelationTap(self._vad_observer)] if self._vad_observer is not None else []),
                    session_processor,
                    SpeakerProcessor(self._speaker),
                    MediaLinkOutputTransport(link, playout, call.transport_failed),
                ]
            ),
            # Pipecat's defaults are 16 kHz in and 24 kHz out; this call is 8 kHz mono both ways.
            params=PipelineParams(audio_in_sample_rate=SAMPLE_RATE, audio_out_sample_rate=SAMPLE_RATE),
            enable_rtvi=False,
            idle_timeout_secs=None,
            cancel_on_idle_timeout=False,
        )
        call.worker = worker

        @worker.event_handler("on_pipeline_finished")
        async def _finished(_worker, _frame) -> None:
            call.finished.set()

        @worker.event_handler("on_pipeline_error")
        async def _error(_worker, frame) -> None:
            await call.pipeline_error(frame)

        # If anything else ends the call (idle, time limit, a finished job), the service hangs up the line through this.
        session.hangup = call.service_hangup

        runner = WorkerRunner(handle_sigint=False)  # the default would take over the server's SIGINT

        try:
            await runner.add_workers(worker)
            await runner.run()
        finally:
            # The media ended (the callee hung up, the line dropped) or this was cancelled: record the call. Detached,
            # because a handler is often being cancelled at exactly this moment and the call's result must still be
            # saved. (A no-op if the call has already been closed from inside.)
            await asyncio.shield(self._service.run_detached(call.close("hangup", hang_up=False)))
            await call.settled()

            if self._vad_analyzer is not None:
                await self._vad_analyzer.cleanup()  # a no-op if the pipeline already released it; covers a pipeline that never got that far

        # The runner absorbs a cancellation of the task it runs in; the caller must still see it.
        task = asyncio.current_task()

        if task is not None and task.cancelling():
            raise asyncio.CancelledError
