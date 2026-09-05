from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol


class VADEventType(StrEnum):
    SPEECH_START = "speech.start"
    SPEECH_END = "speech.end"
    ERROR = "vad.error"


@dataclass(frozen=True, slots=True)
class VADEvent:
    event_type: VADEventType
    session_id: str
    detail: str | None = None


class VoiceActivityDetector(Protocol):
    provider: str
    available: bool

    async def process(self, session_id: str, audio: bytes) -> tuple[VADEvent, ...]: ...


class UnavailableVAD:
    provider = "none"
    available = False

    async def process(self, session_id: str, audio: bytes) -> tuple[VADEvent, ...]:
        return ()
