"""The existing speech recognizer, wrapped as a Pipecat processor.

    InputAudioRawFrame --(8 kHz mu-law, as Deepgram is configured)--> Listener
    Listener events --> InterimTranscriptionFrame | TranscriptionFrame | ProposedUserStoppedSpeakingFrame

Nothing about recognition changes: the same Deepgram stream, endpointing and utterance-end settings, and the same
events. This only translates them into Pipecat's own frames, in the order they arrived: the interim and final
transcripts, and Pipecat's `ProposedUserStoppedSpeakingFrame` for the recognizer's speech_final or UtteranceEnd. The
frames are the stock ones with the one marker that stops an interruption from dropping them (see frames.py). What the
frames mean (the barge-in threshold, when an utterance is complete, merging) is the SessionProcessor's, exactly as it
is `PhoneCall`'s in the legacy runtime.

The mu-law conversion is the legacy Deepgram boundary and stays at it: nothing downstream sees it.
"""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from pipecat.frames.frames import Frame, InputAudioRawFrame, StartFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.utils.time import time_now_iso8601

from app.pipecat_runtime.frames import (
    UninterruptibleInterimTranscriptionFrame,
    UninterruptibleProposedUserStoppedSpeakingFrame,
    UninterruptibleTranscriptionFrame,
)
from app.telephony import mulaw
from app.telephony.deepgram import Transcript, UtteranceEnd


class RecognizerProcessor(FrameProcessor):
    def __init__(self, listener: Any, on_lost: Callable[[], Awaitable[None]], **kwargs) -> None:
        super().__init__(**kwargs)
        self._listener = listener  # the legacy Listener: send_audio(mulaw), events(), aclose() (closed by the runtime)
        self._on_lost = on_lost  # the recognizer's events ended without the call ending: the agent cannot hear
        self._task: asyncio.Task | None = None

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)

        if isinstance(frame, StartFrame):
            await self.push_frame(frame, direction)
            self._task = self.create_task(self._listen(), "recognizer")
        elif isinstance(frame, InputAudioRawFrame):
            try:
                await self._listener.send_audio(mulaw.encode(frame.audio))
            except Exception:
                pass  # the listener dropping is noticed in _listen
        else:
            await self.push_frame(frame, direction)

    async def _listen(self) -> None:
        try:
            async for event in self._listener.events():
                if isinstance(event, Transcript):
                    if event.text:
                        if event.is_final:
                            await self.push_frame(UninterruptibleTranscriptionFrame(event.text, "", time_now_iso8601()))
                        else:
                            await self.push_frame(UninterruptibleInterimTranscriptionFrame(event.text, "", time_now_iso8601()))

                    if event.speech_final:
                        await self.push_frame(UninterruptibleProposedUserStoppedSpeakingFrame())
                elif isinstance(event, UtteranceEnd):
                    await self.push_frame(UninterruptibleProposedUserStoppedSpeakingFrame())
                # SpeechStarted: words, not noise, are what cut the agent off
        except asyncio.CancelledError:
            raise
        except Exception:
            pass  # a recognizer that fails has ended, which is what the next line reports

        await self._on_lost()

    async def cleanup(self) -> None:
        if self._task is not None:
            task, self._task = self._task, None
            await self.cancel_task(task)

        await super().cleanup()
