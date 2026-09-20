"""Twilio: placing and ending calls, and trusting what Twilio tells us.

Twilio Programmable Voice with Media Streams: we ask Twilio to ring someone and,
when they answer, to open a WebSocket to us carrying their voice as 8 kHz mu-law
audio. No Twilio SDK is needed; it is a handful of REST calls and one signature.
"""

import base64
import hashlib
import hmac
import re
import time
from typing import Any
from urllib.parse import quote
from xml.sax.saxutils import quoteattr

import httpx

API = "https://api.twilio.com/2010-04-01"
# Twilio will not ring longer than this, whatever we ask for.
MAX_RING_SECONDS = 600


class TwilioError(Exception):
    pass


def to_e164(phone: str) -> str | None:
    """"+91 98765-43210" -> "+919876543210". None if it is not international format."""
    cleaned = re.sub(r"[\s().-]", "", phone)
    return cleaned if re.fullmatch(r"\+[1-9]\d{6,14}", cleaned) else None


# --- trusting Twilio ---------------------------------------------------------------


def validate_signature(auth_token: str, url: str, params: list[tuple[str, str]], signature: str | None) -> bool:
    """Twilio signs every webhook: HMAC-SHA1 of the exact URL it called followed by
    the POST parameters sorted by name, keyed with your auth token. Anyone can POST
    to our public URL, so without this a stranger could end or fake a call."""
    if not signature:
        return False

    payload = url + "".join(key + value for key, value in sorted(params))
    expected = base64.b64encode(hmac.new(auth_token.encode(), payload.encode(), hashlib.sha1).digest()).decode()

    return hmac.compare_digest(expected, signature)


def sign_stream_token(secret: str, kind: str, ident: str, ttl_seconds: int = 300, now: float | None = None) -> str:
    """A short-lived token we put in the stream's parameters and check when the
    WebSocket connects, so only a call we set up can attach to a session."""
    expires = int((time.time() if now is None else now) + ttl_seconds)
    digest = hmac.new(secret.encode(), f"{kind}:{ident}:{expires}".encode(), hashlib.sha256).hexdigest()
    return f"{expires}.{digest}"


def verify_stream_token(secret: str, kind: str, ident: str, token: str, now: float | None = None) -> bool:
    try:
        expires, digest = token.split(".", 1)
        if int(expires) < (time.time() if now is None else now):
            return False
    except ValueError:
        return False

    expected = hmac.new(secret.encode(), f"{kind}:{ident}:{expires}".encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, digest)


# --- TwiML -----------------------------------------------------------------------------


def stream_twiml(ws_url: str, parameters: dict[str, str]) -> str:
    """Tell Twilio to connect the call's audio to our WebSocket."""
    params = "".join(f"<Parameter name={quoteattr(k)} value={quoteattr(v)}/>" for k, v in parameters.items())
    return f'<Response><Connect><Stream url={quoteattr(ws_url)}>{params}</Stream></Connect></Response>'


def refuse_twiml(message: str) -> str:
    from xml.sax.saxutils import escape

    return f"<Response><Say>{escape(message)}</Say><Hangup/></Response>"


# --- REST ---------------------------------------------------------------------------------


class TwilioClient:
    def __init__(self, account_sid: str, auth_token: str, from_number: str, http: httpx.AsyncClient) -> None:
        self.account_sid = account_sid
        self.auth_token = auth_token
        self.from_number = from_number
        self.http = http

    async def _post(self, path: str, data: list[tuple[str, str]]) -> dict[str, Any]:
        # A field that repeats (StatusCallbackEvent) is sent as a list under one name.
        form: dict[str, list[str]] = {}

        for key, value in data:
            form.setdefault(key, []).append(value)

        try:
            response = await self.http.post(
                f"{API}/Accounts/{quote(self.account_sid)}/{path}",
                data=form,
                auth=(self.account_sid, self.auth_token),
                timeout=15.0,
            )
        except httpx.HTTPError as error:
            raise TwilioError(f"could not reach Twilio ({type(error).__name__})") from None

        if response.status_code >= 400:
            # Twilio's error body says what is wrong; it never contains our credentials.
            try:
                detail = response.json().get("message", "")
            except ValueError:
                detail = ""
            raise TwilioError(f"Twilio said {response.status_code}: {detail}"[:300])

        return response.json()

    async def create_call(self, to: str, twiml: str, status_callback: str, ring_seconds: int) -> str:
        """Ring `to`; when answered, run `twiml`. Returns Twilio's call SID."""
        data = [
            ("To", to),
            ("From", self.from_number),
            ("Twiml", twiml),
            ("Timeout", str(min(ring_seconds, MAX_RING_SECONDS))),
            ("StatusCallback", status_callback),
            ("StatusCallbackMethod", "POST"),
            *[("StatusCallbackEvent", event) for event in ("initiated", "ringing", "answered", "completed")],
        ]
        return (await self._post("Calls.json", data))["sid"]

    async def hang_up(self, call_sid: str) -> None:
        await self._post(f"Calls/{quote(call_sid)}.json", [("Status", "completed")])
