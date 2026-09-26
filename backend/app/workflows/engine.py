"""The workflow engine: decide whether a workflow should place a call to a contact, and if so
create the call job. It does not dial, schedule or loop: a scheduler (a later step) calls it.

    evaluate_workflow(workflow, contact, now)      pure: due? eligible? why not?
    WorkflowEngine(service).run(workflow_id, now)  the same, then create call jobs

Call jobs are created through Service.create_job (dispatch=False), so they get the same
validation, tenant checks and reference-based idempotency as any other job, and are left
"scheduled" for whatever places the call later.
"""

import asyncio
import hashlib
import logging
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.models.enums import AgentStatus, OrganizationStatus, WorkflowStatus
from app.outbound import Callee, CallJobRequest
from app.service import Conflict, NotFound, Service
from app.workflows.clock import require_aware, zone
from app.workflows.eligibility import check_contact_eligibility
from app.workflows.results import Evaluation, Reason, WorkflowOutcome
from app.workflows.triggers import evaluate_trigger

log = logging.getLogger(__name__)

MAX_REFERENCE_LENGTH = 64  # call_jobs.reference


class CallAction(BaseModel):
    """What a workflow's action_config must say. Only these keys are accepted: in particular a
    workflow cannot name an organization, agent or contact of its own here."""

    model_config = ConfigDict(extra="forbid")

    profile_id: str = Field(min_length=1)
    reason: str = Field(min_length=1, max_length=200)  # said to the callee
    # An automated call rings a phone: nobody is there to open a web link.
    channel: Literal["web", "phone"] = "phone"
    callback_url: str | None = Field(default=None, max_length=500)
    ring_timeout_seconds: int = Field(default=120, ge=10, le=900)
    max_duration_seconds: int = Field(default=300, ge=30, le=1800)


def workflow_reference(workflow_id: int, contact_id: int, execution_key: str) -> str:
    """The idempotency key of one execution: the same workflow, contact and execution key always give
    the same reference, and call_jobs.reference is unique, so a second job cannot exist."""
    reference = f"wf:{workflow_id}:contact:{contact_id}:{execution_key}"

    if len(reference) > MAX_REFERENCE_LENGTH:  # only a very long key: keep it deterministic and short
        digest = hashlib.sha256(execution_key.encode()).hexdigest()[:16]
        reference = f"wf:{workflow_id}:contact:{contact_id}:{digest}"

    return reference[:MAX_REFERENCE_LENGTH]


def evaluate_workflow(
    workflow: Any, contact: Any, now: datetime, *, agent: Any = None, organization: Any = None
) -> Evaluation:
    """Should `workflow` call `contact` at `now`? Reads nothing but its arguments and creates nothing.

    Pass the workflow's agent and organization to also require that the agent is active and the
    organization is not suspended (the engine always does)."""
    require_aware(now)

    def no(reason: Reason, detail: str | None = None) -> Evaluation:
        return Evaluation(due=False, eligible=False, reason=reason, detail=detail)

    if workflow.status != WorkflowStatus.ACTIVE:
        return no(Reason.WORKFLOW_INACTIVE, f"workflow status is '{workflow.status}'")

    if organization is not None and organization.status != OrganizationStatus.ACTIVE:
        return no(Reason.ORGANIZATION_INACTIVE, f"organization status is '{organization.status}'")

    if contact.organization_id != workflow.organization_id or (
        agent is not None and agent.organization_id != workflow.organization_id
    ):
        return no(Reason.ORGANIZATION_MISMATCH, "the workflow, its agent and the contact must share an organization")

    if agent is not None and agent.status != AgentStatus.ACTIVE:
        return no(Reason.AGENT_INACTIVE, f"agent status is '{agent.status}'")

    # Conditions are stored but not evaluated yet. A restriction we cannot check must stop the call,
    # not be ignored.
    if workflow.conditions:
        return no(Reason.CONDITIONS_UNSUPPORTED, "workflow conditions are not supported yet")

    try:
        action = CallAction.model_validate(workflow.action_config)
    except ValidationError as error:
        first = error.errors()[0]
        return no(Reason.ACTION_CONFIG_INVALID, f"{'.'.join(str(p) for p in first['loc']) or 'action_config'}: {first['msg']}")

    trigger = evaluate_trigger(workflow.trigger_type, workflow.trigger_config, contact.metadata_, now)

    if not trigger.due:
        return Evaluation(False, False, trigger.reason, trigger.detail, due_at=trigger.due_at)

    config = workflow.trigger_config if isinstance(workflow.trigger_config, dict) else {}
    trigger_zone = config.get("timezone", "UTC")
    eligibility = check_contact_eligibility(
        contact, now, channel=action.channel, default_timezone=trigger_zone if zone(trigger_zone) else "UTC"
    )

    if not eligibility.eligible:
        return Evaluation(True, False, eligibility.reason, eligibility.detail, trigger.execution_key, trigger.due_at)

    return Evaluation(True, True, Reason.WORKFLOW_DUE, None, trigger.execution_key, trigger.due_at, action)


