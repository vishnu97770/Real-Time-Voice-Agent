"""Phone calls through the real HTTP and WebSocket endpoints, with a fake phone network.

The fake Twilio records the calls we place and is "answered" by these tests opening
the audio WebSocket exactly as Twilio would. The fake speech recognition turns audio
bytes b"SAY:<words>" into transcripts, and the fake voice turns text into b"S:<text>".
"""

import base64
import json
import time
import xml.etree.ElementTree as ET
from urllib.parse import urlencode

import httpx
import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.brains.base import Propose, TextDelta, ToolCall
from app.config import Settings
from app.db import Repository
from app.main import create_app
from app.outbound import verify_signature
from app.telephony import Telephony
from app.telephony.deepgram import Transcript
from app.telephony.twilio import TwilioError, sign_stream_token, verify_stream_token
from tests.helpers import ScriptedBrain
from tests.test_outbound import Receiver
from tests.test_telephony_pipeline import FakeListener, FakeSpeaker

KEY = {"X-API-Key": "test-key"}
TOKEN = "tw-auth-token"
PUBLIC = "https://agent.example"
PHONE = "+91 98765 43210"


class FakeTwilio:
    def __init__(self):
        self.calls, self.hangups, self.fail = [], [], False

    async def create_call(self, to, twiml, status_callback, ring_seconds):
        if self.fail:
            raise TwilioError("Twilio said 400: The 'To' number is not a valid phone number.")

        self.calls.append({"to": to, "twiml": twiml, "status_callback": status_callback, "ring_seconds": ring_seconds})
        return f"CA_fake_{len(self.calls)}"

    async def hang_up(self, sid):
        self.hangups.append(sid)


class HearsWhatItIsTold(FakeListener):
    async def send_audio(self, mulaw):
        await super().send_audio(mulaw)

        if mulaw.startswith(b"SAY:"):
            self.push(Transcript(mulaw[4:].decode(), True, True))
        elif mulaw.startswith(b"INTERIM:"):
            self.push(Transcript(mulaw[8:].decode(), False, False))


async def bank_outbound(ctx):
    if ctx.outbound and "confirmed who they are" in ctx.text:
        result = await ctx.run_tool("get_flagged_activity", {})
        yield ToolCall("get_flagged_activity", {}, result)
        yield TextDelta("I'm calling about a charge at TechMart in Singapore. Was that you?")
    elif "not me" in ctx.text:
        yield Propose("freeze_card", {"card": "credit"})
    else:
        yield TextDelta("Understood.")


class World:
    def __init__(self, inbound=False, configured=True, twilio=None):
        self.repo = Repository("sqlite://")
        self.receiver = Receiver()
        self.twilio = twilio or FakeTwilio()
        self.listeners = []

        async def open_listener():
            self.listeners.append(HearsWhatItIsTold())
            return self.listeners[-1]

        telephony = Telephony(
            twilio=self.twilio, open_listener=open_listener, speaker=FakeSpeaker(), public_api_url=PUBLIC,
            auth_token=TOKEN, barge_in_min_words=2, inbound_enabled=inbound, inbound_profile="bank",
        ) if configured else None
        settings = Settings(
            database_url="sqlite://", api_key="test-key", auth_required=False, allow_private_callbacks=True,
            callback_backoff_seconds=0, sweep_interval_seconds=60, public_api_url=PUBLIC, public_base_url="https://console.example",
        )
        self.app = create_app(
            ScriptedBrain(bank_outbound), settings, repo=self.repo,
            http=httpx.AsyncClient(transport=httpx.MockTransport(self.receiver)), telephony=telephony,
        )

    def __enter__(self):
        self.client = TestClient(self.app).__enter__()
        return self

    def __exit__(self, *exc):
        self.client.__exit__(*exc)

    def place(self, **overrides):
        body = {
            "profile_id": "bank", "channel": "phone", "callee": {"name": "Priya Sharma", "phone": PHONE},
            "reason": "unusual activity on your card", "callback_url": "https://crm.example/hooks", **overrides,
        }
        return self.client.post("/api/call-jobs", json=body, headers=KEY)

    def job(self, job_id):
        return self.client.get(f"/api/call-jobs/{job_id}", headers=KEY).json()

    def params(self, call_index=0):
        stream = ET.fromstring(self.twilio.calls[call_index]["twiml"]).find("Connect/Stream")
        return {p.get("name"): p.get("value") for p in stream.findall("Parameter")}

    def connect(self, params, call_sid="CA_fake_1"):
        ws = self.client.websocket_connect("/api/telephony/stream")
        ws = ws.__enter__()
        ws.send_text(json.dumps({"event": "connected", "protocol": "Call", "version": "1.0.0"}))
        ws.send_text(json.dumps({"event": "start", "streamSid": "MZ1", "start": {"streamSid": "MZ1", "callSid": call_sid, "customParameters": params}}))
        return ws

    def expect_dropped(self, params, call_sid="CA_fake_1"):
        """The server closes this connection. Always leave the session cleanly, or the
        test client waits on it forever when the test ends."""
        ws = self.connect(params, call_sid)

        try:
            with pytest.raises(WebSocketDisconnect):
                ws.receive_text()
        finally:
            ws.__exit__(None, None, None)

    def wait_until(self, condition, seconds=5):
        end = time.time() + seconds

        while not condition():
            assert time.time() < end, "timed out"
            time.sleep(0.02)


