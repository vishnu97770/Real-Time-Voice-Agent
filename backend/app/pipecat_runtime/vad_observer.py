"""Silero voice-activity detection: OBSERVED always, and, in one mode, a single inert hint into SessionProcessor.

It watches the callee's audio and records when speech started and stopped, so that what Pipecat's VAD sees can be
compared with what the recognizer (Deepgram's endpointing and utterance-end) decides, on real calls, before anything
is built on it. Acoustic speech boundaries are not business turn boundaries, and nothing here treats them as such:

  * it never ends a turn, clears playback, calls `process_turn` or touches the Session, in ANY mode, including
    "interrupt" (see below);
  * it emits no frames at all: every frame it is given is passed on, untouched and first, and only afterwards is a
    COPY of the audio handed to a private task that runs the analyzer. So it cannot delay, reorder or drop anything
    in the call's own path, and if the analyzer is slow, fails, or is starved, the call cannot tell;
  * it keeps only the kind of event and a time (and, for a stop, how long the speech lasted): no audio, no text, no
    identity of anyone or anything. The record lives in memory, for whoever holds the observer; it is not persisted.

The analyzer is created when the runtime is constructed, before any audio, so that failing to create it can fall the
call back to the legacy runtime. Everything it holds (its model session and its worker thread) is released when the
pipeline ends.

VOICE_PIPECAT_VAD has three values:

    off        no analyzer, nothing below runs.
    observe    everything above: watches, records, changes nothing.
    interrupt  everything "observe" does, PLUS one thing: when speech starts, `on_speech_hint()` (a callback the
               runtime wires to `SessionProcessor.note_vad_speech_hint`) is called. That method does nothing but
               record that it was called (see session_processor.py) - it is not, and must never become, a second
               path to `_supersede()`/`InterruptionFrame`/`clear_playout()`. The existing word-count-and-playing-state
               barge-in rule is `SessionProcessor`'s alone, driven by transcript text, which VAD does not have; a hint
               with no words cannot satisfy it. Making that rule EARLIER using VAD (rather than merely INFORMED, as
               here) needs correlating a VAD boundary with a transcript that has not arrived yet, which is a real
               design question (buffering, and the risk of interrupting on speech that turns out to be one word) -
               deliberately left for a later, separately-reviewed step. "interrupt" in this step is a wired,
               tested, and provably inert hook: business behavior is unchanged from "observe" (see the parity tests).

Correlation (VadCorrelationTap, below) is a second, separate processor: it sits after the recognizer, sees only
frame TYPES (never their text), and reports two more bare notifications back to the same observer - "a transcript
arrived" and "the recognizer says the utterance ended" - so that VAD's own boundaries can be compared against
Deepgram's, and against a bounded counter of a few coarse mismatches. None of this is stored beyond the call, none
of it is `speech_final` or `UtteranceEnd` for `SessionProcessor`'s purposes, and none of it changes what
`SessionProcessor` decides.
"""

import asyncio
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass

from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADAnalyzer
from pipecat.audio.vad.vad_controller import VADController
from pipecat.frames.frames import Frame, InputAudioRawFrame, StartFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor, FrameProcessorSetup

from app.pipecat_runtime.frames import (
    UninterruptibleInterimTranscriptionFrame,
    UninterruptibleProposedUserStoppedSpeakingFrame,
    UninterruptibleTranscriptionFrame,
)

SAMPLE_RATE = 8000  # what the call's audio is; Silero takes 8 kHz natively, so nothing is resampled
MAX_BACKLOG_FRAMES = 100  # 2 s of 20 ms frames: if the analyzer falls further behind than that, audio is skipped
MAX_OBSERVATIONS = 1000  # a very long call keeps its most recent events, not all of them
MAX_TURN_OBSERVATIONS = 200  # bounded separately: one per utterance, not one per 20 ms frame


@dataclass(frozen=True)
class VadObservation:
    kind: str  # "speech_started" | "speech_stopped"
    at: float  # seconds since the observer started, on the monotonic clock
    duration: float | None = None  # for "speech_stopped": how long that speech lasted, in seconds


@dataclass(frozen=True)
class VADTurnObservation:
    """One utterance's VAD-vs-Deepgram timing, as far as it could be observed. Nothing here is text, audio or
    anyone's identity: every field is a sequence number, a timestamp on the monotonic clock, or a derived duration."""

    sequence: int
    vad_start_at: float | None
    vad_stop_at: float | None
    deepgram_end_at: float | None
    vad_duration: float | None  # vad_stop_at - vad_start_at
    vad_to_deepgram_delta: float | None  # deepgram_end_at - vad_stop_at; negative means Deepgram ended first
    matched: bool  # both a VAD stop and a Deepgram end were observed for the same utterance


