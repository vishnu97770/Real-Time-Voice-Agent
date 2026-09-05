from contextlib import asynccontextmanager
from datetime import datetime, timezone
import json
from time import perf_counter
from uuid import uuid4
import logging

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect, status
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.logging import configure_logging
from app.asr import UnavailableASR
from app.agent import UnavailableAgent
from app.tts import UnavailableTTS
from app.pipeline import VoicePipeline
from app.vad import UnavailableVAD
from app.realtime import SessionManager, SessionState, parse_client_message
from app.underwriting import ApplicationState, ApplicationStore, DocumentMetadata, application_dict

settings = get_settings()
configure_logging(settings.log_level)
logger = logging.getLogger("voice-agent.api")
session_manager = SessionManager()
asr = UnavailableASR()
agent = UnavailableAgent()
tts = UnavailableTTS()
vad = UnavailableVAD()
pipeline = VoicePipeline(asr, agent, tts, vad)
application_store = ApplicationStore()


@asynccontextmanager
async def lifespan(_: FastAPI):
    logger.info("service_started", extra={"correlation_id": "startup"})
    yield
    logger.info("service_stopped", extra={"correlation_id": "shutdown"})


app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(settings.allowed_origins),
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
    expose_headers=["X-Correlation-ID"],
)


@app.middleware("http")
async def correlation_middleware(request: Request, call_next):
    correlation_id = request.headers.get("X-Correlation-ID", str(uuid4()))
    started = perf_counter()
    response = await call_next(request)
    response.headers["X-Correlation-ID"] = correlation_id
    logger.info("request_completed", extra={"correlation_id": correlation_id})
    logger.debug("request_duration_ms=%.2f", (perf_counter() - started) * 1000)
    return response


@app.get("/")
async def root() -> dict[str, str]:
    return {"service": settings.app_name, "status": "ready", "environment": settings.environment}


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": settings.app_name, "version": "0.1.0"}


@app.post("/applications", status_code=status.HTTP_201_CREATED)
async def create_application(payload: dict) -> dict:
    applicant_name = payload.get("applicant_name")
    if not isinstance(applicant_name, str) or not applicant_name.strip():
        raise HTTPException(status_code=422, detail="applicant_name is required")
    return application_dict(application_store.create(applicant_name))


@app.get("/applications/{application_id}")
async def get_application(application_id: str) -> dict:
    application = application_store.get(application_id)
    if application is None:
        raise HTTPException(status_code=404, detail="application not found")
    return application_dict(application)


@app.post("/applications/{application_id}/documents", status_code=status.HTTP_201_CREATED)
async def register_document(application_id: str, payload: dict) -> dict:
    try:
        document = DocumentMetadata(
            document_id=str(uuid4()),
            filename=str(payload.get("filename", "")),
            content_type=str(payload.get("content_type", "application/octet-stream")),
            size_bytes=int(payload.get("size_bytes", 0)),
            uploaded_at=datetime.now(timezone.utc).isoformat(),
        )
        if not document.filename or document.size_bytes < 0:
            raise ValueError("filename and non-negative size_bytes are required")
        return application_dict(application_store.add_document(application_id, document))
    except KeyError:
        raise HTTPException(status_code=404, detail="application not found")
    except (TypeError, ValueError) as error:
        raise HTTPException(status_code=422, detail=str(error))


@app.post("/applications/{application_id}/state")
async def transition_application(application_id: str, payload: dict) -> dict:
    try:
        target = ApplicationState(payload.get("state"))
        return application_dict(application_store.transition(application_id, target))
    except KeyError:
        raise HTTPException(status_code=404, detail="application not found")
    except (TypeError, ValueError) as error:
        raise HTTPException(status_code=409, detail=str(error))


@app.get("/applications/{application_id}/audit")
async def get_application_audit(application_id: str) -> list[dict]:
    try:
        return [event.__dict__ if hasattr(event, "__dict__") else {
            "event_id": event.event_id, "application_id": event.application_id,
            "event_type": event.event_type, "detail": event.detail, "created_at": event.created_at,
        } for event in application_store.audit(application_id)]
    except KeyError:
        raise HTTPException(status_code=404, detail="application not found")


@app.websocket("/ws/voice")
async def voice_session(websocket: WebSocket) -> None:
    await websocket.accept()
    session = session_manager.create()
    session_manager.transition(session, SessionState.LISTENING)
    await pipeline.start(session.session_id)
    await websocket.send_json({"type": "session.ready", "session_id": session.session_id, "state": session.state, "connected_at": session.connected_at, "capabilities": {"asr": asr.provider if asr.available else "unavailable", "agent": agent.provider if agent.available else "unavailable", "tts": tts.provider if tts.available else "unavailable", "vad": vad.provider if vad.available else "unavailable"}})
    try:
        while True:
            message = await websocket.receive()
            if message.get("type") == "websocket.disconnect":
                raise WebSocketDisconnect(message.get("code", 1000))
            if message.get("bytes") is not None:
                audio_bytes = message["bytes"]
                if not audio_bytes:
                    await websocket.send_json({"type": "error", "code": "EMPTY_AUDIO"})
                    continue
                await websocket.send_json({"type": "audio.received", "session_id": session.session_id, "bytes": len(audio_bytes), "state": session.state})
                for event in await pipeline.handle_audio(session.session_id, audio_bytes):
                    if event.audio is not None:
                        await websocket.send_bytes(event.audio)
                    else:
                        await websocket.send_json({"type": event.kind, "session_id": event.session_id, "text": event.text, "detail": event.detail})
                continue
            try:
                payload = json.loads(message.get("text", ""))
                message_type, content = parse_client_message(payload)
            except (TypeError, ValueError, json.JSONDecodeError) as error:
                await websocket.send_json({"type": "error", "code": "INVALID_MESSAGE", "detail": str(error)})
                continue
            if message_type == "ping":
                await websocket.send_json({"type": "pong", "session_id": session.session_id})
            elif message_type == "text":
                session_manager.transition(session, SessionState.PROCESSING)
                await websocket.send_json({"type": "text.received", "session_id": session.session_id, "content": content, "state": session.state})
                session_manager.transition(session, SessionState.LISTENING)
                await websocket.send_json({"type": "session.state", "session_id": session.session_id, "state": session.state})
                for event in await pipeline.handle_text(session.session_id, content or ""):
                    await websocket.send_json({"type": event.kind, "session_id": event.session_id, "text": event.text, "detail": event.detail})
            elif message_type == "interrupt":
                pipeline.interrupt(session.session_id)
                session_manager.transition(session, SessionState.INTERRUPTED)
                await websocket.send_json({"type": "session.state", "session_id": session.session_id, "state": session.state})
                session_manager.transition(session, SessionState.LISTENING)
                await websocket.send_json({"type": "session.state", "session_id": session.session_id, "state": session.state})
            elif message_type == "end":
                session_manager.transition(session, SessionState.SESSION_END)
                await websocket.send_json({"type": "session.ended", "session_id": session.session_id, "state": session.state})
                await websocket.close(code=1000)
                return
    except WebSocketDisconnect:
        logger.info("session_disconnected", extra={"correlation_id": session.session_id})
    finally:
        session_manager.remove(session.session_id)
