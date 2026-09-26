"""The existing speech synthesizer, wrapped as a Pipecat processor.

    SpeakFrame(text) --> Speaker.synthesize(text) --> TTSAudioRawFrame (8 kHz PCM, decoded from Deepgram's mu-law)

It says exactly the text it is given. Pipecat's own text-to-speech machinery (sentence aggregation, text filters, an
LLM context to append to) is not in the path at all, so what is spoken is what `process_turn` produced and vetted,
sentence for sentence. Each sentence ends with a TTSStoppedFrame, which is what makes the output flush its last chunk.
A failure comes back as `SpeechFailed` on the frame's `done` future, for the SessionProcessor
to record, as `PhoneCall` records it.
"""

import asyncio
from typing import Any

from pipecat.frames.frames import Frame, TTSAudioRawFrame, TTSStoppedFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from app.pipecat_runtime.frames import SpeakFrame, SpeechFailed
from app.telephony import mulaw
from app.telephony.deepgram import DeepgramError

SAMPLE_RATE = 8000


class SpeakerProcessor(FrameProcessor):
    def __init__(self, speaker: Any, **kwargs) -> None:
        super().__init__(**kwargs)
        self._speaker = speaker  # the legacy Speaker: synthesize(text) -> chunks of 8 kHz mu-law

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)

        if isinstance(frame, SpeakFrame):
            await self._speak(frame)
        else:
            await self.push_frame(frame, direction)

    async def _speak(self, frame: SpeakFrame) -> None:
        outcome: BaseException | None = None

        try:
            async for chunk in self._speaker.synthesize(frame.text):
                await self.push_frame(TTSAudioRawFrame(mulaw.decode(chunk), SAMPLE_RATE, 1))
        except asyncio.CancelledError:
            # An interruption cancels this task: the turn that asked has been cancelled too, nobody is waiting.
            if frame.done is not None and not frame.done.done():
                frame.done.cancel()
            raise
        except DeepgramError as error:
            outcome = SpeechFailed(str(error))
        except Exception as error:
            outcome = error

        # The end of this run of speech. Without it Pipecat's output holds the last partial chunk (up to 20 ms) back
        # and it comes out, glued to the front, of whatever is said next.
        await self.push_frame(TTSStoppedFrame())

        if frame.done is not None and not frame.done.done():
            if outcome is None:
                frame.done.set_result(True)
            else:
                frame.done.set_exception(outcome)
