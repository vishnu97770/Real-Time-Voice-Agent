import asyncio
import hmac
import json
import logging
import math
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, Any, Literal

import httpx
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token
from fastapi import Depends, FastAPI, Header, HTTPException, Path, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.exc import OperationalError

from app.auth import COOKIE, Auth, EmailTaken, public_user
from app.brains import build_brain
from app.brains.base import Brain
from app.config import Settings, get_settings
from app.db import Repository
from app.errors import register_error_handlers
from app.health import router as health_router
from app.logging_setup import configure_logging
from app.outbound import CallJobRequest
from app.profiles import PROFILES
from app.schemas import AgentCreate, AgentUpdate, ContactCreate, ContactUpdate, WorkflowCreate, WorkflowUpdate
from app.security import RateLimiter, hash_token
from app.service import Conflict, NotFound, Service, Unavailable
from app.session import mark_playback_interrupted, process_turn
from app.store import SessionStore, TooManySessions
from app.telephony import Telephony, build_telephony
from app.telephony.routes import register as register_telephony
from app.workflows import JobDispatcher, SchedulerConfig, WorkflowEngine, WorkflowScheduler

log = logging.getLogger("voice_agent")


class StartCall(BaseModel):
    profile_id: str
    customer_ref: str | None = Field(default=None, min_length=1, max_length=64)


class CustomerBody(BaseModel):
    display_name: str = Field(min_length=1, max_length=80)
    data: dict[str, Any]


class SayRequest(BaseModel):
    text: str = Field(min_length=1, max_length=500)


class ClientEvent(BaseModel):
    type: Literal["playback_interrupted"]


class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=200)

class SignupRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    # The password policy (minimum length) is enforced in one place, security.hash_password, so the
    # refusal reaches the person as a readable message rather than a schema error.
    password: str = Field(min_length=1, max_length=200)
    workspace_name: str = Field(min_length=1, max_length=120)


class GoogleLoginRequest(BaseModel):
    credential: str = Field(min_length=1, max_length=8192)


class AnswerRequest(BaseModel):
    token: str = Field(min_length=1, max_length=64)


def sse(event: dict[str, Any]) -> str:
    return f"event: {event['type']}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"


