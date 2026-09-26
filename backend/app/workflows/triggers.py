"""Is a workflow due for a contact at a given moment? Pure: no database, no clock.

A trigger type is a function (config, contact metadata, now) -> TriggerResult, registered in
TRIGGERS. Only `date_offset` exists so far; a new type is one more function and one more entry.
"""

from collections.abc import Callable, Mapping
from datetime import date, datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from app.workflows.clock import hhmm, require_aware, zone
from app.workflows.results import Reason, TriggerResult


def _parse_date(value: object, tz: ZoneInfo) -> date | None:
    """A date ("2026-09-25") or an ISO datetime. A datetime with an offset is read in the workflow's
    timezone, so the date is the one the business sees; one without an offset keeps its own date."""
    if not isinstance(value, str):
        return None

    try:
        return date.fromisoformat(value)
    except ValueError:
        pass

    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        return None

    return (moment.astimezone(tz) if moment.tzinfo else moment).date()


def date_offset(config: Mapping[str, Any], metadata: Mapping[str, Any], now: datetime) -> TriggerResult:
    """Due on (a date stored in the contact's metadata) + offset_days, from `time` until the end of
    that day in `timezone`. One execution per contact per date, whatever the scheduler's cadence.

    config: reference_field (str) and offset_days (int) are required; time ("HH:MM", default
    "00:00") and timezone (IANA name, default "UTC") are optional."""
    field, offset = config.get("reference_field"), config.get("offset_days")
    tz, at = zone(config.get("timezone", "UTC")), hhmm(config.get("time", "00:00"))

    problems = [
        message
        for broken, message in (
            (not isinstance(field, str) or not field, "reference_field must be a non-empty string"),
            (not isinstance(offset, int) or isinstance(offset, bool), "offset_days must be an integer"),
            (tz is None, "timezone must be an IANA zone name such as Asia/Kolkata"),
            (at is None, "time must be HH:MM"),
        )
        if broken
    ]

    if problems:
        return TriggerResult(False, Reason.TRIGGER_CONFIG_INVALID, "; ".join(problems))

    raw = metadata.get(field)

    if raw is None or raw == "":
        return TriggerResult(False, Reason.REFERENCE_DATE_MISSING, f"contact has no '{field}'")

    reference = _parse_date(raw, tz)

    if reference is None:
        return TriggerResult(False, Reason.REFERENCE_DATE_INVALID, f"'{field}' is not an ISO date")

    try:
        target = reference + timedelta(days=offset)
    except OverflowError:
        return TriggerResult(False, Reason.REFERENCE_DATE_INVALID, f"'{field}' plus {offset} days is out of range")

    due_at = datetime.combine(target, at, tzinfo=tz)

    # Compare instants. Two datetimes that share one tzinfo object are compared as wall-clock times and
    # their UTC offsets are ignored, so `now` written in the workflow's own zone would be judged wrongly
    # on a DST day. Giving `now` a different tzinfo (UTC) makes Python compare the real moments.
    # (A time that does not exist, e.g. 02:30 on a spring-forward day, is read as 02:30 before the
    # change, which is 03:30 after it; an ambiguous one, e.g. 01:30 on a fall-back day, as its first
    # occurrence. Both are fixed by the calendar, never by when the evaluation happens.)
    if now.astimezone(timezone.utc) < due_at:
        return TriggerResult(False, Reason.NOT_YET_DUE, f"due {due_at.isoformat()}", due_at=due_at)

    if now.astimezone(tz).date() > target:
        return TriggerResult(False, Reason.WINDOW_PASSED, f"was due on {target.isoformat()}", due_at=due_at)

    return TriggerResult(True, Reason.WORKFLOW_DUE, execution_key=target.isoformat(), due_at=due_at)


TRIGGERS: dict[str, Callable[[Mapping[str, Any], Mapping[str, Any], datetime], TriggerResult]] = {
    "date_offset": date_offset,
}


def evaluate_trigger(trigger_type: str, config: Mapping[str, Any], metadata: Mapping[str, Any], now: datetime) -> TriggerResult:
    require_aware(now)
    handler = TRIGGERS.get(trigger_type)

    if handler is None:
        return TriggerResult(False, Reason.UNSUPPORTED_TRIGGER_TYPE, f"no trigger type '{trigger_type}'")

    if not isinstance(config, Mapping):
        return TriggerResult(False, Reason.TRIGGER_CONFIG_INVALID, "trigger_config must be an object")

    return handler(config, metadata if isinstance(metadata, Mapping) else {}, now)
