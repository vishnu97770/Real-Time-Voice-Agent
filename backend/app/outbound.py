"""The outbound call contract.

A business workflow sends a CallJobRequest (who to call, why, which profile). The
platform conducts the whole call and sends back a call result: the finished job
with its outcome, transcript and audit trail. The business decides who is called
and what happens with the result; the platform only owns the call.
"""

import asyncio
import hashlib
import hmac
import ipaddress
import socket
import time
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, field_validator

# ringing -> in_progress -> completed | wrong_party
#         -> declined | no_answer | failed
FINAL_STATUSES = ("completed", "wrong_party", "declined", "no_answer", "failed")


class Callee(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    phone: str = Field(pattern=r"^\+?[0-9][0-9 ()-]{5,19}$")


class CallJobRequest(BaseModel):
    # Your own id for this call. Sending the same reference again returns the
    # existing job instead of placing a second call.
    reference: str | None = Field(default=None, min_length=1, max_length=64)
    profile_id: str
    # Whose record the agent may see on this call (from the customers table).
    # Without one it uses the profile's built-in demo data.
    customer_ref: str | None = Field(default=None, min_length=1, max_length=64)
    # "web": the callee opens the returned answer_url. "phone": Twilio rings them
    # (needs telephony configured, and a phone number in international +country format).
    channel: Literal["web", "phone"] = "web"
    callee: Callee
    # Said to the callee, so write it as you would say it: "your recent card activity".
    reason: str = Field(min_length=1, max_length=200)
    # Where the finished call result is POSTed, signed. Optional: you can poll instead.
    callback_url: str | None = Field(default=None, max_length=500)
    ring_timeout_seconds: int = Field(default=120, ge=10, le=900)
    max_duration_seconds: int = Field(default=300, ge=30, le=1800)

    @field_validator("callback_url")
    @classmethod
    def _http_only(cls, value: str | None) -> str | None:
        if value is not None and urlsplit(value).scheme not in ("http", "https"):
            raise ValueError("callback_url must be an http or https URL")
        return value


def mask_phone(phone: str) -> str:
    digits = "".join(ch for ch in phone if ch.isdigit())
    return f"***{digits[-4:]}"


def sign_payload(secret: str, body: bytes, timestamp: int | None = None) -> tuple[str, str]:
    """Returns (header value, timestamp). The timestamp is part of what is signed,
    so a captured callback cannot be replayed later."""
    ts = str(timestamp if timestamp is not None else int(time.time()))
    digest = hmac.new(secret.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
    return f"t={ts},v1={digest}", ts


def verify_signature(secret: str, body: bytes, header: str, tolerance_seconds: int = 300) -> bool:
    """What a receiver should do. Provided so integrators (and our tests) have a reference."""
    try:
        parts = dict(item.split("=", 1) for item in header.split(","))
        ts = int(parts["t"])
    except (ValueError, KeyError):
        return False

    if abs(time.time() - ts) > tolerance_seconds:
        return False

    expected, _ = sign_payload(secret, body, ts)
    return hmac.compare_digest(expected, header)


class UnsafeCallbackURL(ValueError):
    pass


async def check_callback_url(url: str, allow_private: bool) -> None:
    """Refuse callback URLs that point inside our own network.

    A callback URL comes from a caller, so without this the service could be made
    to send requests to internal addresses. Resolution is re-checked at delivery
    time. (It does not defend against DNS that changes between check and send.)
    """
    parts = urlsplit(url)

    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise UnsafeCallbackURL("callback_url must be an http or https URL")

    if allow_private:
        return

    try:
        infos = await asyncio.to_thread(
            socket.getaddrinfo, parts.hostname, parts.port or 443, type=socket.SOCK_STREAM
        )
    except socket.gaierror as error:
        raise UnsafeCallbackURL("callback_url host does not resolve") from error

    for info in infos:
        address = ipaddress.ip_address(info[4][0])

        if not address.is_global:
            raise UnsafeCallbackURL("callback_url must not point to a private or local address")


def iso(timestamp: float | None) -> str | None:
    if timestamp is None:
        return None

    from datetime import datetime, timezone

    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat()


def job_view(row: dict[str, Any], result: dict[str, Any] | None = None) -> dict[str, Any]:
    """A job as the API shows it. The phone number is masked everywhere."""
    view = {
        "job_id": row["id"],
        "reference": row["reference"],
        "status": row["status"],
        "profile_id": row["profile_id"],
        "channel": row.get("channel") or "web",
        "customer_ref": row["customer_ref"],
        "callee": {"name": row["callee_name"], "phone": mask_phone(row["callee_phone"])},
        "reason": row["reason"],
        "created_at": iso(row["created_at"]),
        "expires_at": iso(row["expires_at"]),
        "answered_at": iso(row["answered_at"]),
        "finished_at": iso(row["finished_at"]),
        "call_id": row["call_id"],
        "end_reason": row["end_reason"],
        "callback": {"status": row["callback_status"], "attempts": row["callback_attempts"]},
    }

    if result is not None:
        view["result"] = result

    return view