def hear(ws, until_mark=True, limit=60):
    """Read what the caller would hear, up to the next mark. Returns (sentences, mark_name)."""
    said = []

    for _ in range(limit):
        message = json.loads(ws.receive_text())

        if message["event"] == "media":
            audio = base64.b64decode(message["media"]["payload"])
            said += [audio[2:].decode()] if audio.startswith(b"S:") else []
        elif message["event"] == "mark":
            return " ".join(said), message["mark"]["name"]
        elif message["event"] == "clear":
            said.append("<CLEAR>")

    raise AssertionError("no mark arrived")


def speak(ws, words):
    ws.send_text(json.dumps({"event": "media", "streamSid": "MZ1", "media": {"payload": base64.b64encode(b"SAY:" + words.encode()).decode()}}))


def play_finished(ws, mark):
    ws.send_text(json.dumps({"event": "mark", "streamSid": "MZ1", "mark": {"name": mark}}))


# --- placing the call ------------------------------------------------------------------------------------


def test_a_phone_job_rings_the_number_through_twilio_with_a_stream_only_this_call_can_join():
    with World() as w:
        response = w.place(reference="crm-9")
        job = response.json()

        assert response.status_code == 201 and job["status"] == "ringing" and job["channel"] == "phone"
        assert "answer_url" not in job, "there is no link to open: someone picks up a phone"
        assert job["callee"]["phone"] == "***3210"

        (call,) = w.twilio.calls
        assert call["to"] == "+919876543210", "normalised to international format"
        assert call["status_callback"] == f"{PUBLIC}/api/telephony/status?job={job['job_id']}"
        assert 10 <= call["ring_seconds"] <= 120

        stream = ET.fromstring(call["twiml"]).find("Connect/Stream")
        params = w.params()
        assert stream.get("url") == "wss://agent.example/api/telephony/stream"
        assert params["kind"] == "job" and params["job_id"] == job["job_id"]
        assert verify_stream_token(TOKEN, "job", job["job_id"], params["token"])
        assert w.repo.get_job(job["job_id"])["twilio_call_sid"] == "CA_fake_1"

        again = w.place(reference="crm-9")
        assert again.status_code == 200 and len(w.twilio.calls) == 1, "the same reference never rings twice"
        assert w.client.get("/api/health").json()["telephony"] is True


def test_phone_jobs_are_refused_clearly_when_they_cannot_work():
    with World(configured=False) as w:
        response = w.place()
        assert response.status_code == 422 and "not configured" in response.text
        assert w.client.get("/api/health").json()["telephony"] is False
        assert w.client.post("/api/telephony/voice").status_code in (404, 405), "no telephony endpoints when it is off"

    with World() as w:
        bad = w.place(callee={"name": "P", "phone": "98765 43210"})
        assert bad.status_code == 422 and "international format" in bad.text
        assert w.twilio.calls == []


