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
from typing import Any

import httpx

from app.brains.base import Brain
from app.config import Settings
from app.db import Repository
from app.outbound import (
    FINAL_STATUSES,
    CallJobRequest,
    UnsafeCallbackURL,
    check_callback_url,
    iso,
    job_view,
    sign_payload,
)
from app.profiles import get_profile
from app.session import OutboundState, Session, close_session, create_session, open_call
from app.store import SessionStore
from app.telephony.twilio import TwilioError, sign_stream_token, stream_twiml, to_e164

log = logging.getLogger("voice_agent")


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
        self, profile_id: str, customer_ref: str | None = None, channel: str = "web"
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

    # --- jobs (business side) --------------------------------------------

    async def create_job(self, request: CallJobRequest) -> tuple[dict[str, Any], bool]:
        profile = get_profile(request.profile_id)

        if profile is None:
            raise ValueError(f"Unknown profile {request.profile_id}")
        if profile.outbound is None:
            raise ValueError(f"Profile {request.profile_id} does not support outbound calls")

        phone = request.callee.phone

        if request.channel == "phone":
            if self.telephony is None:
                raise ValueError("Phone calls are not configured on this server")

            phone = to_e164(request.callee.phone)

            if phone is None:
                raise ValueError("Phone calls need the number in international format, e.g. +919876543210")

        if request.customer_ref:
            try:
                await self._customer(request.profile_id, request.customer_ref)
            except NotFound as error:
                raise ValueError(str(error)) from None

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
                "callee_name": request.callee.name,
                "channel": request.channel,
                "callee_phone": phone,
                "reason": request.reason,
                "callback_url": request.callback_url,
                "status": "ringing",
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
        ):
            raise Conflict("That reference was already used for a different call job")

        if created and request.channel == "phone":
            row = await self._dial(row)

        view = job_view(row)

        # A web call is answered by opening a link. (A phone call is answered by picking up.)
        if request.channel == "web":
            view["answer_url"] = f"{self.settings.public_base_url}/?job={row['id']}&token={row['answer_token']}"

        return view, created

    async def _dial(self, row: dict[str, Any]) -> dict[str, Any]:
        """Ask Twilio to ring the callee. When they answer, Twilio connects the call's
        audio to our WebSocket, carrying a token only this job's call can present."""
        ring = max(10, int(row["expires_at"] - time.time()))
        secret = self.telephony.auth_token
        twiml = stream_twiml(
            self.telephony.ws_url(),
            {"kind": "job", "job_id": row["id"], "token": sign_stream_token(secret, "job", row["id"], ring + 60)},
        )

        try:
            sid = await self.telephony.twilio.create_call(
                row["callee_phone"], twiml, self.telephony.status_url(row["id"]), ring
            )
        except TwilioError as error:
            log.warning("could not place call for %s: %s", row["id"], error)
            await self._finish_without_call(row["id"], "failed", reason="telephony_error", cancel_line=False)
        else:
            await self._db(self.repo.update_job, row["id"], twilio_call_sid=sid)

        return await self._db(self.repo.get_job, row["id"])

    async def get_job(self, job_id: str) -> dict[str, Any]:
        row = await self._fresh_job(job_id)

        if row is None:
            raise NotFound("Unknown job")

        return job_view(row, await self._result(row))

    async def list_jobs(self, limit: int) -> list[dict[str, Any]]:
        return [job_view(row) for row in await self._db(self.repo.list_jobs, limit)]

    async def list_calls(self, limit: int, direction: str | None) -> list[dict[str, Any]]:
        return await self._db(self.repo.list_results, limit, direction)

    async def get_result(self, call_id: str) -> dict[str, Any]:
        result = await self._db(self.repo.get_result, call_id)

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

    async def answer_job_phone(self, job_id: str, call_sid: str) -> tuple[Session, str]:
        """The callee picked up and Twilio has connected the audio."""
        row = await self._fresh_job(job_id)

        if row is None or row.get("channel") != "phone":
            raise NotFound("Unknown job")

        return await self._answer(row, "phone", call_sid)

    async def _answer(self, row: dict[str, Any], channel: str, call_sid: str | None = None) -> tuple[Session, str]:
        job_id = row["id"]

        if row["status"] != "ringing":
            raise Conflict("This call is no longer available")
        if self.brain is None:
            raise Unavailable("No LLM is configured on the server")

        profile = get_profile(row["profile_id"])
        extra: dict[str, Any] = {}

        if row["customer_ref"]:
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
                organisation=profile.outbound.organisation,
                max_duration_seconds=row["max_duration_seconds"],
            ),
            **extra,
        )
        session.channel = channel
        self._bind_customer(session)
        self.store.add(session)  # may raise TooManySessions before anything changes

        fields: dict[str, Any] = {"answered_at": time.time(), "call_id": session.id}

        if call_sid:
            fields["twilio_call_sid"] = call_sid

        won = await self._db(self.repo.transition, job_id, ["ringing"], "in_progress", **fields)

        if not won:  # someone else answered, or it expired, in the meantime
            self.store.remove(session.id)
            raise Conflict("This call is no longer available")

        return session, open_call(session)

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

        if won and row["callback_url"]:
            self._schedule_delivery(job_id)

    async def _finish_without_call(
        self, job_id: str, status: str, reason: str | None = None, cancel_line: bool = True
    ) -> bool:
        row = await self._db(self.repo.get_job, job_id)
        won = await self._db(
            self.repo.transition,
            job_id,
            ["ringing"],
            status,
            finished_at=time.time(),
            end_reason=reason or status,
            callback_status="pending" if row and row["callback_url"] else "none",
        )

        if won and row["callback_url"]:
            self._schedule_delivery(job_id)

        # We gave up (timed out, declined) but the phone may still be ringing.
        if won and cancel_line and row.get("channel") == "phone" and row.get("twilio_call_sid") and self.telephony:
            try:
                await self.telephony.twilio.hang_up(row["twilio_call_sid"])
            except TwilioError:
                log.warning("could not stop the ringing call for %s", job_id)

        return won

    async def handle_twilio_status(self, job_id: str, params: dict[str, str]) -> None:
        """Twilio tells us how the call is going. Only the endings matter here: the
        moment of answering is seen when the audio connects."""
        row = await self._db(self.repo.get_job, job_id)
        status = params.get("CallStatus", "")

        if row is None or row.get("channel") != "phone":
            return

        sid = params.get("CallSid")

        if row.get("twilio_call_sid") and sid and row["twilio_call_sid"] != sid:
            return  # not the call we placed

        if status in ("busy", "no-answer", "canceled"):
            await self._finish_without_call(job_id, "no_answer", reason=status.replace("-", "_"), cancel_line=False)
        elif status == "failed":
            await self._finish_without_call(job_id, "failed", reason="telephony_error", cancel_line=False)
        elif status == "completed" and row["status"] == "in_progress":
            # Twilio says the line is gone. Normally the audio stream has already told us.
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
