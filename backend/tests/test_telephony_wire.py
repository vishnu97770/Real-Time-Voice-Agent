"""Twilio and Deepgram, checked at the wire: what we send them and what we make of what they send."""

import asyncio
import json
import time
import xml.etree.ElementTree as ET
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from websockets.asyncio.server import serve

from app.telephony.deepgram import (
    DeepgramError,
    DeepgramSTT,
    DeepgramTTS,
    SpeechStarted,
    Transcript,
    UtteranceEnd,
    parse_event,
)
from app.telephony.twilio import (
    TwilioClient,
    TwilioError,
    refuse_twiml,
    sign_stream_token,
    stream_twiml,
    to_e164,
    validate_signature,
    verify_stream_token,
)

DG_KEY = "dg-secret-key-123"
AUTH_TOKEN = "twilio-auth-token-abc"


# --- Twilio ------------------------------------------------------------------------------------


def test_our_signature_check_agrees_with_twilios_own_library():
    """The official library is the oracle: what it signs we accept, and nothing else."""
    validator = pytest.importorskip("twilio.request_validator").RequestValidator(AUTH_TOKEN)
    url = "https://abc123.ngrok.app/api/telephony/status?job=JOB-1"
    params = {"CallSid": "CAxxxx", "CallStatus": "no-answer", "To": "+919876543210", "Timestamp": "Sat, 19 Sep 2026"}
    signature = validator.compute_signature(url, params)

    assert validate_signature(AUTH_TOKEN, url, list(params.items()), signature)
    assert not validate_signature(AUTH_TOKEN, url, list({**params, "CallStatus": "completed"}.items()), signature)
    assert not validate_signature(AUTH_TOKEN, url + "&x=1", list(params.items()), signature)
    assert not validate_signature("another-token", url, list(params.items()), signature)
    assert not validate_signature(AUTH_TOKEN, url, list(params.items()), None)
    assert not validate_signature(AUTH_TOKEN, url, list(params.items()), "")


def test_stream_tokens_bind_to_one_call_and_expire():
    token = sign_stream_token("secret", "job", "JOB-1", 300)

    assert verify_stream_token("secret", "job", "JOB-1", token)
    assert not verify_stream_token("secret", "job", "JOB-2", token), "another job"
    assert not verify_stream_token("secret", "inbound", "JOB-1", token), "another kind"
    assert not verify_stream_token("other", "job", "JOB-1", token), "another secret"
    assert not verify_stream_token("secret", "job", "JOB-1", token, now=time.time() + 400), "expired"
    assert not verify_stream_token("secret", "job", "JOB-1", "garbage")
    assert not verify_stream_token("secret", "job", "JOB-1", "")


def test_twiml_is_valid_xml_even_with_hostile_values():
    xml = stream_twiml("wss://host/api/telephony/stream", {"job_id": 'JOB-1"><Hangup/>', "token": "a&b"})
    stream = ET.fromstring(xml).find("Connect/Stream")

    assert stream.get("url") == "wss://host/api/telephony/stream"
    assert {p.get("name"): p.get("value") for p in stream.findall("Parameter")} == {"job_id": 'JOB-1"><Hangup/>', "token": "a&b"}
    assert ET.fromstring(xml).find("Hangup") is None, "a value cannot break out into new TwiML"
    assert ET.fromstring(refuse_twiml("no <thanks> & bye")).find("Say").text == "no <thanks> & bye"


@pytest.mark.parametrize(
    "given,expected",
    [("+91 98765-43210", "+919876543210"), ("+1 (415) 555-0123", "+14155550123"), ("98765 43210", None), ("+0123456789", None), ("+12", None), ("abc", None)],
)
def test_phone_numbers_must_be_international(given, expected):
    assert to_e164(given) == expected


def twilio_client(handler):
    return TwilioClient("ACtest123", AUTH_TOKEN, "+14155550123", httpx.AsyncClient(transport=httpx.MockTransport(handler)))


async def test_placing_a_call_sends_what_twilio_needs():
    seen = {}

    def handler(request: httpx.Request):
        seen["url"], seen["auth"], seen["body"] = str(request.url), request.headers["authorization"], parse_qs(request.content.decode())
        return httpx.Response(201, json={"sid": "CA999"})

    sid = await twilio_client(handler).create_call("+919876543210", "<Response/>", "https://x/status?job=J", ring_seconds=2000)
    body = seen["body"]

    assert sid == "CA999"
    assert seen["url"] == "https://api.twilio.com/2010-04-01/Accounts/ACtest123/Calls.json"
    assert seen["auth"].startswith("Basic ")
    assert body["To"] == ["+919876543210"] and body["From"] == ["+14155550123"] and body["Twiml"] == ["<Response/>"]
    assert body["Timeout"] == ["600"], "Twilio's ring limit, whatever we asked for"
    assert body["StatusCallback"] == ["https://x/status?job=J"]
    assert body["StatusCallbackEvent"] == ["initiated", "ringing", "answered", "completed"]


async def test_twilio_failures_become_errors_that_do_not_leak_credentials():
    def refuses(request):
        return httpx.Response(400, json={"code": 21211, "message": "The 'To' number is not a valid phone number."})

    def unreachable(request):
        raise httpx.ConnectError("boom", request=request)

    with pytest.raises(TwilioError, match="not a valid phone number") as bad:
        await twilio_client(refuses).create_call("+1", "<Response/>", "https://x", 30)
    with pytest.raises(TwilioError, match="could not reach Twilio") as down:
        await twilio_client(unreachable).create_call("+1", "<Response/>", "https://x", 30)

    for error in (bad.value, down.value):
        assert AUTH_TOKEN not in str(error) and "ACtest123" not in str(error)


