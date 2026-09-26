"""A `MediaLink` as a Pipecat transport: the callee's frames in, the agent's frames out.

Nothing here knows a provider, a codec or a wire format: the link hands over and takes `AudioFrame`s (16-bit PCM,
8 kHz mono for every link the runtime serves today) and Pipecat sees the same audio as its own frames, byte for byte,
with no resampling either way.

The output is the part that needs care, and is written from what was measured, not from what Pipecat's defaults do:
  * it PACES writes to real time. Pipecat's own bookkeeping ("the bot stopped speaking") follows what it has written,
    so unpaced writes would say the agent had finished a moment after synthesis, seconds before the callee heard it;
  * it turns Pipecat's automatic silence off (the defaults insert silence when idle and two seconds of it at the end);
  * on an interruption it lets Pipecat drop everything still queued, THEN clears what the link already holds, so
    nothing can be written after the clear;
  * it asks the link for a checkpoint when a reply's audio has been written, which is what the goodbye drain and
    the "is the agent still speaking" test wait for. Pipecat has no equivalent, and its own speaking state runs
    ahead of the callee's ears by the send lead plus the link's latency.
"""

import asyncio
import time
from collections.abc import Awaitable, Callable

from pipecat.frames.frames import Frame, InputAudioRawFrame, InterruptionFrame, OutputAudioRawFrame, StartFrame
from pipecat.transports.base_input import BaseInputTransport
from pipecat.transports.base_output import BaseOutputTransport
from pipecat.transports.base_transport import TransportParams

from app.media import AudioFrame, MediaLink
from app.pipecat_runtime.frames import ReplyEndedFrame
from app.pipecat_runtime.playout import Playout

SAMPLE_RATE = 8000
CHANNELS = 1
CHUNK_MS = 20  # audio goes out in chunks of this length, one per pacing interval
SEND_LEAD_CHUNKS = 2  # how far ahead of real time a chunk may be written


class MediaLinkInputTransport(BaseInputTransport):
    def __init__(self, link: MediaLink, on_end: Callable[[], Awaitable[None]], **kwargs) -> None:
        super().__init__(
            TransportParams(audio_in_enabled=True, audio_in_sample_rate=SAMPLE_RATE, audio_in_channels=CHANNELS), **kwargs
        )
        self._link = link
        self._on_end = on_end  # the callee's audio ended (hung up, line dropped, or a frame we cannot serve)
        self._pump: asyncio.Task | None = None

    async def start(self, frame: StartFrame) -> None:
        await super().start(frame)
        await self.set_transport_ready(frame)
        self._pump = self.create_task(self._read(), "media-link-in")

    async def _read(self) -> None:
        try:
            async for audio in self._link.audio_in():
                if (audio.sample_rate, audio.channels) != (SAMPLE_RATE, CHANNELS):
                    break  # this runtime is configured for 8 kHz mono, as the legacy recognizer is

                await self.push_audio_frame(
                    InputAudioRawFrame(audio=audio.pcm, sample_rate=audio.sample_rate, num_channels=audio.channels)
                )
        except asyncio.CancelledError:
            raise
        except Exception:
            pass  # a link that fails is a link that has ended

        await self._on_end()

    async def _stop_pump(self) -> None:
        if self._pump is not None:
            pump, self._pump = self._pump, None
            await self.cancel_task(pump)

    async def stop(self, frame) -> None:
        await super().stop(frame)
        await self._stop_pump()

    async def cancel(self, frame) -> None:
        await super().cancel(frame)
        await self._stop_pump()

    async def cleanup(self) -> None:
        await self._stop_pump()
        await super().cleanup()


class MediaLinkOutputTransport(BaseOutputTransport):
    def __init__(self, link: MediaLink, playout: Playout, on_failure: Callable[[Exception], None], **kwargs) -> None:
        super().__init__(
            TransportParams(
                audio_out_enabled=True,
                audio_out_sample_rate=SAMPLE_RATE,
                audio_out_channels=CHANNELS,
                audio_out_10ms_chunks=CHUNK_MS // 10,
                audio_out_auto_silence=False,
                audio_out_end_silence_secs=0,
            ),
            **kwargs,
        )
        self._link = link
        self._playout = playout
        self._on_failure = on_failure
        self._failing = False  # report a run of failures once, not once per chunk
        self._next_send = 0.0

    async def start(self, frame: StartFrame) -> None:
        await super().start(frame)
        await self.set_transport_ready(frame)  # without this there is no media sender and every frame is dropped

    def _failed(self, error: Exception) -> None:
        if not self._failing:
            self._failing = True
            self._on_failure(error)

    async def write_audio_frame(self, frame: OutputAudioRawFrame) -> bool:
        seconds = len(frame.audio) / (2 * frame.num_channels * frame.sample_rate)
        now = time.monotonic()
        self._next_send = max(self._next_send, now)
        wait = self._next_send - now - seconds * SEND_LEAD_CHUNKS

        if wait > 0:
            await asyncio.sleep(wait)

        self._playout.audio_sent()

        try:
            await self._link.send_audio(AudioFrame(frame.audio, frame.sample_rate, frame.num_channels))
        except Exception as error:
            self._failed(error)
            return False

        self._failing = False
        self._next_send += seconds
        return True

    async def write_transport_frame(self, frame: Frame) -> None:
        """Runs after every audio chunk queued before `frame` has been written."""
        if isinstance(frame, ReplyEndedFrame):
            try:
                await self._playout.end_of_reply(self._link)
            except Exception as error:
                self._failed(error)
            finally:
                frame.flushed.set()

    async def process_frame(self, frame: Frame, direction) -> None:
        await super().process_frame(frame, direction)

        if isinstance(frame, InterruptionFrame):
            # Pipecat has by now cancelled its writer and dropped what it had queued; only what the link already
            # holds is left to throw away.
            self._next_send = 0.0

            try:
                await self._playout.apply_interrupt(self._link)
            except Exception as error:
                self._failed(error)
