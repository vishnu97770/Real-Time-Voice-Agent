from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol


class TTSEventType(StrEnum):
    AUDIO_CHUNK = "tts.audio.chunk"
    COMPLETE = "tts.complete"
    ERROR = "tts.error"


@dataclass(frozen=True, slots=True)
class TTSEvent:
    event_type: TTSEventType
    session_id: str
    audio: bytes | None = None
    detail: str | None = None


class StreamingTTS(Protocol):
    provider: str
    available: bool

    async def synthesize(self, session_id: str, text: str) -> tuple[TTSEvent, ...]: ...


class UnavailableTTS:
    provider = "none"
    available = False

    async def synthesize(self, session_id: str, text: str) -> tuple[TTSEvent, ...]:
        return (TTSEvent(TTSEventType.ERROR, session_id, detail="TTS provider is not configured"),)