async def test_hanging_up_ends_the_call():
    seen = {}

    def handler(request):
        seen["url"], seen["body"] = str(request.url), parse_qs(request.content.decode())
        return httpx.Response(200, json={})

    await twilio_client(handler).hang_up("CA999")

    assert seen["url"].endswith("/Calls/CA999.json") and seen["body"] == {"Status": ["completed"]}


# --- Deepgram: messages -------------------------------------------------------------------------


def test_deepgram_messages_are_understood():
    final = parse_event(json.dumps({"type": "Results", "is_final": True, "speech_final": True, "channel": {"alternatives": [{"transcript": " hello there "}]}}))
    interim = parse_event(json.dumps({"type": "Results", "is_final": False, "channel": {"alternatives": [{"transcript": "hel"}]}}))

    assert final == Transcript("hello there", True, True)
    assert interim == Transcript("hel", False, False)
    assert parse_event('{"type":"SpeechStarted"}') == SpeechStarted()
    assert parse_event('{"type":"UtteranceEnd"}') == UtteranceEnd()
    assert parse_event('{"type":"Metadata"}') is None
    assert parse_event("not json") is None
    assert parse_event('{"type":"Results","channel":{"alternatives":[]}}') == Transcript("", False, False)


# --- Deepgram: speech-to-text over a real WebSocket ------------------------------------------------


async def test_speech_to_text_speaks_deepgrams_protocol_and_hears_its_replies():
    seen = {"audio": [], "text": []}

    async def fake_deepgram(socket):
        seen["path"] = socket.request.path
        seen["auth"] = socket.request.headers.get("Authorization")

        async for message in socket:
            if isinstance(message, bytes):
                seen["audio"].append(message)
                await socket.send(json.dumps({"type": "SpeechStarted"}))
                await socket.send(json.dumps({"type": "Results", "is_final": True, "speech_final": True, "channel": {"alternatives": [{"transcript": "yes speaking"}]}}))
            else:
                seen["text"].append(json.loads(message)["type"])

    async with serve(fake_deepgram, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        stream = await DeepgramSTT(DG_KEY, "nova-3", 350, 900, base_url=f"ws://127.0.0.1:{port}").open()

        await stream.send_audio(b"\xff" * 160)
        events = []

        async for event in stream.events():
            events.append(event)
            if len(events) == 2:
                break

        await stream.aclose()
        await asyncio.sleep(0.1)

    query = parse_qs(urlsplit(seen["path"]).query)

    assert urlsplit(seen["path"]).path == "/v1/listen"
    assert seen["auth"] == f"Token {DG_KEY}"
    assert (query["encoding"], query["sample_rate"], query["channels"]) == (["mulaw"], ["8000"], ["1"]), "the phone's own audio format, no resampling"
    assert (query["model"], query["endpointing"], query["utterance_end_ms"]) == (["nova-3"], ["350"], ["900"])
    assert query["interim_results"] == ["true"] and query["vad_events"] == ["true"]
    assert seen["audio"] == [b"\xff" * 160]
    assert events == [SpeechStarted(), Transcript("yes speaking", True, True)]
    assert "CloseStream" in seen["text"], "Deepgram is told the stream is over, so it flushes"


async def test_a_deepgram_connection_failure_is_a_clean_error_without_the_key():
    with pytest.raises(DeepgramError) as error:
        await DeepgramSTT(DG_KEY, base_url="ws://127.0.0.1:1").open()

    assert DG_KEY not in str(error.value)


# --- Deepgram: text-to-speech --------------------------------------------------------------------------


async def test_text_to_speech_streams_telephone_audio_and_asks_for_it_in_the_phones_format():
    seen = {}

    def handler(request: httpx.Request):
        seen["url"], seen["auth"], seen["body"] = str(request.url), request.headers["authorization"], json.loads(request.content)
        return httpx.Response(200, content=b"\x7f" * 4000)

    speaker = DeepgramTTS(DG_KEY, httpx.AsyncClient(transport=httpx.MockTransport(handler)), "aura-2-thalia-en")
    chunks = [chunk async for chunk in speaker.synthesize("Your balance is 84,250 rupees.")]
    query = parse_qs(urlsplit(seen["url"]).query)

    assert b"".join(chunks) == b"\x7f" * 4000 and len(chunks) > 1, "arrives in pieces, not all at once"
    assert seen["auth"] == f"Token {DG_KEY}" and seen["body"] == {"text": "Your balance is 84,250 rupees."}
    assert (query["encoding"], query["sample_rate"], query["container"]) == (["mulaw"], ["8000"], ["none"])
    assert query["model"] == ["aura-2-thalia-en"]


async def test_text_to_speech_failures_are_clean_errors_without_the_key():
    def refuses(request):
        return httpx.Response(402, json={"err_msg": f"Insufficient credits for {DG_KEY}"})

    def down(request):
        raise httpx.ReadTimeout("slow", request=request)

    for handler in (refuses, down):
        speaker = DeepgramTTS(DG_KEY, httpx.AsyncClient(transport=httpx.MockTransport(handler)))

        with pytest.raises(DeepgramError) as error:
            [chunk async for chunk in speaker.synthesize("hello")]

        assert DG_KEY not in str(error.value)
