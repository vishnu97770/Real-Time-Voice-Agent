"""Phone-number helpers that belong to no telephony provider."""

import re


def to_e164(phone: str) -> str | None:
    """"+91 98765-43210" -> "+919876543210". None if it is not international format."""
    cleaned = re.sub(r"[\s().-]", "", phone)
    return cleaned if re.fullmatch(r"\+[1-9]\d{6,14}", cleaned) else None