def test_if_twilio_refuses_the_job_fails_cleanly_and_the_business_is_told():
    twilio = FakeTwilio()
    twilio.fail = True

    with World(twilio=twilio) as w:
        response = w.place()
        job = response.json()

        assert response.status_code == 201 and job["status"] == "failed" and job["end_reason"] == "telephony_error"
        w.wait_until(lambda: w.job(job["job_id"])["callback"]["status"] == "delivered")
        assert w.receiver.bodies[0]["job"]["status"] == "failed"
        assert w.job(job["job_id"])["result"]["outcome"] == "failed"


# --- the call itself ---------------------------------------------------------------------------------------


def test_a_full_phone_call_from_ring_to_signed_result():
    with World() as w:
        job = w.place().json()
        ws = w.connect(w.params())

        opener, mark = hear(ws)
        assert opener.startswith("Hello, this is an AI assistant.")
        assert "calling from Northbridge Bank" in opener and "Am I speaking with Priya Sharma?" in opener
        assert "TechMart" not in opener, "nothing about the callee before they confirm who they are"
        assert w.job(job["job_id"])["status"] == "in_progress"
        play_finished(ws, mark)

        speak(ws, "yes speaking")
        reply, mark = hear(ws)
        assert "TechMart" in reply and "Was that you?" in reply
        play_finished(ws, mark)

        speak(ws, "that was not me")
        prompt, mark = hear(ws)
        assert "freeze your credit card" in prompt and "yes to confirm" in prompt
        play_finished(ws, mark)

        speak(ws, "yes please")
        done, mark = hear(ws)
        assert "is now frozen" in done

        ws.send_text(json.dumps({"event": "stop", "streamSid": "MZ1"}))  # the callee hangs up
        ws.__exit__(None, None, None)

        w.wait_until(lambda: w.job(job["job_id"])["callback"]["status"] == "delivered")
        final = w.job(job["job_id"])
        result = final["result"]

        assert final["status"] == "completed" and final["end_reason"] == "hangup"
        assert result["channel"] == "phone" and result["outcome"] == "action_completed"
        assert result["outbound"]["identity"] == "confirmed" and result["disclosure_given"] is True
        assert w.twilio.hangups == [], "the callee hung up: there is no line left to hang up"

        (delivery,) = w.receiver.calls
        assert verify_signature("test-key", delivery.content, delivery.headers["X-Signature"])


def test_a_wrong_number_hears_nothing_private_and_the_agent_hangs_up_when_it_has_finished_speaking():
    with World() as w:
        job = w.place().json()
        ws = w.connect(w.params())
        _, mark = hear(ws)
        play_finished(ws, mark)

        speak(ws, "no, wrong number")
        goodbye, mark = hear(ws)
        assert "won't take any more of your time" in goodbye and "TechMart" not in goodbye

        assert w.twilio.hangups == [], "not while the goodbye is still playing"
        play_finished(ws, mark)  # ...it has now been heard
        w.wait_until(lambda: w.twilio.hangups == ["CA_fake_1"])
        ws.__exit__(None, None, None)

        w.wait_until(lambda: w.job(job["job_id"])["status"] == "wrong_party")
        assert w.job(job["job_id"])["result"]["outcome"] == "wrong_party"


def test_talking_over_the_agent_over_the_phone_stops_the_audio():
    with World() as w:
        w.place()
        ws = w.connect(w.params())
        _, mark = hear(ws)
        play_finished(ws, mark)
        speak(ws, "yes speaking")
        hear_reply_started = json.loads(ws.receive_text())  # the reply begins to play
        assert hear_reply_started["event"] == "media"

        ws.send_text(json.dumps({"event": "media", "streamSid": "MZ1", "media": {"payload": base64.b64encode(b"INTERIM:hold on a second").decode()}}))
        events = []

        for _ in range(40):
            events.append(json.loads(ws.receive_text())["event"])
            if "clear" in events:
                break

        assert "clear" in events, "Twilio is told to throw away audio that has not played yet"
        ws.__exit__(None, None, None)


