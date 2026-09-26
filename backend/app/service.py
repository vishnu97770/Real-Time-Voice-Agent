"""Calls and outbound call jobs: the layer between HTTP and the session.

The session is the runtime for one live call. This service owns everything that
outlives it: creating jobs, letting a callee answer or decline, expiring calls
nobody answered, saving results, and delivering signed result callbacks.
"""

import asyncio
import hmac
import json
import logging
import secrets
import time
from collections.abc import Sequence
from datetime import datetime, timezone
from typing import Any

import httpx
from sqlalchemy.exc import IntegrityError, OperationalError

from app.brains.base import Brain
from app.config import Settings
from app.db import Repository
from app.models.enums import AgentStatus
from app.outbound import (
    FINAL_STATUSES,
    SCHEDULED,
    CallJobRequest,
    UnsafeCallbackURL,
    check_callback_url,
    iso,
    job_log_tag,
    job_view,
    sign_payload,
)
from app.profiles import get_profile
from app.schemas import (
    AgentCreate,
    AgentResponse,
    AgentUpdate,
    ContactCreate,
    ContactResponse,
    ContactUpdate,
    OrganizationResponse,
    WorkflowCreate,
    WorkflowResponse,
    WorkflowUpdate,
)
from app.session import OutboundState, Session, close_session, create_session, open_call
from app.store import SessionStore
from app.voice_context import VoiceContextError, VoiceSessionContext, build_voice_context, is_domain_job
from app.phone import to_e164
from app.telephony.base import CallEvent, CallEventKind, PlaceCall, ProviderCall, TelephonyError

log = logging.getLogger("voice_agent")

ORPHANED_JOB_SLACK_SECONDS = 60


class NotFound(Exception):
    pass


class Conflict(Exception):
    pass


class Unavailable(Exception):
    pass


