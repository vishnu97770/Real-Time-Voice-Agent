"""The public face of telephony: the two webhooks Twilio calls and the audio WebSocket.

None of these have a login. What protects them is that Twilio signs its webhooks
(checked here) and that the audio WebSocket must present a short-lived token that
only a call we set up carries.
"""

import asyncio
import base64
import json
from typing import Any
from urllib.parse import parse_qsl

from fastapi import FastAPI, HTTPException, Request, Response, WebSocket, WebSocketDisconnect

from app.service import Conflict, NotFound, Service, Unavailable
from app.store import TooManySessions
from app.telephony import Telephony
from app.telephony.deepgram import DeepgramError
from app.telephony.pipeline import PhoneCall
from app.telephony.twilio import (
    TwilioError,
    refuse_twiml,
    sign_stream_token,
    stream_twiml,
    validate_signature,
    verify_stream_token,
)

START_TIMEOUT_SECONDS = 10


class TwilioLine:
    """The phone line, as the pipeline sees it: Media Streams messages over a WebSocket."""

    def __init__(self, socket: WebSocket, stream_sid: str, call_sid: str, telephony: Telephony) -> None:
        self._socket = socket
        self._stream_sid = stream_sid
        self._call_sid = call_sid
        self._telephony = telephony
        self._lock = asyncio.Lock()  # one message on the socket at a time

    async def _send(self, message: dict[str, Any]) -> None:
        async with self._lock:
            await self._socket.send_text(json.dumps({**message, "streamSid": self._stream_sid}))

    async def send_audio(self, mulaw: bytes) -> None:
        await self._send({"event": "media", "media": {"payload": base64.b64encode(mulaw).decode()}})

    async def send_clear(self) -> None:
        await self._send({"event": "clear"})

    async def send_mark(self, name: str) -> None:
        await self._send({"event": "mark", "mark": {"name": name}})

    async def hang_up(self) -> None:
        try:
            await self._telephony.twilio.hang_up(self._call_sid)
        except TwilioError:
            pass  # already gone, which is what we wanted


def register(app: FastAPI, service: Service, telephony: Telephony) -> None:
    async def signed_params(request: Request, url: str) -> list[tuple[str, str]]:
        params = parse_qsl((await request.body()).decode(), keep_blank_values=True)

        if not validate_signature(telephony.auth_token, url, params, request.headers.get("x-twilio-signature")):
            raise HTTPException(403, "Bad signature")

        return params

    @app.post("/api/telephony/status")
    async def status(request: Request, job: str = "") -> Response:
        """Twilio reports how a call we placed is going (busy, no answer, finished...)."""
        params = await signed_params(request, telephony.status_url(job))
        await service.handle_twilio_status(job, dict(params))
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
        call: PhoneCall | None = None

        try:
            start = None

            while start is None:
                message = json.loads(await asyncio.wait_for(socket.receive_text(), START_TIMEOUT_SECONDS))

                if message.get("event") == "start":
                    start = message["start"]
                elif message.get("event") != "connected":
                    return

            call_sid, stream_sid = start["callSid"], start["streamSid"]
            line = TwilioLine(socket, stream_sid, call_sid, telephony)

            try:
                session, greeting = await attach(start.get("customParameters") or {}, call_sid)
            except PermissionError:
                await socket.close(code=1008)  # not one of our calls: say nothing, do nothing
                return
            except (NotFound, Conflict, Unavailable, TooManySessions):
                await line.hang_up()  # ours, but not answerable now (expired, taken, no capacity)
                return

            try:
                listener = await telephony.open_listener()
            except DeepgramError:
                session.log("listener_failed")
                session.end_reason = "listener_failed"
                await service.finalize(session)
                await line.hang_up()
                return

            call = PhoneCall(
                session=session,
                service=service,
                line=line,
                listener=listener,
                speaker=telephony.speaker,
                barge_in_min_words=telephony.barge_in_min_words,
            )
            await call.start(greeting)

            async for raw in socket.iter_text():
                message = json.loads(raw)
                event = message.get("event")

                if event == "media":
                    await call.audio_in(base64.b64decode(message["media"]["payload"]))
                elif event == "mark":
                    call.mark_played(message["mark"]["name"])
                elif event == "stop":
                    break
        except (WebSocketDisconnect, asyncio.TimeoutError, ValueError, KeyError):
            pass
        finally:
            if call is not None:
                # The caller hung up, or the line dropped: record the call. Detached,
                # because this handler is often being cancelled at exactly this moment
                # and the call's result must still be saved.
                await asyncio.shield(service.run_detached(call.close("hangup", hang_up=False)))

            # However this ended, end the connection: not every server does it for us.
            try:
                await socket.close()
            except RuntimeError:
                pass  # already closed