class WorkflowEngine:
    def __init__(self, service: Service) -> None:
        self.service = service
        self.repo = service.repo

    async def _load(self, workflow_id: int) -> tuple[Any, Any, Any]:
        workflow = await asyncio.to_thread(self.repo.get_workflow, workflow_id)

        if workflow is None:
            raise NotFound(f"Unknown workflow {workflow_id}")

        agent = await asyncio.to_thread(self.repo.get_agent, workflow.agent_id)
        organization = await asyncio.to_thread(self.repo.get_organization, workflow.organization_id)

        if agent is None or organization is None:  # the foreign keys make this impossible: a bug
            raise NotFound(f"Workflow {workflow_id} has no agent or organization")

        return workflow, agent, organization

    async def evaluate_contacts(self, workflow_id: int, now: datetime) -> list[tuple[int, Evaluation]]:
        """Which of the organization's contacts would this workflow call at `now`? Creates nothing."""
        workflow, agent, organization = await self._load(workflow_id)
        contacts = await asyncio.to_thread(self.repo.list_contacts, workflow.organization_id)

        return [
            (contact.id, evaluate_workflow(workflow, contact, now, agent=agent, organization=organization))
            for contact in contacts
        ]

    async def run(self, workflow_id: int, now: datetime) -> list[WorkflowOutcome]:
        """Evaluate the workflow for every contact of its organization and create the call jobs that are due."""
        workflow, agent, organization = await self._load(workflow_id)
        contacts = await asyncio.to_thread(self.repo.list_contacts, workflow.organization_id)

        return [await self._process(workflow, agent, organization, contact, now) for contact in contacts]

    async def run_for_contact(self, workflow_id: int, contact_id: int, now: datetime) -> WorkflowOutcome:
        workflow, agent, organization = await self._load(workflow_id)
        contact = await asyncio.to_thread(self.repo.get_contact, contact_id)

        if contact is None:
            raise NotFound(f"Unknown contact {contact_id}")

        return await self._process(workflow, agent, organization, contact, now)

    async def _process(self, workflow: Any, agent: Any, organization: Any, contact: Any, now: datetime) -> WorkflowOutcome:
        evaluation = evaluate_workflow(workflow, contact, now, agent=agent, organization=organization)

        def outcome(reason: Reason, *, created: bool = False, job_id: str | None = None, detail: str | None = None):
            return WorkflowOutcome(
                workflow_id=workflow.id,
                contact_id=contact.id,
                due=evaluation.due,
                eligible=evaluation.eligible,
                call_job_created=created,
                reason=reason,
                job_id=job_id,
                detail=detail,
                execution_key=evaluation.execution_key,
            )

        if not evaluation.actionable:
            return outcome(evaluation.reason, detail=evaluation.detail)

        action: CallAction = evaluation.action
        reference = workflow_reference(workflow.id, contact.id, evaluation.execution_key)

        def existing_job(job: dict[str, Any]) -> WorkflowOutcome:
            """The reference already names a job. Report it as this execution's only if it really is:
            references are chosen by whoever calls the job API, so a job that merely holds ours belongs
            to someone else, and this workflow must neither claim it nor pretend its call was placed."""
            ours = (job["organization_id"], job["workflow_id"], job["contact_id"]) == (
                workflow.organization_id, workflow.id, contact.id
            )

            if not ours:
                return outcome(Reason.REFERENCE_CONFLICT, detail="the execution's reference is used by another job")

            return outcome(Reason.ALREADY_SCHEDULED, job_id=job["id"])

        # An optimisation, not the guard: this execution usually already has its job (an earlier run, or a
        # scheduler that ticks every few minutes). The guard is the unique call_jobs.reference, below.
        existing = await asyncio.to_thread(self.repo.get_job_by_reference, reference)

        if existing:
            return existing_job(existing)

        try:
            request = CallJobRequest(
                reference=reference,
                profile_id=action.profile_id,
                channel=action.channel,
                callee=Callee(name=contact.name[:80], phone=contact.phone),
                reason=action.reason,
                callback_url=action.callback_url,
                ring_timeout_seconds=action.ring_timeout_seconds,
                max_duration_seconds=action.max_duration_seconds,
                organization_id=workflow.organization_id,
                agent_id=workflow.agent_id,
                contact_id=contact.id,
                workflow_id=workflow.id,
            )
            # dispatch=False: record the job, do not ring anyone.
            view, created = await self.service.create_job(request, dispatch=False)
        except ValueError as error:
            # The service's own way of refusing a job (unknown profile, a link that does not belong
            # to the organization, an unsafe callback URL, ...). Anything else propagates.
            return outcome(Reason.CALL_JOB_REJECTED, detail=str(error))
        except Conflict:
            # "That reference was already used for a different call job": we lost a race to a job made
            # from an earlier version of this workflow (it was edited in between), or the reference is
            # held by someone else's job. Recover the job by its reference; if it cannot be found this
            # was not a duplicate and the error is real.
            existing = await asyncio.to_thread(self.repo.get_job_by_reference, reference)

            if existing is None:
                raise

            return existing_job(existing)

        if not created:  # lost a race to an identical job: the unique reference did its work
            return existing_job({**view, "id": view["job_id"]})

        log.info("workflow %s created %s for contact %s (%s)", workflow.id, view["job_id"], contact.id, evaluation.execution_key)

        return outcome(Reason.CALL_JOB_CREATED, created=True, job_id=view["job_id"])
