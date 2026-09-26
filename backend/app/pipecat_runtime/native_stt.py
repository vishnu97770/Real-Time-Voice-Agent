"""Pipecat's own Deepgram STT (`DeepgramSTTService`), configured to match the compatibility recognizer as closely
as that service allows, and adapted to the same frame contract `SessionProcessor` already expects.

    audio (InputAudioRawFrame, 8 kHz PCM16, unchanged)
        |
        v
    _ParityDeepgramSTTService (Pipecat's own service; one override, see below)
        |  InterimTranscriptionFrame | TranscriptionFrame (Pipecat's own, exactly as it emits them)
        |  _EndOfUtterance (ours: private, one bit, no text)
        v
    NativeSttAdapter
        |  UninterruptibleInterimTranscriptionFrame | UninterruptibleTranscriptionFrame | UninterruptibleProposedUserStoppedSpeakingFrame
        v
    SessionProcessor                                          (unchanged: the existing frame contract, from frames.py)

The compatibility recognizer (recognizer.py) reads two signals off the existing Deepgram Listener: `speech_final`
(a field on every transcript event) and a separate `UtteranceEnd` event, and turns either into the end-of-utterance
frame. Pipecat's `DeepgramSTTService` receives the very same two signals over the wire (VERIFIED by reading
pipecat-ai 1.11.0's and deepgram-sdk 7.10.0's own source: `ListenV1Results.speech_final` is a field on every result
message, and a `ListenV1UtteranceEnd` message is a distinct type the SDK parses and dispatches like any other), but
its own `_on_message` only ever forwards a `Results` message as a transcript frame, and never forwards
`UtteranceEnd` as anything. `_ParityDeepgramSTTService` overrides `_on_message` with the smallest change that
closes that gap: call the stock implementation unchanged, then, reading the very same `speech_final` field and the
very same `UtteranceEnd` message type the compatibility path reads, push one private marker frame. No business
decision is made here, in the override or in the adapter below: neither one runs guardrails, decides a turn is
complete on its own initiative, or touches the Session. They translate two provider signals into the same two kinds
of frame the compatibility recognizer already produces, and `SessionProcessor` decides everything else, exactly as
it always has.
"""

from dataclasses import dataclass

from pipecat.frames.frames import DataFrame, Frame, InterimTranscriptionFrame, TranscriptionFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.services.deepgram.stt import DeepgramSTTService
from pipecat.transcriptions.language import Language

from app.pipecat_runtime.frames import (
    UninterruptibleInterimTranscriptionFrame,
    UninterruptibleProposedUserStoppedSpeakingFrame,
    UninterruptibleTranscriptionFrame,
)

SAMPLE_RATE = 8000  # what the call's audio is; native STT is given it explicitly, matching the rest of the runtime
LANGUAGE = Language.EN  # the compatibility recognizer's own language, always, not a setting: matched here the same way


@dataclass
class _EndOfUtterance(DataFrame):
    """Deepgram said the caller's utterance is over: speech_final on a result, or its own UtteranceEnd message.
    Carries nothing. Private to this module; NativeSttAdapter is its only consumer."""


class _ParityDeepgramSTTService(DeepgramSTTService):
    """Pipecat's DeepgramSTTService, with the one addition the compatibility recognizer's semantics need: the
    two end-of-utterance signals it already reads over the wire but never turns into a frame of any kind."""

    async def _on_message(self, message) -> None:
        await super()._on_message(message)

        from deepgram.listen.v1.types import ListenV1Results, ListenV1UtteranceEnd

        if isinstance(message, ListenV1UtteranceEnd) or (
            isinstance(message, ListenV1Results) and message.speech_final
        ):
            await self.push_frame(_EndOfUtterance())


class NativeSttAdapter(FrameProcessor):
    """Turns what `_ParityDeepgramSTTService` sends into the frames `SessionProcessor` already knows: the interim
    and final transcripts as the same protected classes the compatibility recognizer emits, and the end-of-utterance
    marker as the same `ProposedUserStoppedSpeakingFrame` (protected) it emits too."""

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)

        if type(frame) is InterimTranscriptionFrame:
            await self.push_frame(
                UninterruptibleInterimTranscriptionFrame(frame.text, frame.user_id, frame.timestamp, frame.language, result=frame.result)
            )
        elif type(frame) is TranscriptionFrame:
            await self.push_frame(
                UninterruptibleTranscriptionFrame(
                    frame.text, frame.user_id, frame.timestamp, frame.language, result=frame.result, finalized=frame.finalized
                )
            )
        elif isinstance(frame, _EndOfUtterance):
            await self.push_frame(UninterruptibleProposedUserStoppedSpeakingFrame())
        else:
            await self.push_frame(frame, direction)


def build_native_stt(
    *, api_key: str, model: str, endpointing_ms: int, utterance_end_ms: int
) -> tuple[_ParityDeepgramSTTService, NativeSttAdapter]:
    """The two processors, configured to match the compatibility recognizer: the same model, endpointing and
    utterance-end values, smart formatting and punctuation on, interim results on, and (Deepgram's own requirement
    for an UtteranceEnd message at all) vad_events on. `linear16` at 8 kHz mono: the runtime's own audio, unchanged,
    with no mu-law step either way (the compatibility path's mu-law conversion exists only for the legacy Deepgram
    Listener; this service takes the runtime's PCM directly)."""
    service = _ParityDeepgramSTTService(
        api_key=api_key,
        sample_rate=SAMPLE_RATE,
        encoding="linear16",
        settings=DeepgramSTTService.Settings(
            model=model,
            language=LANGUAGE,
            endpointing=endpointing_ms,
            utterance_end_ms=utterance_end_ms,
            interim_results=True,
            smart_format=True,
            punctuate=True,
            extra={"vad_events": True},  # Deepgram's own requirement to receive UtteranceEnd messages at all
        ),
    )
    return service, NativeSttAdapter()
