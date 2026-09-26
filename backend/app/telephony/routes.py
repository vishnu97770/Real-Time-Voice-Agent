"""The public face of telephony: the two webhooks Twilio calls and the audio WebSocket.

None of these have a login. What protects them is that Twilio signs its webhooks
(checked here) and that the audio WebSocket must present a short-lived token that
only a call we set up carries.
"""

import asyncio
import json
from urllib.parse import parse_qsl

from fastapi import FastAPI, HTTPException, Request, Response, WebSocket, WebSocketDisconnect

from app.service import Conflict, NotFound, Service, Unavailable
from app.store import TooManySessions
from app.telephony import Telephony
from app.telephony.base import InvalidWebhook, WebhookRequest
from app.telephony.deepgram import DeepgramError
from app.telephony.media import TwilioMediaLink
from app.telephony.twilio import (
    refuse_twiml,
    sign_stream_token,
    stream_twiml,
    validate_signature,
    verify_stream_token,
)
from app.voice_runtime_factory import create_voice_runtime

START_TIMEOUT_SECONDS = 10


def register(app: FastAPI, service: Service, telephony: Telephony) -> None:
    async def signed_params(request: Request, url: str) -> list[tuple[str, str]]:
        params = parse_qsl((await request.body()).decode(), keep_blank_values=True)

        if not validate_signature(telephony.auth_token, url, params, request.headers.get("x-twilio-signature")):
            raise HTTPException(403, "Bad signature")

        return params

    @app.post("/api/telephony/status")
    async def status(request: Request, job: str = "") -> Response:
        """Twilio reports how a call we placed is going (busy, no answer, finished...). The adapter
        authenticates and reads it; the service acts on the provider-neutral event."""
        try:
            event = telephony.adapter.parse_event(
                WebhookRequest(body=await request.body(), headers=dict(request.headers), query={"job": job})
            )
        except InvalidWebhook:
            raise HTTPException(403, "Bad signature") from None

        if event is not None:
            await service.handle_call_event(event)

        return Response(status_code=204)

    @app.post("/api/telephony/voice")
    async def voice(request: Request) -> Response:
        """Someone phoned our number. Off by default: caller ID proves nothing, and
        an inbound call has no identity check, so an open line would hand data to
        whoever rings."""
        await signed_params(request, telephony.voice_url())

        if not telephony.inbound_enabled:
            twiml = refuse_twiml("Sorry, this number does not take incoming calls.")
        else:
            profile = telephony.inbound_profile
            token = sign_stream_token(telephony.auth_token, "inbound", profile, 120)
            twiml = stream_twiml(telephony.ws_url(), {"kind": "inbound", "profile": profile, "token": token})

        return Response(content=twiml, media_type="text/xml")

    async def attach(parameters: dict[str, str], call_sid: str):
        """Turn the stream's parameters into a session, or refuse."""
        kind = parameters.get("kind")
        token = parameters.get("token", "")

        if kind == "job":
            job_id = parameters.get("job_id", "")

            if not verify_stream_token(telephony.auth_token, "job", job_id, token):
                raise PermissionError

            return await service.answer_job_phone(job_id, call_sid)

        if kind == "inbound" and telephony.inbound_enabled:
            profile = parameters.get("profile", "")

            if not verify_stream_token(telephony.auth_token, "inbound", profile, token):
                raise PermissionError

            return await service.start_inbound(profile, channel="phone")

        raise PermissionError

    @app.websocket("/api/telephony/stream")
    async def stream(socket: WebSocket) -> None:
        await socket.accept()

        try:
            start = None

            while start is None:
                message = json.loads(await asyncio.wait_for(socket.receive_text(), START_TIMEOUT_SECONDS))

                if message.get("event") == "start":
                    start = message["start"]
                elif message.get("event") != "connected":
                    return

            call_sid, stream_sid = start["callSid"], start["streamSid"]
            link = TwilioMediaLink(socket, stream_sid, call_sid, telephony)

            try:
                session, greeting = await attach(start.get("customParameters") or {}, call_sid)
            except PermissionError:
                await socket.close(code=1008)  # not one of our calls: say nothing, do nothing
                return
            except (NotFound, Conflict, Unavailable, TooManySessions):
                await link.close()  # ours, but not answerable now (expired, taken, no capacity)
                return

            try:
                listener = await telephony.open_listener()
            except DeepgramError:
                session.log("listener_failed")
                session.end_reason = "listener_failed"
                await service.finalize(session)
                await link.close()
                return

            # The conversation runs over the media link; when the call ends (the callee hangs up, the line
            # drops, the agent says goodbye) the runtime records it, even if this handler is cancelled.
            runtime = create_voice_runtime(session, telephony=telephony, service=service, listener=listener)
            await runtime.run(session, link, greeting)
        except (WebSocketDisconnect, asyncio.TimeoutError, ValueError, KeyError):
            pass
        finally:
            # However this ended, end the connection: not every server does it for us.
            try:
                await socket.close()
            except RuntimeError:
                pass  # already closed
