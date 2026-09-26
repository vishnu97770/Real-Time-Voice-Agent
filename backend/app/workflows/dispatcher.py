"""The dispatcher: turn a "scheduled" call job into a call, if the call should still happen.

    workflow engine -> scheduled job -> [dispatcher] -> ringing job -> the existing telephony path

It is a plain service, not a loop: something else (a scheduler, later) calls
`dispatch_scheduled_jobs(now)` whenever it likes. `now` is passed in and must be timezone-aware.

For each scheduled phone job, in order:

  1. Identify the execution. The job's own organization, agent, contact and workflow are loaded; no id
     comes from anywhere else. A job that lacks them, or whose records disagree about the tenant, is closed.
  2. Decide again. evaluate_workflow (the Step 4 evaluator, not a copy of it) is run at `now`. Consent,
     status, phone, the contact's window and the workflow, agent and organization states may all have
     changed since the job was scheduled.
       - not actionable: closed, with the evaluator's reason as the job's end_reason
       - the contact's window is closed for now (the only temporary reason): left scheduled
  3. Check that the job is still this execution. The workflow may now be due for a different one (say
     the appointment moved): the job's reference must be the one the evaluator's execution key yields.
  4. Claim it: one conditional UPDATE, scheduled -> ringing, that also gives it a fresh expires_at and
     a fresh answer token. The database lets exactly one dispatcher through.
  5. Only the winner dials, through the existing Service._dial.

Claim first, dial second: a crash between them leaves a ringing job with no call, which the ordinary
ring-timeout sweeper ends as no_answer. That can miss a call; it cannot place two.
"""

import asyncio
import logging
import secrets
from datetime import datetime
from typing import TYPE_CHECKING, Any

from app.outbound import SCHEDULED, job_log_tag
from app.workflows.clock import require_aware
from app.workflows.engine import evaluate_workflow, workflow_reference
from app.workflows.results import DispatchOutcome, DispatchReport, DispatchStatus, Reason

if TYPE_CHECKING:
    from app.service import Service

log = logging.getLogger(__name__)

DEFAULT_LIMIT = 50

# The only reason to wait rather than close: the contact's preferred window is simply not open right now.
# Everything else that makes a job non-actionable is either permanent or a change to the data the job
# was scheduled from, so the job is closed instead of waiting in limbo. (If the window never opens
# that day, the next run finds the day over and closes it as window_passed.)
TEMPORARY = frozenset({Reason.OUTSIDE_CONTACT_WINDOW})

# The limits create_job enforces on ring_timeout_seconds.
MIN_RING_SECONDS, MAX_RING_SECONDS = 10, 900


def ring_timeout_seconds(job: dict[str, Any]) -> int:
    """The ring timeout this job was created with. It is not stored on its own, but create_job sets
    created_at and expires_at = created_at + ring_timeout from the same instant, so it is their difference."""
    seconds = round(job["expires_at"] - job["created_at"])
    return min(max(seconds, MIN_RING_SECONDS), MAX_RING_SECONDS)


