"""The legacy voice runtime: today's `PhoneCall`, run over a provider-neutral `MediaLink`.

    MediaLink  <->  LegacyVoiceRuntime  <->  PhoneCall (unchanged)  <->  Session.process_turn

`PhoneCall` is not modified. It still speaks its own small protocols (`Line`, `Listener`, `Speaker`), in which
audio is opaque 8 kHz mu-law bytes; this module bridges them to `AudioFrame`s. The mu-law assumption is
Deepgram's (the legacy listener and speaker are configured for telephone-quality mu-law) and lives only here.
Once a listener and speaker that speak PCM exist, this bridge, and the two conversions in it, go away.

Conversation policy is not here and never was: `PhoneCall` hands every utterance to `process_turn`.
"""

import asyncio
from typing import Any

from app.media import AudioFrame, MediaLink
from app.session import Session
from app.telephony import mulaw
from app.telephony.pipeline import Listener, PhoneCall, Speaker

SAMPLE_RATE = 8000  # what the legacy listener and speaker work in


class _LinkLine:
    """`PhoneCall`'s `Line`, on top of a `MediaLink`."""

    def __init__(self, link: MediaLink) -> None:
        self._link = link
        self.checkpoint_name: str | None = None  # PhoneCall's name for the latest checkpoint

    async def send_audio(self, mulaw_bytes: bytes) -> None:
        await self._link.send_audio(AudioFrame(mulaw.decode(mulaw_bytes), SAMPLE_RATE))

    async def send_clear(self) -> None:
        await self._link.clear_playout()

    async def send_mark(self, name: str) -> None:
        self.checkpoint_name = name
        await self._link.checkpoint_playout()

    async def hang_up(self) -> None:
        await self._link.close()


class LegacyVoiceRuntime:
    """`VoiceRuntime` implemented by `PhoneCall`."""

    def __init__(self, *, service: Any, listener: Listener, speaker: Speaker, barge_in_min_words: int = 2) -> None:
        self._service = service  # what PhoneCall already needs: finalize(), and run_detached() for the close
        self._listener = listener
        self._speaker = speaker
        self._barge_in_min_words = barge_in_min_words

    async def run(self, session: Session, link: MediaLink, greeting: str) -> None:
        line = _LinkLine(link)
        call = PhoneCall(
            session=session,
            service=self._service,
            line=line,
            listener=self._listener,
            speaker=self._speaker,
            barge_in_min_words=self._barge_in_min_words,
        )
        link.on_playout_reached(lambda: call.mark_played(line.checkpoint_name))

        try:
            await call.start(greeting)

            async for frame in link.audio_in():
                await call.audio_in(self._to_listener(frame))
        finally:
            # The media ended (the callee hung up, the line dropped) or this was cancelled: record the call.
            # Detached, because a handler is often being cancelled at exactly this moment and the call's
            # result must still be saved. (A no-op if the call has already been closed from inside.)
            await asyncio.shield(self._service.run_detached(call.close("hangup", hang_up=False)))

    @staticmethod
    def _to_listener(frame: AudioFrame) -> bytes:
        if (frame.sample_rate, frame.channels) != (SAMPLE_RATE, 1):
            raise ValueError("the legacy speech recognizer takes 8 kHz mono audio")

        return mulaw.encode(frame.pcm)
