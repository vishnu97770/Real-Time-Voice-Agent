from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from uuid import uuid4


class SessionState(StrEnum):
    CONNECTING = "SESSION_CONNECTING"
    LISTENING = "LISTENING"
    PROCESSING = "PROCESSING"
    SPEAKING = "SPEAKING"
    INTERRUPTED = "INTERRUPTED"
    SESSION_END = "SESSION_END"


@dataclass(slots=True)
class VoiceSession:
    session_id: str
    state: SessionState
    connected_at: str


class SessionManager:
    def __init__(self) -> None:
        self._sessions: dict[str, VoiceSession] = {}

    def create(self) -> VoiceSession:
        session = VoiceSession(str(uuid4()), SessionState.CONNECTING, datetime.now(timezone.utc).isoformat())
        self._sessions[session.session_id] = session
        return session

    def remove(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)

    def transition(self, session: VoiceSession, state: SessionState) -> None:
        session.state = state


def parse_client_message(message: dict) -> tuple[str, str | None]:
    message_type = message.get("type")
    if not isinstance(message_type, str):
        raise ValueError("message type is required")
    if message_type not in {"ping", "text", "interrupt", "end"}:
        raise ValueError("unsupported message type")
    content = message.get("content")
    if message_type == "text" and (not isinstance(content, str) or not content.strip()):
        raise ValueError("text messages require non-empty content")
    return message_type, content
