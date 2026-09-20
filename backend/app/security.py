"""Password hashing, session tokens and rate limiting. Standard library only."""

import hashlib
import hmac
import secrets
import time
from collections import deque

MIN_PASSWORD_LENGTH = 12

# scrypt parameters: ~16 MiB and tens of milliseconds per hash, so guessing is
# slow for an attacker and unnoticeable for a person logging in.
_N, _R, _P = 2**14, 8, 1


def hash_password(password: str) -> str:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"Password must be at least {MIN_PASSWORD_LENGTH} characters")

    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P)
    return f"scrypt${_N}${_R}${_P}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt, digest = stored.split("$")
        if scheme != "scrypt":
            return False
        candidate = hashlib.scrypt(
            password.encode(), salt=bytes.fromhex(salt), n=int(n), r=int(r), p=int(p)
        )
    except (ValueError, TypeError):
        return False

    return hmac.compare_digest(candidate.hex(), digest)


# Verified against when the email is unknown, so "no such user" and "wrong
# password" take the same time and give the same answer.
DUMMY_HASH = hash_password("this password is never valid")


def new_token() -> str:
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    """Only this hash is stored, so a database leak does not leak live sessions."""
    return hashlib.sha256(token.encode()).hexdigest()


class RateLimiter:
    """Sliding-window limiter, in memory.

    Correct for one server process. Behind several workers each has its own
    counters, so the effective limit is multiplied: move this to Redis (or the
    proxy) before scaling out.
    """

    MAX_KEYS = 50_000

    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = {}

    def check(self, key: str, limit: int, window_seconds: float, now: float | None = None) -> float | None:
        """Record a hit. Returns None if allowed, or seconds to wait if over the limit."""
        now = time.monotonic() if now is None else now
        hits = self._hits.setdefault(key, deque())

        while hits and hits[0] <= now - window_seconds:
            hits.popleft()

        if len(hits) >= limit:
            return max(0.1, hits[0] + window_seconds - now)

        hits.append(now)

        if len(self._hits) > self.MAX_KEYS:
            self.prune(now, window_seconds)

        return None

    def prune(self, now: float | None = None, window_seconds: float = 3600) -> None:
        now = time.monotonic() if now is None else now

        for key in [k for k, hits in self._hits.items() if not hits or hits[-1] <= now - window_seconds]:
            del self._hits[key]