class Service:
    def __init__(
        self,
        *,
        repo: Repository,
        store: SessionStore,
        brain: Brain | None,
        settings: Settings,
        http: httpx.AsyncClient | None = None,
        telephony: Any = None,
    ) -> None:
        self.telephony = telephony
        self.repo = repo
        self.store = store
        self.brain = brain
        self.settings = settings
        self.http = http or httpx.AsyncClient()
        self._tasks: set[asyncio.Task] = set()
        self._finalizing: dict[str, asyncio.Task] = {}

    async def _db(self, fn, *args, **kwargs):
        return await asyncio.to_thread(fn, *args, **kwargs)

    # --- inbound calls ----------------------------------------------------

    async def _customer(self, profile_id: str, ref: str) -> dict[str, Any]:
        customer = await self._db(self.repo.get_customer, profile_id, ref)

        if customer is None:
            raise NotFound(f"Unknown customer {ref} for profile {profile_id}")

        return customer

    def _bind_customer(self, session: Session) -> None:
        """Write a confirmed action's change back to the customer's record."""
        if session.customer_ref is None:
            return

        profile_id, ref = session.profile.id, session.customer_ref
        session.persist = lambda data: self._db(self.repo.save_customer_data, profile_id, ref, data)

    async def start_inbound(
        self, profile_id: str, customer_ref: str | None = None, channel: str = "web", organization_id: int | None = None
    ) -> tuple[Session, str]:
        if self.brain is None:
            raise Unavailable("No LLM is configured on the server")

        profile = get_profile(profile_id)

        if profile is None:
            raise NotFound(f"Unknown profile {profile_id}")

        extra: dict[str, Any] = {}

        if customer_ref:
            customer = await self._customer(profile_id, customer_ref)
            extra = {"data": customer["data"], "customer_ref": customer_ref, "customer_name": customer["display_name"]}

        session = create_session(profile, self.brain, self.settings.brain_timeout_seconds, **extra)
        session.channel = channel
        session.organization_id = organization_id
        self._bind_customer(session)
        self.store.add(session)

        return session, open_call(session)

    # --- customers (their data, as the agent sees it) ------------------------

    async def list_customers(self, profile_id: str) -> list[dict[str, Any]]:
        return await self._db(self.repo.list_customers, profile_id)

    async def get_customer(self, profile_id: str, ref: str) -> dict[str, Any]:
        return await self._customer(profile_id, ref)

    async def put_customer(self, profile_id: str, ref: str, display_name: str, data: Any) -> None:
        profile = get_profile(profile_id)

        if profile is None:
            raise NotFound(f"Unknown profile {profile_id}")

        problems = profile.check_customer_data(data)

        if problems:
            raise ValueError("Invalid customer data: " + "; ".join(problems))

        await self._db(self.repo.upsert_customer, profile_id, ref, display_name, data)

    # --- organizations and agents (Step 18B: read-only organizations, full agent CRUD) -----

    async def list_organizations(self, user_organization_id: int | None) -> list[dict[str, Any]]:
        """The organizations the signed-in operator belongs to. Today a user has at most one
        (UserRow.organization_id is a single column, not a membership table), so this is a list
        of zero or one - kept as a list so a future multi-organization membership model would not
        need this endpoint's shape to change."""
        if user_organization_id is None:
            return []

        organization = await self._db(self.repo.get_organization, user_organization_id)
        return [OrganizationResponse.model_validate(organization).model_dump(mode="json")] if organization else []

    async def get_organization(self, organization_id: int, user_organization_id: int | None) -> dict[str, Any]:
        # Same "not found" for "does not exist" and "not yours": telling those apart would let
        # one organization discover another's ids by probing.
        if user_organization_id is None or organization_id != user_organization_id:
            raise NotFound("Unknown organization")

        organization = await self._db(self.repo.get_organization, organization_id)

        if organization is None:
            raise NotFound("Unknown organization")

        return OrganizationResponse.model_validate(organization).model_dump(mode="json")

    async def list_agents(self, organization_id: int | None) -> list[dict[str, Any]]:
        if organization_id is None:
            return []

        agents = await self._db(self.repo.list_agents, organization_id)
        return [AgentResponse.model_validate(agent).model_dump(mode="json") for agent in agents]

    async def get_agent(self, agent_id: int, organization_id: int | None) -> dict[str, Any]:
        agent = await self._db(self.repo.get_agent, agent_id) if organization_id is not None else None

        if agent is None or agent.organization_id != organization_id:
            raise NotFound("Unknown agent")

        return AgentResponse.model_validate(agent).model_dump(mode="json")

    async def create_agent(self, organization_id: int | None, request: AgentCreate) -> dict[str, Any]:
        if organization_id is None:
            raise Conflict("Your account is not part of an organization yet")

        values = request.model_dump()
        # A saved, fully-configured agent is meant to be used, not left in the schema's default
        # draft state; nothing in this step adds a lifecycle UI, so this is the one place status
        # is decided.
        values["status"] = AgentStatus.ACTIVE

        try:
            agent = await self._db(self.repo.create_agent, organization_id, values)
        except IntegrityError:
            raise Conflict(f"An agent named {request.name!r} already exists") from None

        return AgentResponse.model_validate(agent).model_dump(mode="json")

    async def update_agent(self, agent_id: int, organization_id: int | None, request: AgentUpdate) -> dict[str, Any]:
        if organization_id is None:
            raise NotFound("Unknown agent")

        fields = request.model_dump(exclude_unset=True)

        try:
            agent = await self._db(self.repo.update_agent, agent_id, organization_id, fields)
        except IntegrityError:
            raise Conflict(f"An agent named {fields.get('name')!r} already exists") from None

        if agent is None:
            raise NotFound("Unknown agent")

        return AgentResponse.model_validate(agent).model_dump(mode="json")

    # --- contacts (Step 18C: full CRUD, organization-scoped like agents) --------------------

    async def list_contacts(self, organization_id: int | None) -> list[dict[str, Any]]:
        if organization_id is None:
            return []

        contacts = await self._db(self.repo.list_contacts, organization_id)
        return [ContactResponse.model_validate(contact).model_dump(mode="json") for contact in contacts]

    async def get_contact(self, contact_id: int, organization_id: int | None) -> dict[str, Any]:
        contact = await self._db(self.repo.get_contact, contact_id) if organization_id is not None else None

        if contact is None or contact.organization_id != organization_id:
            raise NotFound("Unknown contact")

        return ContactResponse.model_validate(contact).model_dump(mode="json")

    async def create_contact(self, organization_id: int | None, request: ContactCreate) -> dict[str, Any]:
        if organization_id is None:
            raise Conflict("Your account is not part of an organization yet")

        contact = await self._db(self.repo.create_contact, organization_id, request.model_dump())
        return ContactResponse.model_validate(contact).model_dump(mode="json")

    async def update_contact(self, contact_id: int, organization_id: int | None, request: ContactUpdate) -> dict[str, Any]:
        if organization_id is None:
            raise NotFound("Unknown contact")

        fields = request.model_dump(exclude_unset=True)
        contact = await self._db(self.repo.update_contact, contact_id, organization_id, fields)

        if contact is None:
            raise NotFound("Unknown contact")

        return ContactResponse.model_validate(contact).model_dump(mode="json")

    async def delete_contact(self, contact_id: int, organization_id: int | None) -> None:
        if organization_id is None:
            raise NotFound("Unknown contact")

        try:
            deleted = await self._db(self.repo.delete_contact, contact_id, organization_id)
        except IntegrityError:
            raise Conflict("This contact has call jobs and cannot be deleted") from None

        if not deleted:
            raise NotFound("Unknown contact")

    # --- workflows (Step 18D: CRUD; Step 18E: eligibility + manual trigger, both reusing the ---
    # --- existing WorkflowEngine - see app/workflows/engine.py, not duplicated here) -----------

    async def list_workflows(self, organization_id: int | None) -> list[dict[str, Any]]:
        if organization_id is None:
            return []

        workflows = await self._db(self.repo.list_workflows, organization_id)
        return [WorkflowResponse.model_validate(workflow).model_dump(mode="json") for workflow in workflows]

    async def get_workflow(self, workflow_id: int, organization_id: int | None) -> dict[str, Any]:
        workflow = await self._db(self.repo.get_workflow, workflow_id) if organization_id is not None else None

        if workflow is None or workflow.organization_id != organization_id:
            raise NotFound("Unknown workflow")

        return WorkflowResponse.model_validate(workflow).model_dump(mode="json")

    async def create_workflow(self, organization_id: int | None, request: WorkflowCreate) -> dict[str, Any]:
        if organization_id is None:
            raise Conflict("Your account is not part of an organization yet")

        # The database's own composite foreign key (agent_id, organization_id) would refuse this
        # too, but as an IntegrityError indistinguishable from "that name is already taken" (both
        # raise the same exception type) - checking first, the same way create_job already does
        # for a job's own agent/contact/workflow links, gives a specific, readable reason.
        problem = await self._db(self.repo.job_link_problem, organization_id, request.agent_id, None, None)

        if problem:
            raise ValueError(problem)

        try:
            workflow = await self._db(self.repo.create_workflow, organization_id, request.model_dump())
        except IntegrityError:
            raise Conflict(f"A workflow named {request.name!r} already exists") from None

        return WorkflowResponse.model_validate(workflow).model_dump(mode="json")

    async def update_workflow(self, workflow_id: int, organization_id: int | None, request: WorkflowUpdate) -> dict[str, Any]:
        if organization_id is None:
            raise NotFound("Unknown workflow")

        fields = request.model_dump(exclude_unset=True)

        if fields.get("agent_id") is not None:
            problem = await self._db(self.repo.job_link_problem, organization_id, fields["agent_id"], None, None)

            if problem:
                raise ValueError(problem)

        try:
            workflow = await self._db(self.repo.update_workflow, workflow_id, organization_id, fields)
        except IntegrityError:
            raise Conflict(f"A workflow named {fields.get('name')!r} already exists") from None

        if workflow is None:
            raise NotFound("Unknown workflow")

        return WorkflowResponse.model_validate(workflow).model_dump(mode="json")

    async def delete_workflow(self, workflow_id: int, organization_id: int | None) -> None:
        if organization_id is None:
            raise NotFound("Unknown workflow")

        try:
            deleted = await self._db(self.repo.delete_workflow, workflow_id, organization_id)
        except IntegrityError:
            raise Conflict("This workflow has call jobs and cannot be deleted") from None

        if not deleted:
            raise NotFound("Unknown workflow")

    async def _owned_workflow_and_contact(self, workflow_id: int, contact_id: int, organization_id: int | None) -> tuple[Any, Any]:
        """Both, only if they belong to the caller's own organization - the one check that must
        happen before the (organization-blind) engine is ever called with these ids. Same
        not-found-either-way rule as everywhere else in this tenant boundary."""
        if organization_id is None:
            raise NotFound("Unknown workflow")

        workflow = await self._db(self.repo.get_workflow, workflow_id)

        if workflow is None or workflow.organization_id != organization_id:
            raise NotFound("Unknown workflow")

        contact = await self._db(self.repo.get_contact, contact_id)

        if contact is None or contact.organization_id != organization_id:
            raise NotFound("Unknown contact")

        return workflow, contact

    async def check_workflow_eligibility(self, workflow_id: int, contact_id: int, organization_id: int | None) -> dict[str, Any]:
        """Pure: the same judgment WorkflowEngine.run would make for this contact, without
        creating anything. Deferred import: app.workflows.engine imports this module (Service),
        so the two cannot import each other at module load time."""
        from app.workflows.engine import evaluate_workflow

        workflow, contact = await self._owned_workflow_and_contact(workflow_id, contact_id, organization_id)
        agent = await self._db(self.repo.get_agent, workflow.agent_id)
        organization = await self._db(self.repo.get_organization, organization_id)
        evaluation = evaluate_workflow(workflow, contact, datetime.now(timezone.utc), agent=agent, organization=organization)

        return {
            "workflow_id": workflow_id,
            "contact_id": contact_id,
            "due": evaluation.due,
            "eligible": evaluation.eligible,
            "reason": evaluation.reason.value,
            "detail": evaluation.detail,
            "execution_key": evaluation.execution_key,
            "due_at": evaluation.due_at.isoformat() if evaluation.due_at else None,
        }

    async def trigger_workflow(self, workflow_id: int, contact_id: int, organization_id: int | None) -> dict[str, Any]:
        """The same run WorkflowEngine.run/the scheduler would do for this one contact, on demand:
        eligibility, consent, the contact's window, idempotency (call_jobs.reference) and the
        "record only, do not dial" job creation are all the engine's own, unchanged. A refusal
        (not due, not eligible, already scheduled) is a normal 200 result, never an error - the
        engine's own convention (see app/workflows/results.py)."""
        from app.workflows.engine import WorkflowEngine

        await self._owned_workflow_and_contact(workflow_id, contact_id, organization_id)
        outcome = await WorkflowEngine(self).run_for_contact(workflow_id, contact_id, datetime.now(timezone.utc))

        return outcome.as_dict()

    # --- jobs (business side) --------------------------------------------

    async def create_job(self, request: CallJobRequest, *, dispatch: bool = True) -> tuple[dict[str, Any], bool]:
        """Create a job (or return the existing one for the same reference).

        With dispatch=False the job is only recorded, as "scheduled": nothing is dialed, no answer
        link is issued and it cannot expire, so a caller such as the workflow engine can create jobs
        without placing calls. Whatever places it later must move it to "ringing" and reset
        expires_at. The default is the behaviour every existing caller relies on."""
        profile = get_profile(request.profile_id)

        if profile is None:
            raise ValueError(f"Unknown profile {request.profile_id}")
        if profile.outbound is None:
            raise ValueError(f"Profile {request.profile_id} does not support outbound calls")

        phone = request.callee.phone

        if request.channel == "phone":
            if dispatch and self.telephony is None:
                raise ValueError("Phone calls are not configured on this server")

            phone = to_e164(request.callee.phone)

            if phone is None:
                raise ValueError("Phone calls need the number in international format, e.g. +919876543210")

        if request.customer_ref:
            try:
                await self._customer(request.profile_id, request.customer_ref)
            except NotFound as error:
                raise ValueError(str(error)) from None

        if request.organization_id is not None:
            problem = await self._db(
                self.repo.job_link_problem,
                request.organization_id,
                request.agent_id,
                request.contact_id,
                request.workflow_id,
            )

            if problem:
                raise ValueError(problem)

        if request.callback_url:
            if not (self.settings.webhook_secret or self.settings.api_key):
                raise ValueError(
                    "Result callbacks are signed, so set VOICE_AGENT_WEBHOOK_SECRET (or an API key) first"
                )

            await check_callback_url(request.callback_url, self.settings.allow_private_callbacks)

        now = time.time()
        row, created = await self._db(
            self.repo.create_job,
            {
                "id": f"JOB-{secrets.token_hex(6).upper()}",
                "reference": request.reference,
                "profile_id": request.profile_id,
                "customer_ref": request.customer_ref,
                "organization_id": request.organization_id,
                "agent_id": request.agent_id,
                "contact_id": request.contact_id,
                "workflow_id": request.workflow_id,
                "callee_name": request.callee.name,
                "channel": request.channel,
                "callee_phone": phone,
                "reason": request.reason,
                "callback_url": request.callback_url,
                "status": "ringing" if dispatch else SCHEDULED,
                "answer_token": secrets.token_urlsafe(16),
                "created_at": now,
                "expires_at": now + request.ring_timeout_seconds,
                "max_duration_seconds": request.max_duration_seconds,
                "callback_status": "none",
                "callback_attempts": 0,
            },
        )

        if not created and (
            row["profile_id"] != request.profile_id
            or row["callee_name"] != request.callee.name
            or row["reason"] != request.reason
            or (row["organization_id"], row["agent_id"], row["contact_id"], row["workflow_id"])
            != (request.organization_id, request.agent_id, request.contact_id, request.workflow_id)
        ):
            raise Conflict("That reference was already used for a different call job")

        if created and dispatch and request.channel == "phone":
            row = await self._dial(row)

        view = job_view(row)

        # A web call is answered by opening a link. (A phone call is answered by picking up.)
        # (By the job's status, not by `dispatch`: a repeated reference can return a scheduled job that
        # some other caller recorded, and a scheduled job has nothing to answer yet.)
        if request.channel == "web" and row["status"] != SCHEDULED:
            view["answer_url"] = f"{self.settings.public_base_url}/?job={row['id']}&token={row['answer_token']}"

        return view, created

    async def _dial(self, row: dict[str, Any]) -> dict[str, Any]:
        """Ask the telephony provider to ring the callee. How the call is placed, and how its audio
        reaches us when they answer, is the provider adapter's business."""
        ring = max(10, int(row["expires_at"] - time.time()))

        try:
            call = await self.telephony.adapter.place_call(
                PlaceCall(job_id=row["id"], to=row["callee_phone"], ring_seconds=ring)
            )
        except TelephonyError as error:
            log.warning("could not place call for %s: %s", row["id"], error)
            await self._finish_without_call(row["id"], "failed", reason="telephony_error", cancel_line=False)
        else:
            # (The column is still called twilio_call_sid: it holds the provider's call id.)
            await self._db(self.repo.update_job, row["id"], twilio_call_sid=call.provider_call_id)

        return await self._db(self.repo.get_job, row["id"])

    async def get_job(self, job_id: str, organization_id: int | None = None) -> dict[str, Any]:
        """With `organization_id`, a job of another organization is "unknown", the same answer as a job
        that does not exist, so ids cannot be probed across organizations."""
        row = await self._fresh_job(job_id)

        if row is None or (organization_id is not None and row.get("organization_id") != organization_id):
            raise NotFound("Unknown job")

        return job_view(row, await self._result(row))

    async def list_jobs(self, limit: int, organization_id: int | None = None) -> list[dict[str, Any]]:
        return [job_view(row) for row in await self._db(self.repo.list_jobs, limit, organization_id)]

    async def list_calls(
        self, limit: int, direction: str | None, organization_id: int | None = None
    ) -> list[dict[str, Any]]:
        return await self._db(self.repo.list_results, limit, direction, organization_id)

    async def get_result(self, call_id: str, organization_id: int | None = None) -> dict[str, Any]:
        result = await self._db(self.repo.get_result, call_id, organization_id)

        if result is None:
            raise NotFound("No result for that call")

        return result

    # --- jobs (callee side, authorised by the answer token) ---------------

    async def _authorised_job(self, job_id: str, token: str) -> dict[str, Any]:
        row = await self._fresh_job(job_id)

        # Same answer for "no such job" and "wrong token": don't confirm a job exists.
        if row is None or not hmac.compare_digest(row["answer_token"], token):
            raise NotFound("Unknown job")

        return row

    async def ring_info(self, job_id: str, token: str) -> dict[str, Any]:
        row = await self._authorised_job(job_id, token)
        profile = get_profile(row["profile_id"])

        return {
            "status": row["status"],
            "profile_id": row["profile_id"],
            "organisation": profile.outbound.organisation,
            "seconds_left": max(0, int(row["expires_at"] - time.time())) if row["status"] == "ringing" else 0,
        }

    async def answer_job(self, job_id: str, token: str) -> tuple[Session, str]:
        row = await self._authorised_job(job_id, token)

        if (row.get("channel") or "web") != "web":
            raise NotFound("Unknown job")  # a phone call is answered by phone, not by link

        return await self._answer(row, "web")

    async def answer_job_phone(self, job_id: str, provider_call_id: str) -> tuple[Session, str]:
        """The callee picked up and the provider has connected the audio."""
        row = await self._fresh_job(job_id)

        if row is None or row.get("channel") != "phone":
            raise NotFound("Unknown job")

        return await self._answer(row, "phone", provider_call_id)

    async def _answer(
        self, row: dict[str, Any], channel: str, provider_call_id: str | None = None
    ) -> tuple[Session, str]:
        job_id = row["id"]

        if row["status"] != "ringing":
            raise Conflict("This call is no longer available")
        if self.brain is None:
            raise Unavailable("No LLM is configured on the server")

        profile = get_profile(row["profile_id"])
        extra: dict[str, Any] = {}
        context: VoiceSessionContext | None = None

        if is_domain_job(row):
            # An automated call for an organization's agent and contact: it is conducted from those records,
            # not from the profile's built-in demo data.
            context = await self._voice_context(row)
        elif row["customer_ref"]:
            customer = await self._customer(row["profile_id"], row["customer_ref"])
            extra = {
                "data": customer["data"],
                "customer_ref": row["customer_ref"],
                "customer_name": customer["display_name"],
            }

        session = create_session(
            profile,
            self.brain,
            self.settings.brain_timeout_seconds,
            OutboundState(
                job_id=job_id,
                callee_name=row["callee_name"],
                reason=row["reason"],
                organisation=context.organization.name if context else profile.outbound.organisation,
                max_duration_seconds=row["max_duration_seconds"],
            ),
            context=context,
            **extra,
        )
        session.channel = channel
        self._bind_customer(session)
        self.store.add(session)  # may raise TooManySessions before anything changes

        fields: dict[str, Any] = {"answered_at": time.time(), "call_id": session.id}

        if provider_call_id:
            fields["twilio_call_sid"] = provider_call_id

        won = await self._db(self.repo.transition, job_id, ["ringing"], "in_progress", **fields)

        if not won:  # someone else answered, or it expired, in the meantime
            self.store.remove(session.id)
            raise Conflict("This call is no longer available")

        # The ids that tie this call's records together: our call id, and the provider's (never a phone number).
        provider = f" provider={self.telephony.adapter.name} provider_call={provider_call_id}" if provider_call_id and self.telephony else ""
        log.info("job answered %s channel=%s call=%s%s", job_log_tag(row), channel, session.id, provider)
        return session, open_call(session)

    async def _voice_context(self, row: dict[str, Any]) -> VoiceSessionContext:
        """The context an automated call is conducted from, built from the job's own records.

        If a record is missing or the records do not belong together there is nobody coherent to speak as
        or to, so no session is started: the job ends failed (through the ordinary path, with its callback)
        and the caller gets a controlled error, which hangs up the phone line."""
        organization = await self._db(self.repo.get_organization, row["organization_id"])
        agent = await self._db(self.repo.get_agent, row["agent_id"])
        contact = await self._db(self.repo.get_contact, row["contact_id"])
        workflow = await self._db(self.repo.get_workflow, row["workflow_id"]) if row["workflow_id"] is not None else None

        try:
            context = build_voice_context(row, organization, agent, contact, workflow)
        except VoiceContextError as error:
            log.warning("no voice context for %s: %s", job_log_tag(row), error)
            await self._finish_without_call(row["id"], "failed", reason="domain_context_invalid", cancel_line=False)
            raise Unavailable(
                "This call cannot be conducted: the agent, contact or workflow it was scheduled for is missing or inconsistent"
            ) from None

        log.info("voice context built %s", job_log_tag(row))
        return context

    async def decline_job(self, job_id: str, token: str) -> None:
        await self._authorised_job(job_id, token)
        await self._finish_without_call(job_id, "declined")

    # --- finishing --------------------------------------------------------

    def run_detached(self, coro) -> asyncio.Task:
        """Run something that must finish even if whoever asked for it is cancelled
        (a dropped connection, a shutdown). The task is kept so shutdown can wait for it."""
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def finalize(self, session: Session) -> dict[str, Any]:
        """Close a call, save its result and, if it was a job, finish the job.

        Safe to call more than once and from several places at once (the hang-up,
        the agent ending the call, the sweeper): they share one run. And once
        started it always completes: a call's result must not be lost because the
        connection that reported the hang-up was cancelled half-way through."""
        task = self._finalizing.get(session.id)

        if task is None:
            task = self.run_detached(self._finalize(session))
            self._finalizing[session.id] = task
            task.add_done_callback(lambda _done, sid=session.id: self._finalizing.pop(sid, None))

        return await asyncio.shield(task)

    async def _finalize(self, session: Session) -> dict[str, Any]:
        summary = close_session(session)

        # Who the result belongs to, for a console call opened by an organization's own account.
        if session.organization_id is not None:
            summary["organization_id"] = session.organization_id

        # A phone line stays open until we say otherwise, whoever ended the call.
        if session.hangup is not None:
            hangup, session.hangup = session.hangup, None

            try:
                await hangup()
            except Exception:
                session.log("hangup_failed")

        if not session.saved:
            session.saved = True
            await self._db(self.repo.save_result, summary)

        if session.outbound:
            await self._finish_job(session)

        return summary

    async def _finish_job(self, session: Session) -> None:
        job_id = session.outbound.job_id
        row = await self._db(self.repo.get_job, job_id)
        status = "wrong_party" if session.outbound.identity == "wrong_party" else "completed"

        won = await self._db(
            self.repo.transition,
            job_id,
            ["in_progress"],
            status,
            finished_at=time.time(),
            end_reason=session.end_reason,
            callback_status="pending" if row["callback_url"] else "none",
        )

        if won:
            sid = f" provider_call={row['twilio_call_sid']}" if row.get("twilio_call_sid") else ""
            log.info("job finished %s status=%s end_reason=%s call=%s%s", job_log_tag(row), status, session.end_reason, session.id, sid)

        if won and row["callback_url"]:
            self._schedule_delivery(job_id)

    async def _finish_without_call(
        self,
        job_id: str,
        status: str,
        reason: str | None = None,
        cancel_line: bool = True,
        from_statuses: Sequence[str] = ("ringing",),
    ) -> bool:
        """End a job that never became a conversation, with the callback every finished job gets.
        `from_statuses` is the state it must still be in; a dispatcher passes ("scheduled",) to close
        a job that was never placed."""
        row = await self._db(self.repo.get_job, job_id)
        won = await self._db(
            self.repo.transition,
            job_id,
            list(from_statuses),
            status,
            finished_at=time.time(),
            end_reason=reason or status,
            callback_status="pending" if row and row["callback_url"] else "none",
        )

        if won:
            log.info("job closed without a call %s status=%s reason=%s", job_log_tag(row), status, reason or status)

        if won and row["callback_url"]:
            self._schedule_delivery(job_id)

        # We gave up (timed out, declined) but the phone may still be ringing.
        if won and cancel_line and row.get("channel") == "phone" and row.get("twilio_call_sid") and self.telephony:
            try:
                await self.telephony.adapter.hang_up(
                    ProviderCall(self.telephony.adapter.name, row["twilio_call_sid"])
                )
            except TelephonyError:
                log.warning("could not stop the ringing call for %s", job_id)

        return won

    async def handle_call_event(self, event: CallEvent) -> None:
        """The provider tells us how a call is going (already authenticated and read by its adapter). Only the
        endings matter here: the moment of answering is seen when the audio connects."""
        job_id = event.job_id
        row = await self._db(self.repo.get_job, job_id)

        if row is None or row.get("channel") != "phone":
            return

        if row.get("twilio_call_sid") and event.provider_call_id and row["twilio_call_sid"] != event.provider_call_id:
            return  # not the call we placed

        if event.kind in (CallEventKind.NO_ANSWER, CallEventKind.BUSY):
            await self._finish_without_call(job_id, "no_answer", reason=event.reason or event.kind.value, cancel_line=False)
        elif event.kind == CallEventKind.FAILED:
            await self._finish_without_call(job_id, "failed", reason=event.reason or "telephony_error", cancel_line=False)
        elif event.kind == CallEventKind.COMPLETED and row["status"] == "in_progress":
            # The provider says the line is gone. Normally the audio stream has already told us.
            for session in self.store.all():
                if session.outbound and session.outbound.job_id == job_id and not session.ended:
                    session.end_reason = "hangup"
                    await self.finalize(session)

    async def _fresh_job(self, job_id: str) -> dict[str, Any] | None:
        """The job, with a ring timeout applied if it is due."""
        row = await self._db(self.repo.get_job, job_id)

        if row and row["status"] == "ringing" and row["expires_at"] <= time.time():
            await self._finish_without_call(job_id, "no_answer")
            row = await self._db(self.repo.get_job, job_id)

        return row

    async def _result(self, row: dict[str, Any]) -> dict[str, Any] | None:
        if row["status"] not in FINAL_STATUSES:
            return None

        if row["call_id"]:
            saved = await self._db(self.repo.get_result, row["call_id"])

            if saved:
                return saved

        # Nobody talked (declined, or nobody answered): a result all the same.
        return {
            "outcome": row["status"],
            "direction": "outbound",
            "disclosure_given": False,
            "transcript": [],
            "audit": [{"at": iso(row["finished_at"]), "type": f"call_{row['status']}"}],
        }

    # --- background -------------------------------------------------------

    async def sweep(self) -> None:
        """Ring timeouts, and calls whose caller vanished without hanging up."""
        for job_id in await self._db(self.repo.due_ringing, time.time()):
            await self._finish_without_call(job_id, "no_answer")

        now = time.time()

        # A live call is a session in memory, so a job that is still in_progress well past its time limit
        # with no session behind it lost that session (a restart, or the store's own expiry): nothing else
        # will ever end it. The limit + 30 s is the longest the loop below lets a call run; the slack on top
        # keeps this clear of a call that is being finalized right now.
        for job_id, call_id in await self._db(self.repo.due_in_progress, now, ORPHANED_JOB_SLACK_SECONDS):
            if call_id and self.store.get(call_id) is not None:
                continue

            log.warning("job lost its call %s: no live session, ending it", job_id)
            await self._finish_without_call(job_id, "failed", reason="session_lost", from_statuses=("in_progress",))

        for session in self.store.all():
            if not session.outbound or session.ended:
                continue

            if session.elapsed() > session.outbound.max_duration_seconds + 30:
                session.end_reason = "time_limit"
            elif now - session.last_active > self.settings.call_idle_timeout_seconds:
                session.end_reason = "idle"
            else:
                continue

            await self.finalize(session)

    async def run_sweeper(self) -> None:
        while True:
            await asyncio.sleep(self.settings.sweep_interval_seconds)

            try:
                await self.sweep()
            except OperationalError as error:  # an outage: one line per cycle, not a traceback
                log.warning("sweep skipped, database unavailable (%s)", type(error.orig).__name__)
            except Exception:  # the sweeper must outlive a bad row
                log.exception("sweep failed")

    async def resume_callbacks(self) -> None:
        """After a restart, deliver results that were still waiting to be sent."""
        for job_id in await self._db(self.repo.pending_callbacks):
            self._schedule_delivery(job_id)

    def _schedule_delivery(self, job_id: str) -> None:
        task = asyncio.create_task(self._deliver(job_id))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def drain(self) -> None:
        """Wait for in-flight callback deliveries (used on shutdown and in tests)."""
        while self._tasks:
            batch = list(self._tasks)
            await asyncio.gather(*batch, return_exceptions=True)
            # gather() completes without yielding when every task is already done,
            # so the done-callbacks that empty `_tasks` would never get to run and
            # this loop would spin forever. Drop the finished tasks ourselves.
            self._tasks.difference_update(batch)

    async def _deliver(self, job_id: str) -> None:
        secret = self.settings.webhook_secret or self.settings.api_key or ""
        attempts = self.settings.callback_attempts

        for attempt in range(1, attempts + 1):
            row = await self._db(self.repo.get_job, job_id)

            try:
                await check_callback_url(row["callback_url"], self.settings.allow_private_callbacks)
            except UnsafeCallbackURL:
                await self._db(self.repo.update_job, job_id, callback_status="failed")
                return

            body = json.dumps(
                {"event": "call_job.finished", "job": job_view(row, await self._result(row))},
                default=str,
            ).encode()
            signature, timestamp = sign_payload(secret, body)

            try:
                response = await self.http.post(
                    row["callback_url"],
                    content=body,
                    headers={
                        "Content-Type": "application/json",
                        "X-Signature": signature,
                        "X-Timestamp": timestamp,
                        "X-Job-Id": job_id,
                    },
                    timeout=5.0,
                    follow_redirects=False,
                )
                delivered = 200 <= response.status_code < 300
            except httpx.HTTPError:
                delivered = False

            await self._db(
                self.repo.update_job,
                job_id,
                callback_attempts=attempt,
                callback_status="delivered" if delivered else "pending",
            )

            if delivered:
                return

            if attempt < attempts:
                await asyncio.sleep(self.settings.callback_backoff_seconds * attempt)

        await self._db(self.repo.update_job, job_id, callback_status="failed")
