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

from pydantic import BaseModel, Field, field_validator, model_validator

# scheduled -> ringing -> in_progress -> completed | wrong_party
#                      -> declined | no_answer | failed
#
# "scheduled" is a job that has been recorded but not placed (Service.create_job(dispatch=False), used
# only by the workflow engine). Every part of the live call lifecycle acts on "ringing" or
# "in_progress" only (the ring-timeout sweeper, expiry on read, answering, declining, Twilio status
# callbacks, the audio stream, finishing a call), so a scheduled job cannot ring, expire, be answered
# or be ended by any of them. tests/test_dispatch_boundary.py holds that in place.
SCHEDULED = "scheduled"

# DISPATCH CONTRACT: what places a scheduled job. Implemented by app/workflows/dispatcher.py and pinned
# by tests/test_dispatcher.py; nothing runs it on a schedule yet (a caller passes it `now`).
#
#  1. Pick jobs with status "scheduled" and channel "phone". (A web job has no way to reach the callee.)
#  2. Decide again, now. Consent, status, the contact's window and the workflow, agent and organization
#     states may have changed since the job was scheduled; the engine does not cancel anything. Load
#     the job's own contact, workflow, agent and organization by the ids on the job and run
#     app.workflows.evaluate_workflow(...) at the current time. Place the call only if it is
#     actionable AND its execution_key is the one the job was scheduled for (the reference ends with it).
#     Permanent no (opted_out, contact_inactive, workflow_inactive, agent_inactive, organization_inactive,
#     window_passed): cancel. Temporary no (outside_contact_window): leave it scheduled for a later tick.
#  3. Claim it with ONE atomic compare-and-swap, before dialing:
#         repo.transition(job_id, ["scheduled"], "ringing", expires_at=now + ring_timeout,
#                         answer_token=<a fresh token>)
#     Exactly one dispatcher wins; the losers get False and skip. This is what prevents a second call.
#     expires_at MUST be reset here: the value stored at scheduling time is stale, and a job claimed
#     with a stale one is swept as no_answer on the next sweep. (answer_token was generated when the job
#     was recorded and has never been shown; rotate it anyway. A phone call never uses it.)
#  4. Then dial with the existing Service._dial(job): it takes the ring time and the stream token's lifetime
#     from expires_at, records twilio_call_sid, and on a Twilio error moves the job ringing -> failed.
#     Claim first, dial second: if the process dies in between, the job is a ringing job with no call,
#     which the sweeper ends as no_answer. The worst case is a missed call, never two calls.
#  5. Never create a job here, and never take ids from anywhere but the job: the job's organization,
#     agent, contact and workflow already agree (composite foreign keys), and the reference already
#     guarantees one job per execution.
#  6. Closing a job that was never placed uses Service._finish_without_call(..., from_statuses=["scheduled"]):
#     the same callback as any job that ends without a call, status "failed" (the existing final status
#     for a call that was not placed) and end_reason = the reason (24 characters at most). There is no
#     separate "cancelled" status; status has no CHECK constraint, so adding one later needs no migration.
#  7. A job that ends failed / no_answer is not retried by any of this. A retry is a new job with its
#     own reference (e.g. "<reference>:retry:2").
FINAL_STATUSES = ("completed", "wrong_party", "declined", "no_answer", "failed")


# What a callee's phone number may look like. Shared with the workflow eligibility check.
PHONE_PATTERN = r"^\+?[0-9][0-9 ()-]{5,19}$"


class Callee(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    phone: str = Field(pattern=PHONE_PATTERN)


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
    # Optional domain links: which organization owns the job, which of its agents makes the call,
    # which of its contacts is called, and the workflow that created it. Leave all out for a plain
    # job. The agent, contact and workflow must belong to the organization.
    organization_id: int | None = Field(default=None, ge=1)
    agent_id: int | None = Field(default=None, ge=1)
    contact_id: int | None = Field(default=None, ge=1)
    workflow_id: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def _links_need_an_organization(self) -> "CallJobRequest":
        linked = (self.agent_id, self.contact_id, self.workflow_id)

        if any(value is not None for value in linked) and self.organization_id is None:
            raise ValueError("organization_id is required with agent_id, contact_id or workflow_id")

        return self

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


def job_log_tag(row: dict[str, Any]) -> str:
    """What identifies a job in a log line: its id, and for a workflow's job also the workflow, contact,
    agent and organization and the execution reference (which is made of ids and a date). Never a phone
    number, a name, a token or a callback URL. A reference some API caller chose is left out."""
    parts = [f"job={row['id']}"]

    for key in ("organization_id", "workflow_id", "agent_id", "contact_id"):
        if row.get(key) is not None:
            parts.append(f"{key.removesuffix('_id')}={row[key]}")

    if row.get("workflow_id") is not None and (row.get("reference") or "").startswith("wf:"):
        parts.append(f"reference={row['reference']}")

    return " ".join(parts)


def job_view(row: dict[str, Any], result: dict[str, Any] | None = None) -> dict[str, Any]:
    """A job as the API shows it. The phone number is masked everywhere."""
    view = {
        "job_id": row["id"],
        "reference": row["reference"],
        "status": row["status"],
        "profile_id": row["profile_id"],
        "channel": row.get("channel") or "web",
        "customer_ref": row["customer_ref"],
        "organization_id": row.get("organization_id"),
        "agent_id": row.get("agent_id"),
        "contact_id": row.get("contact_id"),
        "workflow_id": row.get("workflow_id"),
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
