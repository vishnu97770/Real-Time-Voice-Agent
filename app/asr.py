from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol


class ASREventType(StrEnum):
    PARTIAL = "transcript.partial"
    FINAL = "transcript.final"
    ERROR = "asr.error"


@dataclass(frozen=True, slots=True)
class ASREvent:
    event_type: ASREventType
    session_id: str
    text: str | None = None
    detail: str | None = None


class StreamingASR(Protocol):
    provider: str
    available: bool

    async def start(self, session_id: str) -> None: ...
    async def push_audio(self, session_id: str, audio: bytes) -> tuple[ASREvent, ...]: ...
    async def finish(self, session_id: str) -> tuple[ASREvent, ...]: ...


class UnavailableASR:
    provider = "none"
    available = False

    async def start(self, session_id: str) -> None:
        return None

    async def push_audio(self, session_id: str, audio: bytes) -> tuple[ASREvent, ...]:
        return (ASREvent(ASREventType.ERROR, session_id, detail="ASR provider is not configured"),)

    async def finish(self, session_id: str) -> tuple[ASREvent, ...]:
        return ()
