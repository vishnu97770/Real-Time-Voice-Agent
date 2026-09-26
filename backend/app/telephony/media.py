"""Twilio Media Streams as a `MediaLink`.

This is the Twilio media adapter: everything Twilio-specific about a call's audio lives here. The wire format
(JSON messages, base64 8 kHz mu-law payloads), the `mark` and `clear` events, the stream id, and hanging up
through Twilio are all this module's; the voice runtime above it sees only `AudioFrame`s and the neutral
`MediaLink` operations.
"""

import asyncio
import base64
import json
from collections.abc import AsyncIterator, Callable
from typing import Any

from fastapi import WebSocketDisconnect

from app.media import AudioFrame
from app.telephony import mulaw
from app.telephony.base import ProviderCall, TelephonyError

SAMPLE_RATE = 8000  # Twilio Media Streams carries 8 kHz mono mu-law


class TwilioMediaLink:
    def __init__(self, socket: Any, stream_sid: str, call_sid: str, telephony: Any) -> None:
        self._socket = socket
        self._stream_sid = stream_sid
        self._call_sid = call_sid
        self._telephony = telephony
        self._lock = asyncio.Lock()  # one message on the socket at a time
        self._mark_number = 0
        self._last_mark: str | None = None
        self._reached: Callable[[], None] | None = None

    async def _send(self, message: dict[str, Any]) -> None:
        async with self._lock:
            await self._socket.send_text(json.dumps({**message, "streamSid": self._stream_sid}))

    async def audio_in(self) -> AsyncIterator[AudioFrame]:
        """The callee's voice. Also the place Twilio's `mark` echoes are noticed, and where the stream ends:
        on Twilio's `stop`, on a disconnect, or on a message that cannot be read."""
        try:
            async for raw in self._socket.iter_text():
                message = json.loads(raw)
                event = message.get("event")

                if event == "media":
                    yield AudioFrame(mulaw.decode(base64.b64decode(message["media"]["payload"])), SAMPLE_RATE)
                elif event == "mark":
                    # Only the most recent mark means "everything sent so far has played".
                    if message["mark"]["name"] == self._last_mark and self._reached is not None:
                        self._reached()
                elif event == "stop":
                    return
        except (WebSocketDisconnect, asyncio.TimeoutError, ValueError, KeyError):
            return

    async def send_audio(self, frame: AudioFrame) -> None:
        if (frame.sample_rate, frame.channels) != (SAMPLE_RATE, 1):
            raise ValueError("a Twilio media stream carries 8 kHz mono audio")

        await self._send({"event": "media", "media": {"payload": base64.b64encode(mulaw.encode(frame.pcm)).decode()}})

    async def clear_playout(self) -> None:
        await self._send({"event": "clear"})

    async def checkpoint_playout(self) -> None:
        self._mark_number += 1
        self._last_mark = f"m{self._mark_number}"
        await self._send({"event": "mark", "mark": {"name": self._last_mark}})

    def on_playout_reached(self, callback: Callable[[], None]) -> None:
        self._reached = callback

    async def close(self) -> None:
        try:
            adapter = self._telephony.adapter
            await adapter.hang_up(ProviderCall(adapter.name, self._call_sid))
        except TelephonyError:
            pass  # already gone, which is what we wanted
