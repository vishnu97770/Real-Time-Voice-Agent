"""What the workflow engine reports. A business rejection (not due, no consent, ...) is a normal
result with a reason, never an exception: a scheduler needs to see and log them. Unexpected
failures (a database error, a bug) are not represented here; they propagate."""

from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any


class Reason(StrEnum):
    # The workflow itself
    WORKFLOW_INACTIVE = "workflow_inactive"
    ORGANIZATION_INACTIVE = "organization_inactive"
    ORGANIZATION_MISMATCH = "organization_mismatch"
    AGENT_INACTIVE = "agent_inactive"
    CONDITIONS_UNSUPPORTED = "conditions_unsupported"
    ACTION_CONFIG_INVALID = "action_config_invalid"

    # Is it time?
    UNSUPPORTED_TRIGGER_TYPE = "unsupported_trigger_type"
    TRIGGER_CONFIG_INVALID = "trigger_config_invalid"
    REFERENCE_DATE_MISSING = "reference_date_missing"
    REFERENCE_DATE_INVALID = "reference_date_invalid"
    NOT_YET_DUE = "not_yet_due"
    WINDOW_PASSED = "window_passed"
    WORKFLOW_DUE = "workflow_due"

    # May this contact be called?
    CONTACT_INACTIVE = "contact_inactive"
    OPTED_OUT = "opted_out"
    CONSENT_UNKNOWN = "consent_unknown"
    PHONE_MISSING = "phone_missing"
    PHONE_INVALID = "phone_invalid"
    CONTACT_WINDOW_INVALID = "contact_window_invalid"
    OUTSIDE_CONTACT_WINDOW = "outside_contact_window"
    CONTACT_ELIGIBLE = "contact_eligible"

    # Creating the call job
    CALL_JOB_CREATED = "call_job_created"
    ALREADY_SCHEDULED = "already_scheduled"
    REFERENCE_CONFLICT = "reference_conflict"  # the execution's reference is held by a job that is not this workflow's
    CALL_JOB_REJECTED = "call_job_rejected"

    # Dispatching a scheduled job. Any reason a dispatcher gives for closing a job is stored as the
    # job's end_reason, a VARCHAR(24) on PostgreSQL: keep every value at 24 characters or fewer.
    DISPATCHED = "dispatched"
    EXECUTION_MISMATCH = "execution_mismatch"  # the workflow is now due for a different execution
    EXECUTION_INVALID = "execution_invalid"  # the job lacks, or has lost, what identifies its execution
    TENANT_MISMATCH = "tenant_mismatch"
    ALREADY_CLAIMED = "already_claimed"  # another dispatcher (or a cancellation) got there first
    TELEPHONY_UNAVAILABLE = "telephony_unavailable"
    TELEPHONY_ERROR = "telephony_error"  # the same word _dial records when Twilio refuses a call
    DISPATCH_ERROR = "dispatch_error"


@dataclass(frozen=True)
class TriggerResult:
    """Is the workflow due for this contact right now?"""

    due: bool
    reason: Reason
    detail: str | None = None
    # Names this execution, the same for every evaluation inside its window (e.g. "2026-09-24").
    # It is what makes a repeated evaluation recognisable as the same execution.
    execution_key: str | None = None
    due_at: datetime | None = None


@dataclass(frozen=True)
class EligibilityResult:
    """May this contact be called (independent of whether a workflow is due)?"""

    eligible: bool
    reason: Reason
    detail: str | None = None


@dataclass(frozen=True)
class Evaluation:
    """A workflow judged against one contact at one moment. Pure: nothing was created."""

    due: bool
    eligible: bool
    reason: Reason  # WORKFLOW_DUE when both are true, otherwise why not
    detail: str | None = None
    execution_key: str | None = None
    due_at: datetime | None = None
    # The validated action to take (set only when the evaluation is actionable).
    action: Any = None

    @property
    def actionable(self) -> bool:
        return self.due and self.eligible


