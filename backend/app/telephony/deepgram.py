"""Deepgram: streaming speech-to-text and text-to-speech, at telephone quality.

Twilio carries 8 kHz mu-law audio, and Deepgram accepts and produces exactly that,
so nothing is resampled or re-encoded on the way through.
"""

import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import httpx
from websockets.asyncio.client import connect

STT_URL = "wss://api.deepgram.com"
TTS_URL = "https://api.deepgram.com"
KEEPALIVE_SECONDS = 5


class DeepgramError(Exception):
    pass


@dataclass(frozen=True)
class Transcript:
    text: str
    is_final: bool  # this piece of speech will not change any more
    speech_final: bool  # ...and the caller has stopped talking (endpointing)


@dataclass(frozen=True)
class SpeechStarted:
    pass


@dataclass(frozen=True)
class UtteranceEnd:
    pass


Event = Transcript | SpeechStarted | UtteranceEnd


def parse_event(raw: str | bytes) -> Event | None:
    """One Deepgram message -> an event, or None for the ones we do not use."""
    try:
        message = json.loads(raw)
    except (ValueError, TypeError):
        return None

    kind = message.get("type")

    if kind == "Results":
        alternatives = (message.get("channel") or {}).get("alternatives") or [{}]
        return Transcript(
            text=(alternatives[0].get("transcript") or "").strip(),
            is_final=bool(message.get("is_final")),
            speech_final=bool(message.get("speech_final")),
        )
    if kind == "SpeechStarted":
        return SpeechStarted()
    if kind == "UtteranceEnd":
        return UtteranceEnd()
    return None


class SttStream:
    """One call's live transcription."""

    def __init__(self, socket: Any) -> None:
        self._socket = socket
        self._keepalive = asyncio.create_task(self._ping())

    async def _ping(self) -> None:
        # Deepgram closes a stream that has been silent for ~10 s.
        try:
            while True:
                await asyncio.sleep(KEEPALIVE_SECONDS)
                await self._socket.send(json.dumps({"type": "KeepAlive"}))
        except Exception:
            return

    async def send_audio(self, mulaw: bytes) -> None:
        await self._socket.send(mulaw)

    async def events(self) -> AsyncIterator[Event]:
        try:
            async for raw in self._socket:
                event = parse_event(raw)

                if event is not None:
                    yield event
        except Exception:  # the connection dropped: the call goes on without hearing
            return

    async def aclose(self) -> None:
        self._keepalive.cancel()

        try:
            await self._socket.send(json.dumps({"type": "CloseStream"}))
            await self._socket.close()
        except Exception:
            pass


class DeepgramSTT:
    def __init__(
        self,
        api_key: str,
        model: str = "nova-3",
        endpointing_ms: int = 400,
        utterance_end_ms: int = 1000,
        base_url: str = STT_URL,
    ) -> None:
        self.api_key = api_key
        self.query = urlencode(
            {
                "model": model,
                "language": "en",
                "encoding": "mulaw",
                "sample_rate": 8000,
                "channels": 1,
                "interim_results": "true",  # words as they are spoken, which is what makes barge-in fast
                "endpointing": endpointing_ms,
                "utterance_end_ms": utterance_end_ms,
                "vad_events": "true",
                "smart_format": "true",
                "punctuate": "true",
            }
        )
        self.base_url = base_url

    async def open(self) -> SttStream:
        try:
            socket = await connect(
                f"{self.base_url}/v1/listen?{self.query}",
                additional_headers={"Authorization": f"Token {self.api_key}"},
                open_timeout=10,
                max_size=2**20,
            )
        except Exception as error:
            raise DeepgramError(f"could not start speech recognition ({type(error).__name__})") from None

        return SttStream(socket)


class DeepgramTTS:
    def __init__(self, api_key: str, http: httpx.AsyncClient, model: str = "aura-2-thalia-en", base_url: str = TTS_URL) -> None:
        self.api_key = api_key
        self.http = http
        self.url = f"{base_url}/v1/speak?" + urlencode(
            {"model": model, "encoding": "mulaw", "sample_rate": 8000, "container": "none"}
        )

    async def synthesize(self, text: str) -> AsyncIterator[bytes]:
        """Speech for `text` as mu-law bytes, arriving as it is generated, so the
        first syllable can go out before the sentence has finished rendering."""
        try:
            async with self.http.stream(
                "POST",
                self.url,
                json={"text": text},
                headers={"Authorization": f"Token {self.api_key}"},
                timeout=httpx.Timeout(15.0, read=30.0),
            ) as response:
                if response.status_code >= 400:
                    await response.aread()
                    raise DeepgramError(f"text-to-speech failed with status {response.status_code}")

                async for chunk in response.aiter_bytes(chunk_size=1600):
                    if chunk:
                        yield chunk
        except httpx.HTTPError as error:
            raise DeepgramError(f"text-to-speech failed ({type(error).__name__})") from None
