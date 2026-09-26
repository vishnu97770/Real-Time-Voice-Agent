"""May this contact be called right now? Independent of any workflow: a workflow can be due while
the contact is still not callable. Pure: no database, no clock.

Checks run in this order and the first failure is the answer:
  1. status is active                     (ContactStatus.ACTIVE)
  2. consent is granted                   (revoked = opted out; unknown is NOT enough)
  3. a phone number is present and looks like one (the outbound API's own pattern; for the
     "phone" channel also international +country format, which Twilio needs)
  4. the current time is inside preferred_contact_time, if the contact has one
"""

import re
from datetime import datetime
from typing import Any

from app.models.enums import ConsentStatus, ContactStatus
from app.outbound import PHONE_PATTERN
from app.phone import to_e164
from app.workflows.clock import hhmm, require_aware, zone
from app.workflows.results import EligibilityResult, Reason

_PHONE = re.compile(PHONE_PATTERN)


def _no(reason: Reason, detail: str | None = None) -> EligibilityResult:
    return EligibilityResult(False, reason, detail)


def check_phone(phone: str | None, channel: str) -> EligibilityResult:
    """Conservative: nothing is corrected or reformatted, the number is only accepted or refused."""
    if phone is None or not phone.strip():
        return _no(Reason.PHONE_MISSING)

    if not _PHONE.fullmatch(phone):  # not .match: its `$` would let a trailing newline through
        return _no(Reason.PHONE_INVALID, "not a recognisable phone number")

    if channel == "phone" and to_e164(phone) is None:
        return _no(Reason.PHONE_INVALID, "a phone call needs international format, e.g. +919876543210")

    return EligibilityResult(True, Reason.CONTACT_ELIGIBLE)


def check_contact_window(window: Any, now: datetime, default_timezone: str = "UTC") -> EligibilityResult:
    """`window` is the contact's preferred_contact_time: {"start": "10:00", "end": "18:00",
    "timezone": "Asia/Kolkata"}. Start is inclusive, end exclusive; start after end wraps midnight.
    No window (None or {}) means any time. A window that cannot be read refuses the call: a
    malformed restriction must not be treated as no restriction."""
    if not window:
        return EligibilityResult(True, Reason.CONTACT_ELIGIBLE)

    if not isinstance(window, dict):
        return _no(Reason.CONTACT_WINDOW_INVALID, "preferred_contact_time must be an object")

    start, end = hhmm(window.get("start")), hhmm(window.get("end"))
    tz = zone(window.get("timezone", default_timezone))

    if start is None or end is None or start == end:
        return _no(Reason.CONTACT_WINDOW_INVALID, "needs different start and end, as HH:MM")

    if tz is None:
        return _no(Reason.CONTACT_WINDOW_INVALID, "timezone must be an IANA zone name")

    local = now.astimezone(tz).time()
    inside = start <= local < end if start < end else local >= start or local < end

    if not inside:
        return _no(Reason.OUTSIDE_CONTACT_WINDOW, f"{local.strftime('%H:%M')} is outside {window['start']}-{window['end']} {tz.key}")

    return EligibilityResult(True, Reason.CONTACT_ELIGIBLE)


def check_contact_eligibility(contact: Any, now: datetime, *, channel: str, default_timezone: str = "UTC") -> EligibilityResult:
    """`default_timezone` is used when the contact's window names none (the workflow's own zone)."""
    require_aware(now)

    if contact.status != ContactStatus.ACTIVE:
        return _no(Reason.CONTACT_INACTIVE, f"contact status is '{contact.status}'")

    if contact.consent_status == ConsentStatus.REVOKED:
        return _no(Reason.OPTED_OUT)

    if contact.consent_status != ConsentStatus.GRANTED:  # unknown, and anything unrecognised
        return _no(Reason.CONSENT_UNKNOWN, f"consent is '{contact.consent_status}'")

    phone = check_phone(contact.phone, channel)

    if not phone.eligible:
        return phone

    return check_contact_window(contact.preferred_contact_time, now, default_timezone)