@dataclass(frozen=True)
class WorkflowOutcome:
    """What running a workflow for one contact did."""

    workflow_id: int
    contact_id: int
    due: bool
    eligible: bool
    call_job_created: bool
    reason: Reason
    job_id: str | None = None
    detail: str | None = None
    execution_key: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class DispatchStatus(StrEnum):
    DISPATCHED = "dispatched"  # claimed, and Twilio was asked to ring
    SKIPPED = "skipped"  # left as it was: not now, or someone else has it
    CANCELLED = "cancelled"  # closed without a call: it must never be dialed
    FAILED = "failed"  # claimed, but the call could not be placed


@dataclass(frozen=True)
class DispatchOutcome:
    """What the dispatcher did with one scheduled job."""

    job_id: str
    status: DispatchStatus
    reason: Reason
    detail: str | None = None
    workflow_id: int | None = None
    contact_id: int | None = None
    twilio_call_sid: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class DispatchReport:
    """One dispatcher run: how many jobs it looked at and what became of each."""

    selected: int
    dispatched: int
    skipped: int
    cancelled: int
    failed: int
    outcomes: tuple[DispatchOutcome, ...] = field(default=())

    @classmethod
    def of(cls, outcomes: list[DispatchOutcome]) -> "DispatchReport":
        def count(status: DispatchStatus) -> int:
            return sum(1 for o in outcomes if o.status == status)

        return cls(
            selected=len(outcomes),
            dispatched=count(DispatchStatus.DISPATCHED),
            skipped=count(DispatchStatus.SKIPPED),
            cancelled=count(DispatchStatus.CANCELLED),
            failed=count(DispatchStatus.FAILED),
            outcomes=tuple(outcomes),
        )

    def as_dict(self) -> dict[str, Any]:
        return {**{k: v for k, v in asdict(self).items() if k != "outcomes"}, "outcomes": [o.as_dict() for o in self.outcomes]}


@dataclass(frozen=True)
class WorkflowRunResult:
    """What one workflow's turn in a tick produced: the engine's outcomes, or the error that stopped it."""

    workflow_id: int
    outcomes: tuple[WorkflowOutcome, ...] = ()
    # The exception's class name. The message and traceback are in the log, not here: they can carry
    # data (a database error quotes SQL), and this report is meant to be logged and returned freely.
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None

    def as_dict(self) -> dict[str, Any]:
        return {"workflow_id": self.workflow_id, "error": self.error, "outcomes": [o.as_dict() for o in self.outcomes]}


@dataclass(frozen=True)
class SchedulerReport:
    """One scheduler tick: the workflows it ran, then the dispatch run, all at the same `tick_at`."""

    tick_at: datetime
    workflows_seen: int  # active workflows selected for this tick (after the limit)
    workflows_processed: int
    workflows_failed: int
    workflows_truncated: bool  # there were more active workflows than the limit; the rest were not run
    workflow_results: tuple[WorkflowRunResult, ...]
    dispatch_report: DispatchReport | None  # None when the dispatch run itself failed
    list_error: str | None = None  # the workflows could not be listed
    dispatch_error: str | None = None
    duration_seconds: float = 0.0

    @property
    def ok(self) -> bool:
        """True only if nothing went wrong anywhere in the tick."""
        return self.list_error is None and self.dispatch_error is None and self.workflows_failed == 0

    @property
    def jobs_created(self) -> int:
        return sum(1 for r in self.workflow_results for o in r.outcomes if o.call_job_created)

    def as_dict(self) -> dict[str, Any]:
        return {
            "tick_at": self.tick_at.isoformat(),
            "ok": self.ok,
            "workflows_seen": self.workflows_seen,
            "workflows_processed": self.workflows_processed,
            "workflows_failed": self.workflows_failed,
            "workflows_truncated": self.workflows_truncated,
            "jobs_created": self.jobs_created,
            "list_error": self.list_error,
            "dispatch_error": self.dispatch_error,
            "duration_seconds": self.duration_seconds,
            "workflow_results": [r.as_dict() for r in self.workflow_results],
            "dispatch_report": self.dispatch_report.as_dict() if self.dispatch_report else None,
        }