# --- who is allowed to connect ---------------------------------------------------------------------------------


def test_a_stream_without_a_valid_token_is_dropped_without_touching_anything():
    with World() as w:
        job = w.place().json()
        other = w.place(callee={"name": "Someone", "phone": "+14155550123"}).json()

        for params in (
            {"kind": "job", "job_id": job["job_id"], "token": "garbage"},
            {"kind": "job", "job_id": job["job_id"], "token": w.params(1)["token"]},  # a token for another job
            {"kind": "job", "job_id": job["job_id"], "token": sign_stream_token("wrong-secret", "job", job["job_id"])},
            {"kind": "job", "job_id": job["job_id"], "token": sign_stream_token(TOKEN, "job", job["job_id"], ttl_seconds=-5)},
            {"kind": "unknown"},
            {},
        ):
            w.expect_dropped(params)

        assert w.job(job["job_id"])["status"] == "ringing" and w.job(other["job_id"])["status"] == "ringing"
        assert len(w.app.state.service.store) == 0
        assert w.twilio.hangups == []


def test_garbage_on_the_socket_does_not_take_the_server_down():
    with World() as w:
        w.place()

        with w.client.websocket_connect("/api/telephony/stream") as ws:
            ws.send_text("this is not json")

            with pytest.raises(WebSocketDisconnect):
                ws.receive_text()

        assert w.client.get("/api/health").status_code == 200


def test_a_call_that_is_no_longer_ringing_cannot_be_joined_and_the_line_is_hung_up():
    with World() as w:
        job = w.place().json()
        w.repo.update_job(job["job_id"], expires_at=time.time() - 1)  # nobody answered in time
        w.expect_dropped(w.params(), call_sid="CA_late")

        w.wait_until(lambda: "CA_late" in w.twilio.hangups)
        assert w.job(job["job_id"])["status"] == "no_answer"


def test_a_phone_job_cannot_be_answered_by_web_link():
    with World() as w:
        job = w.place().json()
        response = w.client.post(f"/api/call-jobs/{job['job_id']}/answer", json={"token": "anything"})

        assert response.status_code == 404


# --- Twilio's status webhook -----------------------------------------------------------------------------------------


def status_call(w, job_id, fields, sign=True, url=None):
    validator = pytest.importorskip("twilio.request_validator").RequestValidator(TOKEN)
    url = url or f"{PUBLIC}/api/telephony/status?job={job_id}"
    headers = {"X-Twilio-Signature": validator.compute_signature(url, fields)} if sign else {}
    return w.client.post(f"/api/telephony/status?job={job_id}", content=urlencode(fields), headers={"Content-Type": "application/x-www-form-urlencoded", **headers})


@pytest.mark.parametrize(
    "call_status,expected,reason",
    [("no-answer", "no_answer", "no_answer"), ("busy", "no_answer", "busy"), ("canceled", "no_answer", "canceled"), ("failed", "failed", "telephony_error")],
)
def test_twilio_reports_that_nobody_picked_up_and_the_job_and_the_business_hear_about_it(call_status, expected, reason):
    with World() as w:
        job = w.place().json()
        response = status_call(w, job["job_id"], {"CallSid": "CA_fake_1", "CallStatus": call_status})

        assert response.status_code == 204
        w.wait_until(lambda: w.job(job["job_id"])["callback"]["status"] == "delivered")
        final = w.job(job["job_id"])
        assert final["status"] == expected and final["end_reason"] == reason
        assert w.receiver.bodies[0]["job"]["status"] == expected
        assert w.twilio.hangups == [], "Twilio has already ended it: nothing to hang up"


