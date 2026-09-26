"""The few frames of our own, and the transcript frames as the recognizer sends them.

Two things about Pipecat's frames decide what is here (both observed, not assumed):

  * A SYSTEM frame can overtake data frames pushed before it, so the end of an utterance is not a system frame.
    Pipecat's own `ProposedUserStoppedSpeakingFrame` is a CONTROL frame that stays in order with the transcript, and is
    used as it is; there is no custom end-of-utterance frame.
  * An `InterruptionFrame` reaching a processor cancels the frame it is handling and drops the frames queued behind it,
    unless they are `UninterruptibleFrame`s. The transcript and the end of an utterance must not vanish that way, so the
    recognizer sends them as the `Uninterruptible…` classes below: the stock classes plus that one marker, nothing else.
    "Uninterruptible" only means "not silently dropped". Whether words interrupt the agent, and whether an utterance is
    complete, are still decided by the SessionProcessor, never by the frames."""

import asyncio
from dataclasses import dataclass

from pipecat.frames.frames import (
    DataFrame,
    InterimTranscriptionFrame,
    ProposedUserStoppedSpeakingFrame,
    TranscriptionFrame,
    TTSSpeakFrame,
    UninterruptibleFrame,
)


class SpeechFailed(Exception):
    """The speaker could not produce a sentence. Carries no text, only a short reason."""


@dataclass
class UninterruptibleInterimTranscriptionFrame(InterimTranscriptionFrame, UninterruptibleFrame):
    """A stock interim transcript that an interruption cannot drop."""


@dataclass
class UninterruptibleTranscriptionFrame(TranscriptionFrame, UninterruptibleFrame):
    """A stock final transcript piece that an interruption cannot drop."""


@dataclass
class UninterruptibleProposedUserStoppedSpeakingFrame(ProposedUserStoppedSpeakingFrame, UninterruptibleFrame):
    """The stock end-of-utterance proposal (the recognizer's speech_final or UtteranceEnd), which an interruption
    cannot drop. The SessionProcessor consumes it directly; nothing resolves it into a UserStoppedSpeakingFrame."""


@dataclass
class SpeakFrame(TTSSpeakFrame):
    """One already-vetted sentence to say, exactly as `process_turn` produced it. `done` is resolved when the
    speaker has finished synthesising it (or fails with why it could not), which is what paces the reply."""

    done: asyncio.Future | None = None


@dataclass
class ReplyEndedFrame(DataFrame):
    """Everything of one reply has been sent. Arrives at the output after the reply's audio has been written, so
    the output can ask the link to report when that audio has been heard. `flushed` is set once it has."""

    flushed: asyncio.Event