@dataclass
class VadDiagnostics:
    """Bounded counters, not events: Stage C's six comparisons, each a running count for the call."""

    vad_speech_no_transcript: int = 0  # VAD saw speech; no transcript followed before the next VAD start (or call end)
    transcript_no_vad_start: int = 0  # a transcript arrived while VAD had not (yet, or ever) reported speech starting
    vad_stop_before_deepgram_end: int = 0
    deepgram_end_before_vad_stop: int = 0  # Deepgram ended the utterance while VAD still considered speech ongoing
    repeated_vad_boundaries: int = 0  # VAD stopped, then started again, before Deepgram ended the utterance for it
    vad_activity_during_playback: int = 0  # VAD reported speech starting while the agent's own audio was playing


def create_silero_analyzer() -> VADAnalyzer:
    """A Silero analyzer set for the call's 8 kHz audio, with Pipecat's default thresholds. Loading the model can fail
    (a missing file, a broken runtime), which is why this is called at construction and not once audio is flowing."""
    return SileroVADAnalyzer(sample_rate=SAMPLE_RATE)


class VadObserver(FrameProcessor):
    def __init__(
        self,
        analyzer: VADAnalyzer,
        *,
        max_backlog: int = MAX_BACKLOG_FRAMES,
        max_observations: int = MAX_OBSERVATIONS,
        max_turn_observations: int = MAX_TURN_OBSERVATIONS,
        on_speech_hint: Callable[[], None] | None = None,
        is_bot_speaking: Callable[[], bool] | None = None,
        **kwargs,
    ) -> None:
        """`on_speech_hint`: called (synchronously, no arguments) when speech starts, only in "interrupt" mode; the
        runtime is the only thing that ever sets it, and only to `SessionProcessor.note_vad_speech_hint`, itself
        inert (see the module docstring). `is_bot_speaking`: an optional read-only check ("is the agent's own audio
        playing right now?"), used only for the Stage C playback-overlap counter; never a reference to `Playout`
        itself, and never written to - this processor still touches no playback state and decides no interruption."""
        super().__init__(**kwargs)
        self._analyzer = analyzer
        self._controller = VADController(analyzer)
        self._audio: asyncio.Queue[InputAudioRawFrame] = asyncio.Queue(maxsize=max_backlog)
        self._observations: deque[VadObservation] = deque(maxlen=max_observations)
        self._turn_observations: deque[VADTurnObservation] = deque(maxlen=max_turn_observations)
        self._diagnostics = VadDiagnostics()
        self._sequence = 0
        self._task: asyncio.Task | None = None
        self._t0 = time.monotonic()
        self._speech_from: float | None = None  # this utterance's VAD start, once stopped: None again until the next
        self._pending: dict | None = None  # a VAD stop waiting to be matched with a Deepgram end
        self._vad_speaking = False  # VAD's own current state: True from speech_started to speech_stopped
        self._had_transcript_since_start = False  # a transcript arrived since the currently-open VAD speech started
        self._on_speech_hint = on_speech_hint
        self._is_bot_speaking = is_bot_speaking
        self.dropped_frames = 0  # audio the analyzer was too far behind to be given
        self.failed = False  # the analyzer could not be run; observation stopped, the call was not affected

        @self._controller.event_handler("on_speech_started")
        async def _started(_controller) -> None:
            self._speech_from = time.monotonic()
            self._observations.append(VadObservation("speech_started", self._speech_from - self._t0))

            if self._pending is not None:  # VAD stopped, Deepgram has not yet ended the utterance, and speech resumed
                self._diagnostics.repeated_vad_boundaries += 1

            self._vad_speaking = True
            self._had_transcript_since_start = False

            if self._is_bot_speaking is not None:
                try:
                    if self._is_bot_speaking():
                        self._diagnostics.vad_activity_during_playback += 1
                except Exception:
                    pass  # a diagnostic check must not cost the call anything

            if self._on_speech_hint is not None:
                try:
                    self._on_speech_hint()
                except Exception:
                    pass  # the hint is informational; its receiver failing is not this processor's problem

        @self._controller.event_handler("on_speech_stopped")
        async def _stopped(_controller) -> None:
            now = time.monotonic()
            started_at = self._speech_from
            lasted = None if started_at is None else now - started_at

            if not self._had_transcript_since_start:
                self._diagnostics.vad_speech_no_transcript += 1

            self._speech_from = None
            self._vad_speaking = False
            self._observations.append(VadObservation("speech_stopped", now - self._t0, lasted))
            self._open_pending(vad_start_at=started_at, vad_stop_at=now)

    @property
    def observations(self) -> tuple[VadObservation, ...]:
        return tuple(self._observations)

    @property
    def turn_observations(self) -> tuple[VADTurnObservation, ...]:
        return tuple(self._turn_observations)

    @property
    def diagnostics(self) -> VadDiagnostics:
        return VadDiagnostics(**vars(self._diagnostics))

    # --- correlation: called by VadCorrelationTap, never by anything of our own -------------------------------------

    def note_transcript_seen(self) -> None:
        """A transcript (interim or final) reached SessionProcessor. No text is given, and none is kept."""
        self._had_transcript_since_start = True

        if not self._vad_speaking:
            self._diagnostics.transcript_no_vad_start += 1

    def note_end_signal(self) -> None:
        """The recognizer's speech_final / UtteranceEnd reached SessionProcessor (Deepgram's own end-of-utterance,
        unchanged and still authoritative; this only records when it happened, for comparison)."""
        now = time.monotonic()

        if self._vad_speaking:
            self._diagnostics.deepgram_end_before_vad_stop += 1

        if self._pending is not None:
            self._close_pending(deepgram_end_at=now)
        else:
            self._sequence += 1
            self._turn_observations.append(
                VADTurnObservation(self._sequence, None, None, now - self._t0, None, None, matched=False)
            )

    def _open_pending(self, *, vad_start_at: float | None, vad_stop_at: float) -> None:
        if self._pending is not None:  # an earlier stop was never matched; record it unmatched before starting a new one
            self._close_pending(deepgram_end_at=None)

        self._pending = {"vad_start_at": vad_start_at, "vad_stop_at": vad_stop_at}

    def _close_pending(self, *, deepgram_end_at: float | None) -> None:
        pending, self._pending = self._pending, None
        vad_start_at, vad_stop_at = pending["vad_start_at"], pending["vad_stop_at"]
        matched = deepgram_end_at is not None
        delta = None if not matched else deepgram_end_at - vad_stop_at
        duration = None if vad_start_at is None else vad_stop_at - vad_start_at

        if matched and delta > 0:
            self._diagnostics.vad_stop_before_deepgram_end += 1

        self._sequence += 1
        self._turn_observations.append(
            VADTurnObservation(
                self._sequence,
                None if vad_start_at is None else vad_start_at - self._t0,
                vad_stop_at - self._t0,
                None if deepgram_end_at is None else deepgram_end_at - self._t0,
                duration,
                delta,
                matched=matched,
            )
        )

    async def setup(self, setup: FrameProcessorSetup) -> None:
        await super().setup(setup)

        try:
            await self._controller.setup(setup)  # gives the analyzer the pipeline's sample rate
        except Exception:
            self.failed = True  # observation is optional: never let it break the call

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        await self.push_frame(frame, direction)  # first, and unchanged: the call's own path is not ours to touch

        if isinstance(frame, StartFrame) and not self.failed:
            try:
                await self._controller.start()
                self._task = self.create_task(self._analyse(), "vad-observer")
            except Exception:
                self.failed = True
        elif isinstance(frame, InputAudioRawFrame) and not self.failed:
            try:
                self._audio.put_nowait(frame)
            except asyncio.QueueFull:
                self.dropped_frames += 1

    async def _analyse(self) -> None:
        try:
            while True:
                await self._controller.process_frame(await self._audio.get())
        except asyncio.CancelledError:
            raise
        except Exception:
            self.failed = True  # the analyzer stopped working; the call carries on exactly as before

    async def cleanup(self) -> None:
        if self._task is not None:
            task, self._task = self._task, None
            await self.cancel_task(task)

        await self._controller.cleanup()  # stops its idle timer and shuts the analyzer's worker thread down
        await super().cleanup()


class VadCorrelationTap(FrameProcessor):
    """Sits after the recognizer (compat or native - both send the same three frame types, see frames.py), before
    SessionProcessor. It never reads a frame's text or any other content: only its TYPE, to report two bare
    notifications back to a VadObserver. Every frame is forwarded unchanged, always; this processor makes no
    decision, holds no state of the conversation, and cannot itself be why the call behaves differently."""

    def __init__(self, observer: VadObserver, **kwargs) -> None:
        super().__init__(**kwargs)
        self._observer = observer

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)

        if isinstance(frame, (UninterruptibleInterimTranscriptionFrame, UninterruptibleTranscriptionFrame)):
            self._observer.note_transcript_seen()
        elif isinstance(frame, UninterruptibleProposedUserStoppedSpeakingFrame):
            self._observer.note_end_signal()

        await self.push_frame(frame, direction)
