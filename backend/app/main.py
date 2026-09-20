import asyncio
import hmac
import json
import math
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, Any, Literal

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Path, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.auth import COOKIE, Auth, public_user
from app.brains import build_brain
from app.brains.base import Brain
from app.config import Settings, get_settings
from app.db import Repository
from app.outbound import CallJobRequest
from app.profiles import PROFILES
from app.security import RateLimiter, hash_token
from app.service import Conflict, NotFound, Service, Unavailable
from app.session import mark_playback_interrupted, process_turn
from app.store import SessionStore, TooManySessions
from app.telephony import Telephony, build_telephony
from app.telephony.routes import register as register_telephony


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

    async def housekeeping() -> None:
        while True:
            await asyncio.sleep(60)
            limiter.prune()
            await asyncio.to_thread(repo.purge_expired_sessions, time.time())

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        await service.resume_callbacks()
        sweeper = asyncio.create_task(service.run_sweeper())
        cleaner = asyncio.create_task(housekeeping())

        yield

        sweeper.cancel()
        cleaner.cancel()
        await service.drain()
        await service.http.aclose()

    app = FastAPI(title="Real-Time Voice Agent", version="0.2.0", lifespan=lifespan)
    app.state.service = service
    app.state.auth = auth

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
            return {"id": 0, "email": "anonymous", "role": "admin"}

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

        try:
            session, greeting = await service.start_inbound(request.profile_id, request.customer_ref)
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
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
        direction: Annotated[Literal["inbound", "outbound"] | None, Query()] = None,
    ) -> list[dict[str, Any]]:
        """Finished calls, newest first: the call history."""
        return await service.list_calls(limit, direction)

    @app.get("/api/calls/{call_id}/result", dependencies=[Depends(business)])
    async def call_result(call_id: str) -> dict[str, Any]:
        try:
            return await service.get_result(call_id)
        except NotFound as error:
            raise translate(error) from None

    # --- customers ---------------------------------------------------------------

    Ref = Annotated[str, Path(pattern=r"^[A-Za-z0-9._-]{1,64}$")]

    @app.get("/api/customers", dependencies=[Depends(business)])
    async def list_customers(profile_id: str) -> list[dict[str, Any]]:
        """Who the agent can be pointed at for a profile (names only, no data)."""
        return await service.list_customers(profile_id)

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

    # --- outbound: the business side (API key) -----------------------------

    @app.post("/api/call-jobs", status_code=201)
    async def create_job(
        request: CallJobRequest, response: Response, who: Annotated[str, Depends(business)]
    ) -> dict[str, Any]:
        hit("jobs", who, settings.limit_jobs_per_principal)

        try:
            view, created = await service.create_job(request)
        except (ValueError, Conflict) as error:  # includes an unsafe callback URL
            raise translate(error) from None

        if not created:
            response.status_code = 200  # same reference again: the existing job

        return view

    @app.get("/api/call-jobs", dependencies=[Depends(business)])
    async def list_jobs(limit: Annotated[int, Query(ge=1, le=200)] = 50) -> list[dict[str, Any]]:
        return await service.list_jobs(limit)

    @app.get("/api/call-jobs/{job_id}", dependencies=[Depends(business)])
    async def get_job(job_id: str) -> dict[str, Any]:
        try:
            return await service.get_job(job_id)
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