class JobDispatcher:
    def __init__(self, service: "Service") -> None:
        self.service = service
        self.repo = service.repo

    async def dispatch_scheduled_jobs(self, now: datetime, *, limit: int = DEFAULT_LIMIT) -> DispatchReport:
        """Look at the oldest `limit` scheduled phone jobs and place, wait for or close each.

        Unexpected failures (a database error) propagate: nothing about the job in hand has changed
        yet, whatever earlier jobs in the run did stays done, and the next run picks up the rest."""
        require_aware(now)

        if limit < 1:
            raise ValueError("limit must be at least 1")

        jobs = await self._db(self.repo.list_scheduled_phone_jobs, limit)
        return DispatchReport.of([await self.dispatch_job(job, now) for job in jobs])

    async def dispatch_job(self, job: dict[str, Any], now: datetime) -> DispatchOutcome:
        """One scheduled job (as a repository row). Safe to call for the same job from several workers."""
        require_aware(now)
        job_id = job["id"]

        def outcome(status: DispatchStatus, reason: Reason, detail: str | None = None, **more: Any) -> DispatchOutcome:
            return DispatchOutcome(
                job_id, status, reason, detail, job.get("workflow_id"), job.get("contact_id"), **more
            )

        async def close(reason: Reason, detail: str | None = None) -> DispatchOutcome:
            # The same ending, with the same callback, as any other job that finishes without a call.
            # "failed" is the existing final status for a call that was not placed; end_reason says why.
            won = await self.service._finish_without_call(
                job_id, "failed", reason=reason.value, cancel_line=False, from_statuses=[SCHEDULED]
            )

            if not won:
                return outcome(DispatchStatus.SKIPPED, Reason.ALREADY_CLAIMED, "the job was no longer scheduled")

            return outcome(DispatchStatus.CANCELLED, reason, detail)  # (the service logged the closing)

        # 1. Identify the execution, from the job's own ids only.
        ids = (job.get("organization_id"), job.get("agent_id"), job.get("contact_id"), job.get("workflow_id"))

        if None in ids or not job.get("reference"):
            return await close(Reason.EXECUTION_INVALID, "the job has no organization, agent, contact, workflow or reference")

        organization, agent, contact, workflow = await self._load(*ids)

        if None in (organization, agent, contact, workflow):
            return await close(Reason.EXECUTION_INVALID, "a record the job points at cannot be found")

        if not (job["organization_id"] == organization.id == agent.organization_id == contact.organization_id == workflow.organization_id):
            return await close(Reason.TENANT_MISMATCH, "the job's records do not share its organization")

        # 2. Decide again, now, with the Step 4 evaluator.
        evaluation = evaluate_workflow(workflow, contact, now, agent=agent, organization=organization)

        # 3. Is this job still the execution the workflow is due for?
        if evaluation.due and evaluation.execution_key is not None:
            if workflow_reference(workflow.id, contact.id, evaluation.execution_key) != job["reference"]:
                return await close(
                    Reason.EXECUTION_MISMATCH, f"the workflow is now due for execution {evaluation.execution_key}"
                )

        if not evaluation.actionable:
            if evaluation.reason in TEMPORARY:
                return outcome(DispatchStatus.SKIPPED, evaluation.reason, evaluation.detail)

            return await close(evaluation.reason, evaluation.detail)

        if self.service.telephony is None:  # nothing was claimed, so nothing is left half done
            return outcome(DispatchStatus.SKIPPED, Reason.TELEPHONY_UNAVAILABLE, "phone calls are not configured")

        # 4. Claim. The ring time starts now, not when the job was scheduled; the token is issued now.
        claimed = await self._db(
            self.repo.transition,
            job_id,
            [SCHEDULED],
            "ringing",
            expires_at=now.timestamp() + ring_timeout_seconds(job),
            answer_token=secrets.token_urlsafe(16),
        )

        if not claimed:  # another dispatcher won, or the job was closed in the meantime
            return outcome(DispatchStatus.SKIPPED, Reason.ALREADY_CLAIMED, "another dispatcher has this job")

        # 5. Dial, as the winner. _dial reports a Twilio refusal by finishing the job itself (failed /
        #    telephony_error); anything else it raises leaves a ringing job that the sweeper will end.
        try:
            placed = await self.service._dial(await self._db(self.repo.get_job, job_id))
        except Exception as error:
            log.exception("could not place the call for claimed job %s", job_id)
            return outcome(
                DispatchStatus.FAILED,
                Reason.DISPATCH_ERROR,
                f"{type(error).__name__} while placing the call; the job stays ringing until it times out",
            )

        if placed["twilio_call_sid"]:
            log.info("scheduled job placed %s", job_log_tag(job))
            return outcome(DispatchStatus.DISPATCHED, Reason.DISPATCHED, twilio_call_sid=placed["twilio_call_sid"])

        if placed["status"] == "failed":
            return outcome(DispatchStatus.FAILED, Reason.TELEPHONY_ERROR, "Twilio refused the call")

        return outcome(DispatchStatus.FAILED, Reason.DISPATCH_ERROR, f"the job is {placed['status']} and has no call")

    async def _load(self, organization_id: int, agent_id: int, contact_id: int, workflow_id: int) -> tuple[Any, ...]:
        return (
            await self._db(self.repo.get_organization, organization_id),
            await self._db(self.repo.get_agent, agent_id),
            await self._db(self.repo.get_contact, contact_id),
            await self._db(self.repo.get_workflow, workflow_id),
        )

    async def _db(self, fn, *args, **kwargs):
        return await asyncio.to_thread(fn, *args, **kwargs)