def test_forged_or_mismatched_status_webhooks_change_nothing():
    with World() as w:
        job = w.place().json()
        jid = job["job_id"]

        assert status_call(w, jid, {"CallSid": "CA_fake_1", "CallStatus": "failed"}, sign=False).status_code == 403
        assert status_call(w, jid, {"CallSid": "CA_fake_1", "CallStatus": "failed"}, url="https://evil.example/x").status_code == 403
        assert w.client.post(f"/api/telephony/status?job={jid}", content="CallStatus=failed", headers={"X-Twilio-Signature": "AAAA"}).status_code == 403
        assert status_call(w, jid, {"CallSid": "CA_someone_elses", "CallStatus": "failed"}).status_code == 204  # signed, but not our call
        assert status_call(w, "JOB-NOPE", {"CallSid": "CA_fake_1", "CallStatus": "failed"}).status_code == 204

        assert w.job(jid)["status"] == "ringing", "nothing above was allowed to end the call"


def test_if_the_audio_stream_is_lost_twilios_completed_report_still_finishes_the_job():
    with World() as w:
        job = w.place().json()
        ws = w.connect(w.params())
        _, mark = hear(ws)
        play_finished(ws, mark)
        speak(ws, "yes speaking")
        hear(ws)

        status_call(w, job["job_id"], {"CallSid": "CA_fake_1", "CallStatus": "completed"})
        w.wait_until(lambda: w.job(job["job_id"])["status"] == "completed")

        assert w.job(job["job_id"])["end_reason"] == "hangup"
        ws.__exit__(None, None, None)


def test_a_ring_timeout_also_stops_the_phone_ringing():
    with World() as w:
        job = w.place().json()
        w.repo.update_job(job["job_id"], expires_at=time.time() - 1)

        assert w.job(job["job_id"])["status"] == "no_answer"
        assert w.twilio.hangups == ["CA_fake_1"], "we gave up, so the phone must stop ringing"


# --- inbound calls -------------------------------------------------------------------------------------------------------


def voice_call(w, sign=True):
    validator = pytest.importorskip("twilio.request_validator").RequestValidator(TOKEN)
    fields = {"CallSid": "CA_in_1", "From": "+14155550999", "To": "+14155550123"}
    headers = {"X-Twilio-Signature": validator.compute_signature(f"{PUBLIC}/api/telephony/voice", fields)} if sign else {}
    return w.client.post("/api/telephony/voice", content=urlencode(fields), headers={"Content-Type": "application/x-www-form-urlencoded", **headers})


def test_the_incoming_line_is_closed_by_default_and_only_twilio_can_ask_about_it():
    with World() as w:
        assert voice_call(w, sign=False).status_code == 403

        twiml = ET.fromstring(voice_call(w).text)
        assert twiml.find("Connect") is None and twiml.find("Hangup") is not None
        assert "does not take incoming calls" in twiml.find("Say").text

        forged = sign_stream_token(TOKEN, "inbound", "bank")  # even a valid-looking token is useless while it is closed
        w.expect_dropped({"kind": "inbound", "profile": "bank", "token": forged}, call_sid="CA_in_1")

        assert len(w.app.state.service.store) == 0


def test_an_incoming_call_can_be_taken_when_the_operator_switches_the_line_on():
    with World(inbound=True) as w:
        stream = ET.fromstring(voice_call(w).text).find("Connect/Stream")
        params = {p.get("name"): p.get("value") for p in stream.findall("Parameter")}
        assert params["kind"] == "inbound" and verify_stream_token(TOKEN, "inbound", "bank", params["token"])

        ws = w.connect(params, call_sid="CA_in_1")
        greeting, mark = hear(ws)
        assert greeting.startswith("Hello, this is an AI assistant.") and "Northbridge Bank" in greeting

        play_finished(ws, mark)
        speak(ws, "hello")
        assert "Understood." in hear(ws)[0]

        ws.send_text(json.dumps({"event": "stop", "streamSid": "MZ1"}))
        ws.__exit__(None, None, None)

        rows = w.client.get("/api/calls", headers=KEY).json()
        w.wait_until(lambda: len(w.client.get("/api/calls", headers=KEY).json()) == 1)
        row = w.client.get("/api/calls", headers=KEY).json()[0]
        assert row["direction"] == "inbound"
        assert w.client.get(f"/api/calls/{row['call_id']}/result", headers=KEY).json()["channel"] == "phone"