def create_app(
    brain: Brain | None = None,
    settings: Settings | None = None,
    repo: Repository | None = None,
    http: httpx.AsyncClient | None = None,
    telephony: Telephony | None = None,
) -> FastAPI:
    """Everything but `settings` is injectable, for tests."""
    settings = settings or get_settings()
    configure_logging(settings.log_level)
    brain = brain if brain is not None else build_brain(settings)
    repo = repo or Repository(settings.database_url)
    http = http or httpx.AsyncClient()
    telephony = telephony or build_telephony(settings, http)
    auth = Auth(repo, settings)
    limiter = RateLimiter()
    service = Service(
        repo=repo,
        store=SessionStore(settings.max_sessions, settings.session_ttl_seconds),
        brain=brain,
        settings=settings,
        http=http,
        telephony=telephony,
    )

    # Only when switched on: a scheduler makes this process ring people, so it never starts by accident.
    scheduler = (
        WorkflowScheduler(WorkflowEngine(service), JobDispatcher(service), SchedulerConfig.from_settings(settings))
        if settings.workflow_scheduler_enabled
        else None
    )

    async def housekeeping() -> None:
        while True:
            await asyncio.sleep(60)
            limiter.prune()

            try:
                await asyncio.to_thread(repo.purge_expired_sessions, time.time())
            except OperationalError as error:  # an outage must not end this task for good
                log.warning("housekeeping skipped, database unavailable (%s)", type(error.orig).__name__)
            except Exception:
                log.exception("housekeeping failed")

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        try:
            if not await asyncio.to_thread(repo.migrated):
                log.warning("The database has not been migrated: run `alembic upgrade head`")

            await service.resume_callbacks()
        except OperationalError as error:  # the API should still come up; a restart resumes them
            log.error(
                "Could not resume pending callbacks; restart once the database is reachable (%s)",
                type(error.orig).__name__,
            )

        sweeper = asyncio.create_task(service.run_sweeper())
        cleaner = asyncio.create_task(housekeeping())

        if scheduler:
            scheduler.start()

        yield

        if scheduler:  # first: a tick in flight may still be creating jobs, dialing and sending callbacks
            await scheduler.stop()

        sweeper.cancel()
        cleaner.cancel()
        await service.drain()
        await service.http.aclose()

    app = FastAPI(
        title=settings.app_name,
        version="0.2.0",
        lifespan=lifespan,
        # Starlette's debug mode renders tracebacks and bypasses the error handlers.
        debug=settings.debug and settings.app_env != "production",
    )
    app.state.service = service
    app.state.auth = auth
    app.state.scheduler = scheduler  # None unless WORKFLOW_SCHEDULER_ENABLED
    register_error_handlers(app)
    app.include_router(health_router)

    if telephony is not None:
        register_telephony(app, service, telephony)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", "X-API-Key"],
    )

    def find(call_id: str):
        session = service.store.get(call_id)

        if session is None:
            raise HTTPException(404, "Unknown call")

        return session

    def hit(name: str, key: str, per_minute: int) -> None:
        """Count a request against a limit; 429 with Retry-After if it is over."""
        wait = limiter.check(f"{name}:{key}", per_minute, 60)

        if wait is not None:
            raise HTTPException(
                429, "Too many requests. Please slow down.", headers={"Retry-After": str(math.ceil(wait))}
            )

    async def operator(request: Request) -> dict[str, Any] | None:
        """The signed-in operator, or None. With sign-in switched off (development),
        anyone is treated as an anonymous operator."""
        user = await auth.user_for_request(request)

        if user is not None:
            if not auth.origin_ok(request):
                raise HTTPException(403, "Cross-origin request refused")
            return user

        if not settings.auth_required:
            return {"id": 0, "email": "anonymous", "role": "admin", "organization_id": None}

        raise HTTPException(401, "Sign in required")

    def api_key_ok(x_api_key: str | None) -> bool:
        return bool(settings.api_key and x_api_key and hmac.compare_digest(x_api_key, settings.api_key))

    async def business(request: Request, x_api_key: Annotated[str | None, Header()] = None) -> str:
        """A business system (API key) or an operator (session). Returns a stable
        identity to rate-limit by."""
        if x_api_key is not None:
            if not api_key_ok(x_api_key):
                raise HTTPException(401, "Invalid API key")
            return f"key:{hash_token(x_api_key)[:12]}"

        user = await operator(request)
        return f"user:{user['id']}"

    def confinement(user: dict[str, Any] | None) -> int | None:
        """The organization this account is confined to, or None for no confinement.

        Only an ordinary operator who belongs to an organization is confined: that is what a
        self-service sign-up creates, and such an account must never see another organization's
        calls, jobs or results. Administrators, accounts that predate organizations, business
        systems (API key) and a server with sign-in switched off see everything, exactly as they
        did before sign-up existed."""
        if user is None or user.get("role") == "admin":
            return None

        return user.get("organization_id")

    async def org_scope(request: Request, x_api_key: Annotated[str | None, Header()] = None) -> int | None:
        """Used next to `business`, which has already refused anyone who may not be here."""
        if x_api_key is not None:
            return None  # a business system (its key was checked by `business`)

        return confinement(await auth.user_for_request(request))

    async def admin_or_key(request: Request, x_api_key: Annotated[str | None, Header()] = None) -> None:
        """Changing customer data: a business system (API key) or an admin."""
        if x_api_key is not None:
            if not api_key_ok(x_api_key):
                raise HTTPException(401, "Invalid API key")
            return

        user = await operator(request)

        if user["role"] != "admin":
            raise HTTPException(403, "Administrators only")

    def translate(error: Exception) -> HTTPException:
        if isinstance(error, NotFound):
            return HTTPException(404, str(error))
        if isinstance(error, Conflict):
            return HTTPException(409, str(error))
        if isinstance(error, (Unavailable, TooManySessions)):
            return HTTPException(503, str(error) or "Too many active calls")
        return HTTPException(422, str(error))

    # --- health ------------------------------------------------------------

    @app.get("/api/health")
    async def health() -> dict[str, Any]:
        return {
            "status": "ok",
            # None means no LLM is configured: clients should use their local runtime.
            "brain": brain.name if brain else None,
            "outbound": bool(brain),
            "telephony": telephony is not None,
            "auth_required": settings.auth_required,
            "signup_enabled": settings.signup_enabled,
            "profiles": list(PROFILES),
        }

    # --- sign-in ---------------------------------------------------------------

    @app.post("/api/auth/login")
    async def login(request: Request, body: LoginRequest, response: Response) -> dict[str, Any]:
        hit("login-ip", auth.client_ip(request), settings.limit_login_per_ip)
        hit("login-email", body.email.strip().lower(), settings.limit_login_per_email)

        user = await auth.authenticate(body.email.strip(), body.password)

        # Same answer whether the email is unknown, the password is wrong or the user is disabled.
        if user is None:
            raise HTTPException(401, "Invalid email or password")

        response.set_cookie(
            COOKIE,
            await auth.start_session(user),
            max_age=settings.auth_session_hours * 3600,
            httponly=True,
            samesite="lax",
            secure=settings.cookie_secure,
            path="/",
        )
        return {"user": public_user(user)}


    @app.post("/api/auth/google")
    async def google_login(
        request: Request,
        body: GoogleLoginRequest,
        response: Response,
    ) -> dict[str, Any]:
        hit("login-ip", auth.client_ip(request), settings.limit_login_per_ip)

        if not settings.google_client_id:
            raise HTTPException(503, "Google sign-in is not configured")

        try:
            claims = id_token.verify_oauth2_token(
                body.credential,
                google_requests.Request(),
                settings.google_client_id,
            )
        except ValueError:
            raise HTTPException(401, "Invalid Google credential") from None

        email = str(claims.get("email", "")).strip().lower()
        if not email or claims.get("email_verified") is not True:
            raise HTTPException(401, "Google account email is not verified")

        user = await auth._db(auth.repo.get_user_by_email, email)

        if user is None or user["disabled"]:
            raise HTTPException(401, "Google account is not authorized")

        response.set_cookie(
            COOKIE,
            await auth.start_session(user),
            max_age=settings.auth_session_hours * 3600,
            httponly=True,
            samesite="lax",
            secure=settings.cookie_secure,
            path="/",
        )

        return {"user": public_user(user)}

    

    @app.post("/api/auth/signup", status_code=201)
    async def signup(request: Request, body: SignupRequest, response: Response) -> dict[str, Any]:
        """Create an account (with its own workspace) and sign it in, exactly as /api/auth/login would."""
        if not settings.signup_enabled:
            raise HTTPException(403, "Sign-up is not open on this server")

        hit("signup-ip", auth.client_ip(request), settings.limit_signup_per_ip)

        try:
            user = await auth.register(body.email, body.password, body.workspace_name)
        except EmailTaken:
            raise HTTPException(409, "An account with this email already exists") from None
        except ValueError as error:
            raise HTTPException(422, str(error)) from None

        response.set_cookie(
            COOKIE,
            await auth.start_session(user),
            max_age=settings.auth_session_hours * 3600,
            httponly=True,
            samesite="lax",
            secure=settings.cookie_secure,
            path="/",
        )
        response.status_code = 201

        return {"user": public_user(user)}

    @app.post("/api/auth/logout", status_code=204)
    async def logout(request: Request, response: Response) -> Response:
        await auth.end_session(request.cookies.get(COOKIE))
        response.delete_cookie(COOKIE, path="/")
        response.status_code = 204
        return response

    @app.get("/api/auth/me")
    async def me(request: Request) -> dict[str, Any]:
        user = await auth.user_for_request(request)

        if user is None:
            raise HTTPException(401, "Sign in required")

        return {"user": public_user(user)}

    # --- live calls (inbound: the caller opens the call) --------------------

    @app.post("/api/calls", status_code=201)
    async def start_call(request: StartCall, user: Annotated[dict | None, Depends(operator)]) -> dict[str, Any]:
        hit("calls", str(user["id"]), settings.limit_calls_per_user)

        scope = confinement(user)

        # The customers table is shared between organizations; a confined account has no business in it.
        if scope is not None and request.customer_ref:
            raise HTTPException(403, "Customer records are not available to your account")

        try:
            session, greeting = await service.start_inbound(request.profile_id, request.customer_ref, organization_id=scope)
        except (NotFound, Unavailable, TooManySessions) as error:
            raise translate(error) from None

        return {
            "call_id": session.id,
            "profile_id": session.profile.id,
            "brain": brain.name,
            "greeting": greeting,
        }

    @app.post("/api/calls/{call_id}/turn")
    async def turn(call_id: str, request: SayRequest) -> StreamingResponse:
        session = find(call_id)
        hit("turns", call_id, settings.limit_turns_per_call)

        if session.ended:
            raise HTTPException(409, "This call has ended")

        async def stream() -> AsyncIterator[str]:
            events = process_turn(session, request.text)

            try:
                async for event in events:
                    yield sse(event)
            finally:
                # If the caller cut the reply off (barge-in), stop the LLM too.
                await events.aclose()

            # The agent ended the call itself (wrong party, time limit): don't
            # wait for the client to notice, or a lost client leaves it open.
            if session.should_end:
                await service.finalize(session)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.post("/api/calls/{call_id}/events", status_code=204)
    async def client_event(call_id: str, event: ClientEvent) -> Response:
        session = find(call_id)

        if event.type == "playback_interrupted":
            mark_playback_interrupted(session)

        session.last_active = time.time()
        return Response(status_code=204)

    @app.post("/api/calls/{call_id}/end")
    async def end_call(call_id: str) -> dict[str, Any]:
        return await service.finalize(find(call_id))

    @app.get("/api/calls", dependencies=[Depends(business)])
    async def list_calls(
        scope: Annotated[int | None, Depends(org_scope)],
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
        direction: Annotated[Literal["inbound", "outbound"] | None, Query()] = None,
    ) -> list[dict[str, Any]]:
        """Finished calls, newest first: the call history (a confined account sees only its own)."""
        return await service.list_calls(limit, direction, scope)

    @app.get("/api/calls/{call_id}/result", dependencies=[Depends(business)])
    async def call_result(call_id: str, scope: Annotated[int | None, Depends(org_scope)]) -> dict[str, Any]:
        try:
            return await service.get_result(call_id, scope)
        except NotFound as error:
            raise translate(error) from None

    # --- customers ---------------------------------------------------------------

    Ref = Annotated[str, Path(pattern=r"^[A-Za-z0-9._-]{1,64}$")]

    @app.get("/api/customers", dependencies=[Depends(business)])
    async def list_customers(profile_id: str, scope: Annotated[int | None, Depends(org_scope)]) -> list[dict[str, Any]]:
        """Who the agent can be pointed at for a profile (names only, no data). The table is shared
        between organizations, so a confined account is offered none of it (its own people are
        Contacts)."""
        return [] if scope is not None else await service.list_customers(profile_id)

    @app.get("/api/customers/{profile_id}/{ref}", dependencies=[Depends(admin_or_key)])
    async def get_customer(profile_id: str, ref: Ref) -> dict[str, Any]:
        try:
            return await service.get_customer(profile_id, ref)
        except NotFound as error:
            raise translate(error) from None

    @app.put("/api/customers/{profile_id}/{ref}", status_code=204, dependencies=[Depends(admin_or_key)])
    async def put_customer(profile_id: str, ref: Ref, body: CustomerBody) -> Response:
        try:
            await service.put_customer(profile_id, ref, body.display_name.strip(), body.data)
        except (NotFound, ValueError) as error:
            raise translate(error) from None

        return Response(status_code=204)

    # --- organizations and agents (an operator's own tenant only; read-only for organizations) ---

    @app.get("/api/organizations")
    async def list_organizations(user: Annotated[dict, Depends(operator)]) -> list[dict[str, Any]]:
        return await service.list_organizations(user["organization_id"])

    @app.get("/api/organizations/{organization_id}")
    async def get_organization(organization_id: int, user: Annotated[dict, Depends(operator)]) -> dict[str, Any]:
        try:
            return await service.get_organization(organization_id, user["organization_id"])
        except NotFound as error:
            raise translate(error) from None

    @app.get("/api/agents")
    async def list_agents(user: Annotated[dict, Depends(operator)]) -> list[dict[str, Any]]:
        return await service.list_agents(user["organization_id"])

    @app.get("/api/agents/{agent_id}")
    async def get_agent(agent_id: int, user: Annotated[dict, Depends(operator)]) -> dict[str, Any]:
        try:
            return await service.get_agent(agent_id, user["organization_id"])
        except NotFound as error:
            raise translate(error) from None

    @app.post("/api/agents", status_code=201)
    async def create_agent(request: AgentCreate, user: Annotated[dict, Depends(operator)]) -> dict[str, Any]:
        try:
            return await service.create_agent(user["organization_id"], request)
        except Conflict as error:
            raise translate(error) from None

    @app.put("/api/agents/{agent_id}")
    async def update_agent(agent_id: int, request: AgentUpdate, user: Annotated[dict, Depends(operator)]) -> dict[str, Any]:
        try:
            return await service.update_agent(agent_id, user["organization_id"], request)
        except (NotFound, Conflict) as error:
            raise translate(error) from None

    # --- contacts (an operator's own tenant only; distinct from /api/customers, the legacy demo
    # data below - see the Step 18C report for why the two are kept separate) ---------------------

    @app.get("/api/contacts")
    async def list_contacts(user: Annotated[dict, Depends(operator)]) -> list[dict[str, Any]]:
        return await service.list_contacts(user["organization_id"])

    @app.get("/api/contacts/{contact_id}")
    async def get_contact(contact_id: int, user: Annotated[dict, Depends(operator)]) -> dict[str, Any]:
        try:
            return await service.get_contact(contact_id, user["organization_id"])
        except NotFound as error:
            raise translate(error) from None

    @app.post("/api/contacts", status_code=201)
    async def create_contact(request: ContactCreate, user: Annotated[dict, Depends(operator)]) -> dict[str, Any]:
        try:
            return await service.create_contact(user["organization_id"], request)
        except Conflict as error:
            raise translate(error) from None

    @app.put("/api/contacts/{contact_id}")
    async def update_contact(contact_id: int, request: ContactUpdate, user: Annotated[dict, Depends(operator)]) -> dict[str, Any]:
        try:
            return await service.update_contact(contact_id, user["organization_id"], request)
        except NotFound as error:
            raise translate(error) from None

    @app.delete("/api/contacts/{contact_id}", status_code=204)
    async def delete_contact(contact_id: int, user: Annotated[dict, Depends(operator)]) -> Response:
        try:
            await service.delete_contact(contact_id, user["organization_id"])
        except (NotFound, Conflict) as error:
            raise translate(error) from None

        return Response(status_code=204)

    # --- workflows (an operator's own tenant only) --------------------------------------------

    @app.get("/api/workflows")
    async def list_workflows(user: Annotated[dict, Depends(operator)]) -> list[dict[str, Any]]:
        return await service.list_workflows(user["organization_id"])

    @app.get("/api/workflows/{workflow_id}")
    async def get_workflow(workflow_id: int, user: Annotated[dict, Depends(operator)]) -> dict[str, Any]:
        try:
            return await service.get_workflow(workflow_id, user["organization_id"])
        except NotFound as error:
            raise translate(error) from None

    @app.post("/api/workflows", status_code=201)
    async def create_workflow(request: WorkflowCreate, user: Annotated[dict, Depends(operator)]) -> dict[str, Any]:
        try:
            return await service.create_workflow(user["organization_id"], request)
        except (ValueError, Conflict) as error:
            raise translate(error) from None

    @app.put("/api/workflows/{workflow_id}")
    async def update_workflow(workflow_id: int, request: WorkflowUpdate, user: Annotated[dict, Depends(operator)]) -> dict[str, Any]:
        try:
            return await service.update_workflow(workflow_id, user["organization_id"], request)
        except (NotFound, ValueError, Conflict) as error:
            raise translate(error) from None

    @app.delete("/api/workflows/{workflow_id}", status_code=204)
    async def delete_workflow(workflow_id: int, user: Annotated[dict, Depends(operator)]) -> Response:
        try:
            await service.delete_workflow(workflow_id, user["organization_id"])
        except (NotFound, Conflict) as error:
            raise translate(error) from None

        return Response(status_code=204)

    # --- workflow eligibility + manual trigger (Step 18E: both reuse WorkflowEngine, see
    # app/service.py's check_workflow_eligibility/trigger_workflow - nothing here duplicates it) ---

    @app.post("/api/workflows/{workflow_id}/contacts/{contact_id}/eligibility")
    async def workflow_eligibility(
        workflow_id: int, contact_id: int, user: Annotated[dict, Depends(operator)]
    ) -> dict[str, Any]:
        try:
            return await service.check_workflow_eligibility(workflow_id, contact_id, user["organization_id"])
        except NotFound as error:
            raise translate(error) from None

    @app.post("/api/workflows/{workflow_id}/contacts/{contact_id}/trigger")
    async def workflow_trigger(workflow_id: int, contact_id: int, user: Annotated[dict, Depends(operator)]) -> dict[str, Any]:
        try:
            return await service.trigger_workflow(workflow_id, contact_id, user["organization_id"])
        except NotFound as error:
            raise translate(error) from None

    # --- outbound: the business side (API key) -----------------------------

    @app.post("/api/call-jobs", status_code=201)
    async def create_job(
        request: CallJobRequest,
        response: Response,
        who: Annotated[str, Depends(business)],
        scope: Annotated[int | None, Depends(org_scope)],
    ) -> dict[str, Any]:
        hit("jobs", who, settings.limit_jobs_per_principal)

        if scope is not None:
            # A confined account creates jobs for its own organization only: whatever it names is
            # checked against that organization (so another one's agents and contacts are out of
            # reach), and the job is stamped with it (so the account can see the result).
            if request.organization_id not in (None, scope):
                raise HTTPException(403, "That organization is not yours")
            if request.customer_ref:
                raise HTTPException(403, "Customer records are not available to your account")

            request = request.model_copy(update={"organization_id": scope})

        try:
            view, created = await service.create_job(request)
        except (ValueError, Conflict) as error:  # includes an unsafe callback URL
            raise translate(error) from None

        if not created:
            response.status_code = 200  # same reference again: the existing job

        return view

    @app.get("/api/call-jobs", dependencies=[Depends(business)])
    async def list_jobs(
        scope: Annotated[int | None, Depends(org_scope)], limit: Annotated[int, Query(ge=1, le=200)] = 50
    ) -> list[dict[str, Any]]:
        return await service.list_jobs(limit, scope)

    @app.get("/api/call-jobs/{job_id}", dependencies=[Depends(business)])
    async def get_job(job_id: str, scope: Annotated[int | None, Depends(org_scope)]) -> dict[str, Any]:
        try:
            return await service.get_job(job_id, scope)
        except NotFound as error:
            raise translate(error) from None

    # --- outbound: the callee side (answer token from the link) ------------

    @app.get("/api/call-jobs/{job_id}/ring")
    async def ring(
        job_id: str, http_request: Request, token: Annotated[str, Query(min_length=1, max_length=64)]
    ) -> dict[str, Any]:
        hit("callee", auth.client_ip(http_request), settings.limit_callee_per_ip)

        try:
            return await service.ring_info(job_id, token)
        except NotFound as error:
            raise translate(error) from None

    @app.post("/api/call-jobs/{job_id}/answer", status_code=201)
    async def answer(job_id: str, request: AnswerRequest, http_request: Request) -> dict[str, Any]:
        hit("callee", auth.client_ip(http_request), settings.limit_callee_per_ip)

        try:
            session, greeting = await service.answer_job(job_id, request.token)
        except (NotFound, Conflict, Unavailable, TooManySessions) as error:
            raise translate(error) from None

        return {
            "call_id": session.id,
            "profile_id": session.profile.id,
            "brain": brain.name,
            "greeting": greeting,
        }

    @app.post("/api/call-jobs/{job_id}/decline", status_code=204)
    async def decline(job_id: str, request: AnswerRequest, http_request: Request) -> Response:
        hit("callee", auth.client_ip(http_request), settings.limit_callee_per_ip)

        try:
            await service.decline_job(job_id, request.token)
        except NotFound as error:
            raise translate(error) from None

        return Response(status_code=204)

    return app


app = create_app()
