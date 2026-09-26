"""Small time helpers shared by triggers and the contact window. Nothing here reads the clock:
the caller always passes `now`."""

import re
from datetime import datetime, time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

_HHMM = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


def require_aware(now: datetime) -> datetime:
    """A naive datetime could silently mean the wrong moment, so it is a programming error."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("`now` must be a timezone-aware datetime")

    return now


def zone(name: object) -> ZoneInfo | None:
    """An IANA zone such as "Asia/Kolkata" (or "UTC"), or None if it is not one."""
    if not isinstance(name, str) or not name:
        return None

    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, OSError):
        return None


def hhmm(value: object) -> time | None:
    """"10:00" -> time(10, 0), or None if it is not exactly HH:MM (24-hour)."""
    match = _HHMM.match(value) if isinstance(value, str) else None
    return time(int(match[1]), int(match[2])) if match else None
