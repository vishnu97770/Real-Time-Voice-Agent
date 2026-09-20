import time

from app.session import Session


class TooManySessions(Exception):
    pass


class SessionStore:
    """In-memory calls. Nothing survives a restart: persistent storage is a later
    step, and the interface here is what it will sit behind."""

    def __init__(self, max_sessions: int, ttl_seconds: int) -> None:
        self.max_sessions = max_sessions
        self.ttl_seconds = ttl_seconds
        self._sessions: dict[str, Session] = {}

    def _purge(self) -> None:
        cutoff = time.time() - self.ttl_seconds

        for call_id in [key for key, item in self._sessions.items() if item.last_active < cutoff]:
            del self._sessions[call_id]

    def add(self, session: Session) -> None:
        self._purge()

        if len(self._sessions) >= self.max_sessions:
            raise TooManySessions

        self._sessions[session.id] = session

    def remove(self, call_id: str) -> None:
        self._sessions.pop(call_id, None)

    def all(self) -> list[Session]:
        return list(self._sessions.values())

    def get(self, call_id: str) -> Session | None:
        return self._sessions.get(call_id)

    def __len__(self) -> int:
        return len(self._sessions)
