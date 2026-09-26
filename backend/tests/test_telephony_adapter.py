"""Step 9: the telephony control plane sits behind a provider-neutral adapter.

    Service -> TelephonyAdapter -> TwilioTelephony -> the existing Twilio code

Four things are shown. (1) One contract, run against the Twilio adapter and against a fake provider that has
nothing to do with Twilio. (2) The Twilio adapter does exactly what the code it wraps always did. (3) The whole
automated lifecycle (workflow, scheduler, dispatcher, job, provider, result) runs on the fake provider with no
Twilio in sight. (4) The dependency direction is enforced: workflows and the service do not import Twilio."""

import ast
import asyncio
import json
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlencode

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import JobRow, ResultRow
from app.outbound import SCHEDULED, Callee, CallJobRequest
from app.service import Service
from app.session import process_turn
from app.telephony import Telephony
from app.telephony.base import (
    CallEvent,
    CallEventKind,
    InvalidWebhook,
    PlaceCall,
    ProviderCall,
    TelephonyAdapter,
    TelephonyError,
    WebhookRequest,
)
from app.telephony.twilio import TwilioError, TwilioTelephony, status_url, verify_stream_token, ws_url
from tests.test_dispatcher import NOW, PHONE, clock, world  # noqa: F401  (fixtures)
from tests.test_scheduler import scheduler_for, tick
from tests.test_telephony_routes import PUBLIC, TOKEN, FakeTwilio

BACKEND = Path(__file__).parents[1]
E164 = "+919876543210"


# === a provider that is not Twilio ==============================================================================


class FakeTelephony:
    """A complete provider adapter with no Twilio in it. It records what it is asked and reads its own kind of
    callback (a JSON body and a header), so nothing about Twilio can be leaning on the interface."""

    name = "fake"

    def __init__(self):
        self.placed: list[PlaceCall] = []
        self.hung_up: list[ProviderCall] = []
        self.refuse = False

    async def place_call(self, request: PlaceCall) -> ProviderCall:
        if self.refuse:
            raise TelephonyError("the fake provider refused the call")

        self.placed.append(request)
        return ProviderCall(self.name, f"FAKE-{len(self.placed)}")

    async def hang_up(self, call: ProviderCall) -> None:
        self.hung_up.append(call)

    def parse_event(self, request: WebhookRequest) -> CallEvent | None:
        if request.headers.get("x-fake-signature") != "genuine":
            raise InvalidWebhook("not from the fake provider")

        body = json.loads(request.body)

        if body["kind"] == "noise":
            return None

        return CallEvent(CallEventKind(body["kind"]), request.query.get("job", ""), self.name, body.get("call"), body.get("reason"))


class Harness:
    """What the contract test needs from any adapter: a way to make it refuse, and callbacks to feed it."""

    def __init__(self, adapter, refuse, hung_up, webhook, forged, uninteresting):
        self.adapter, self.refuse, self.hung_up = adapter, refuse, hung_up
        self.webhook, self.forged, self.uninteresting = webhook, forged, uninteresting


def twilio_harness() -> Harness:
    client = FakeTwilio()
    adapter = TwilioTelephony(client, TOKEN, PUBLIC)
    validator = pytest.importorskip("twilio.request_validator").RequestValidator(TOKEN)  # an independent implementation

    def request(job_id, fields, signed=True):
        headers = {"x-twilio-signature": validator.compute_signature(status_url(PUBLIC, job_id), fields)} if signed else {}
        return WebhookRequest(body=urlencode(fields).encode(), headers=headers, query={"job": job_id})

    words = {"no_answer": "no-answer", "busy": "busy", "failed": "failed", "completed": "completed", "ringing": "ringing", "answered": "in-progress"}

    return Harness(
        adapter,
        refuse=lambda: setattr(client, "fail", True),
        hung_up=lambda: client.hangups,
        webhook=lambda kind, job, sid: request(job, {"CallSid": sid, "CallStatus": words[kind]}),
        forged=lambda kind, job, sid: request(job, {"CallSid": sid, "CallStatus": words[kind]}, signed=False),
        uninteresting=lambda job: request(job, {"CallSid": "X", "CallStatus": "queued"}),
    )


def fake_harness() -> Harness:
    adapter = FakeTelephony()

    def request(kind, job, sid, signature="genuine"):
        return WebhookRequest(body=json.dumps({"kind": kind, "call": sid}).encode(), headers={"x-fake-signature": signature}, query={"job": job})

    return Harness(
        adapter,
        refuse=lambda: setattr(adapter, "refuse", True),
        hung_up=lambda: [c.provider_call_id for c in adapter.hung_up],
        webhook=lambda kind, job, sid: request(kind, job, sid),
        forged=lambda kind, job, sid: request(kind, job, sid, signature="forged"),
        uninteresting=lambda job: WebhookRequest(body=json.dumps({"kind": "noise"}).encode(), headers={"x-fake-signature": "genuine"}, query={"job": job}),
    )


# === 1. the contract, for any provider ===========================================================================


@pytest.mark.parametrize("make", [twilio_harness, fake_harness], ids=["twilio", "fake"])
async def test_a_provider_adapter_meets_the_contract(make):
    h = make()
    adapter: TelephonyAdapter = h.adapter

    # placing a call gives back the provider's own identity for it
    call = await adapter.place_call(PlaceCall(job_id="JOB-1", to=E164, ring_seconds=90))
    assert isinstance(call, ProviderCall) and call.provider == adapter.name and call.provider_call_id

    # ending it
    assert await adapter.hang_up(call) is None
    assert h.hung_up() == [call.provider_call_id]

    # a genuine callback becomes a provider-neutral event that names the job and the provider's call
    for kind in ("no_answer", "busy", "failed", "completed", "ringing", "answered"):
        event = adapter.parse_event(h.webhook(kind, "JOB-1", call.provider_call_id))
        assert event.kind == CallEventKind(kind) and event.job_id == "JOB-1"
        assert (event.provider, event.provider_call_id) == (adapter.name, call.provider_call_id)

    # a callback that is not genuine is refused, whatever it says
    with pytest.raises(InvalidWebhook):
        adapter.parse_event(h.forged("completed", "JOB-1", call.provider_call_id))

    # a genuine callback about something the application does not use is not an event
    assert adapter.parse_event(h.uninteresting("JOB-1")) is None

    # a provider that refuses is reported as a TelephonyError, whatever its own error type
    h.refuse()
    with pytest.raises(TelephonyError):
        await adapter.place_call(PlaceCall(job_id="JOB-2", to=E164, ring_seconds=90))


def test_the_neutral_values_carry_only_what_they_need():
    assert set(PlaceCall.__dataclass_fields__) == {"job_id", "to", "ring_seconds"}, "no domain data can ride along"
    assert set(ProviderCall.__dataclass_fields__) == {"provider", "provider_call_id"}
    assert {kind.value for kind in CallEventKind} == {"ringing", "answered", "no_answer", "busy", "failed", "completed"}
    assert issubclass(TwilioError, TelephonyError), "Twilio's own error is a TelephonyError"


# === 2. the Twilio adapter does what the wrapped code always did ====================================================


async def test_the_twilio_adapter_places_the_call_exactly_as_the_service_always_did():
    client = FakeTwilio()
    adapter = TwilioTelephony(client, TOKEN, PUBLIC)
    before = time.time()
    call = await adapter.place_call(PlaceCall(job_id="JOB-7", to=E164, ring_seconds=90))

    assert (call.provider, call.provider_call_id) == ("twilio", "CA_fake_1")
    (placed,) = client.calls
    assert placed["to"] == E164 and placed["ring_seconds"] == 90
    assert placed["status_callback"] == f"{PUBLIC}/api/telephony/status?job=JOB-7"

    stream = ET.fromstring(placed["twiml"]).find("Connect/Stream")
    params = {p.get("name"): p.get("value") for p in stream.findall("Parameter")}
    assert stream.get("url") == ws_url(PUBLIC) == "wss://agent.example/api/telephony/stream"
    assert params["kind"] == "job" and params["job_id"] == "JOB-7"
    assert verify_stream_token(TOKEN, "job", "JOB-7", params["token"])
    assert not verify_stream_token(TOKEN, "job", "JOB-8", params["token"]), "the token is for this job only"
    assert not verify_stream_token("another-secret", "job", "JOB-7", params["token"])
    # valid for the ring time plus a minute, and no longer
    assert verify_stream_token(TOKEN, "job", "JOB-7", params["token"], now=before + 90 + 55)
    assert not verify_stream_token(TOKEN, "job", "JOB-7", params["token"], now=time.time() + 90 + 65)


async def test_the_twilio_adapter_reports_twilios_refusal_as_the_error_it_always_raised():
    client = FakeTwilio()
    client.fail = True

    with pytest.raises(TwilioError, match="not a valid phone number"):
        await TwilioTelephony(client, TOKEN, PUBLIC).place_call(PlaceCall(job_id="J", to="+1", ring_seconds=30))


async def test_the_twilio_adapter_hangs_up_by_twilios_call_sid():
    client = FakeTwilio()
    await TwilioTelephony(client, TOKEN, PUBLIC).hang_up(ProviderCall("twilio", "CA123"))

    assert client.hangups == ["CA123"]


@pytest.mark.parametrize("status, kind, reason", [
    ("ringing", CallEventKind.RINGING, None),
    ("in-progress", CallEventKind.ANSWERED, None),
    ("busy", CallEventKind.BUSY, "busy"),
    ("no-answer", CallEventKind.NO_ANSWER, "no_answer"),
    ("canceled", CallEventKind.NO_ANSWER, "canceled"),
    ("failed", CallEventKind.FAILED, "telephony_error"),
    ("completed", CallEventKind.COMPLETED, None),
])
def test_every_twilio_status_becomes_the_same_neutral_event_the_service_used_to_derive(status, kind, reason):
    h = twilio_harness()
    fields = {"CallSid": "CA_1", "CallStatus": status}
    validator = pytest.importorskip("twilio.request_validator").RequestValidator(TOKEN)
    signature = validator.compute_signature(status_url(PUBLIC, "JOB-3"), fields)
    event = h.adapter.parse_event(WebhookRequest(urlencode(fields).encode(), {"x-twilio-signature": signature}, {"job": "JOB-3"}))

    assert event == CallEvent(kind, "JOB-3", "twilio", "CA_1", reason)


@pytest.mark.parametrize("status", ["initiated", "queued", "", "something-new"])
def test_statuses_the_service_never_acted_on_are_still_not_events(status):
    h = twilio_harness()
    fields = {"CallSid": "CA_1", "CallStatus": status}
    validator = pytest.importorskip("twilio.request_validator").RequestValidator(TOKEN)
    request = WebhookRequest(urlencode(fields).encode(), {"x-twilio-signature": validator.compute_signature(status_url(PUBLIC, "J"), fields)}, {"job": "J"})

    assert h.adapter.parse_event(request) is None


def test_the_twilio_adapter_believes_only_a_request_signed_for_exactly_its_url_and_token():
    h = twilio_harness()
    validator = pytest.importorskip("twilio.request_validator").RequestValidator(TOKEN)
    fields = {"CallSid": "CA_1", "CallStatus": "failed"}
    body = urlencode(fields).encode()
    good = validator.compute_signature(status_url(PUBLIC, "JOB-A"), fields)

    assert h.adapter.parse_event(WebhookRequest(body, {"x-twilio-signature": good}, {"job": "JOB-A"})).kind == CallEventKind.FAILED
    for headers, query in (
        ({}, {"job": "JOB-A"}),  # unsigned
        ({"x-twilio-signature": "AAAA"}, {"job": "JOB-A"}),  # garbage
        ({"x-twilio-signature": good}, {"job": "JOB-B"}),  # signed for another job's URL
        ({"x-twilio-signature": pytest.importorskip("twilio.request_validator").RequestValidator("other").compute_signature(status_url(PUBLIC, "JOB-A"), fields)}, {"job": "JOB-A"}),  # another token
    ):
        with pytest.raises(InvalidWebhook):
            h.adapter.parse_event(WebhookRequest(body, headers, query))

    tampered = urlencode({**fields, "CallStatus": "completed"}).encode()
    with pytest.raises(InvalidWebhook):
        h.adapter.parse_event(WebhookRequest(tampered, {"x-twilio-signature": good}, {"job": "JOB-A"}))


def test_the_telephony_bundle_uses_twilio_unless_told_otherwise_and_keeps_its_urls():
    client, kwargs = FakeTwilio(), dict(open_listener=None, speaker=None, public_api_url=PUBLIC, auth_token=TOKEN)
    default = Telephony(twilio=client, **kwargs)
    fake = FakeTelephony()
    given = Telephony(twilio=None, adapter=fake, **kwargs)

    assert isinstance(default.adapter, TwilioTelephony) and default.adapter.client is client
    assert given.adapter is fake and given.twilio is None
    assert default.ws_url() == "wss://agent.example/api/telephony/stream"
    assert default.status_url("JOB 1") == f"{PUBLIC}/api/telephony/status?job=JOB%201"
    assert default.voice_url() == f"{PUBLIC}/api/telephony/voice"


# === 3. the whole automated lifecycle on a provider that is not Twilio ===============================================


@pytest.fixture
async def fake_world(world):
    """The dispatcher rig with its telephony swapped for the fake provider. FakeTwilio is left in place only so
    the tests can prove it is never touched."""
    real = world.service.telephony
    world.fake = FakeTelephony()
    world.service.telephony = Telephony(
        twilio=None, open_listener=real.open_listener, speaker=real.speaker, public_api_url=PUBLIC, auth_token=TOKEN, adapter=world.fake
    )
    return world


def results(world):
    with Session(world.repo.engine) as db:
        return list(db.scalars(select(ResultRow)))


async def say(session, text):
    return [event async for event in process_turn(session, text)]


async def test_an_automated_call_runs_from_workflow_to_result_on_a_provider_that_is_not_twilio(fake_world):
    world = fake_world
    ids = world.seed()

    report = await tick(scheduler_for(world), world)  # workflow -> scheduler -> engine -> job -> dispatcher -> provider

    assert (report.jobs_created, report.dispatch_report.dispatched) == (1, 1)
    (job,) = world.repo.list_jobs(10)
    assert job["status"] == "ringing" and job["twilio_call_sid"] == "FAKE-1", "the provider's call id is stored (the column keeps its old name)"
    assert world.fake.placed == [PlaceCall(job["id"], E164, 120)]
    assert world.twilio.calls == [] and world.twilio.hangups == [], "Twilio was never involved"
    assert (job["organization_id"], job["agent_id"], job["contact_id"], job["workflow_id"]) == (ids.organization, ids.agent, ids.contact, ids.workflow)

    session, greeting = await world.service.answer_job_phone(job["id"], "FAKE-1")
    assert world.repo.get_job(job["id"])["status"] == "in_progress" and "Priya Sharma" in greeting
    await say(session, "yes speaking")
    await world.service.finalize(session)

    final = world.repo.get_job(job["id"])
    assert (final["status"], final["twilio_call_sid"]) == ("completed", "FAKE-1")
    (result,) = results(world)
    assert (result.job_id, result.call_id, result.organization_id) == (job["id"], final["call_id"], ids.organization)


async def test_a_manual_phone_job_is_placed_through_the_same_interface(fake_world):
    world = fake_world
    view, created = await world.service.create_job(CallJobRequest(
        reference="manual-1", profile_id="bank", channel="phone", callee=Callee(name="Priya Sharma", phone=PHONE), reason="x",
    ))

    assert created and view["status"] == "ringing"
    assert world.fake.placed == [PlaceCall(view["job_id"], E164, world.fake.placed[0].ring_seconds)]
    assert 10 <= world.fake.placed[0].ring_seconds <= 120 and world.twilio.calls == []
    assert world.repo.get_job(view["job_id"])["twilio_call_sid"] == "FAKE-1"


async def test_a_provider_that_refuses_ends_the_job_failed_exactly_as_twilio_does(fake_world):
    world = fake_world
    world.fake.refuse = True
    world.seed(callback=True)
    report = await tick(scheduler_for(world), world)
    await world.service.drain()

    assert (report.dispatch_report.failed, report.dispatch_report.dispatched) == (1, 0)
    (job,) = world.repo.list_jobs(10)
    assert (job["status"], job["end_reason"], job["twilio_call_sid"]) == ("failed", "telephony_error", None)
    assert len(world.receiver.calls) == 1 and results(world) == []
    world.fake.refuse = False
    assert (await tick(scheduler_for(world), world, NOW + timedelta(minutes=1))).dispatch_report.selected == 0, "and it is not retried"


@pytest.mark.parametrize("kind, reason, status, end_reason", [
    (CallEventKind.NO_ANSWER, "no_answer", "no_answer", "no_answer"),
    (CallEventKind.NO_ANSWER, "canceled", "no_answer", "canceled"),
    (CallEventKind.BUSY, "busy", "no_answer", "busy"),
    (CallEventKind.BUSY, None, "no_answer", "busy"),
    (CallEventKind.FAILED, "telephony_error", "failed", "telephony_error"),
    (CallEventKind.FAILED, None, "failed", "telephony_error"),
])
async def test_a_neutral_event_ends_a_ringing_job_the_way_twilios_statuses_always_did(fake_world, kind, reason, status, end_reason):
    world = fake_world
    world.seed(callback=True)
    await tick(scheduler_for(world), world)
    (job,) = world.repo.list_jobs(10)

    await world.service.handle_call_event(CallEvent(kind, job["id"], "fake", "FAKE-1", reason))
    await world.service.drain()

    row = world.repo.get_job(job["id"])
    assert (row["status"], row["end_reason"]) == (status, end_reason)
    assert world.fake.hung_up == [], "the provider already ended it: nothing to hang up"
    assert len(world.receiver.calls) == 1 and results(world) == []


@pytest.mark.parametrize("kind", [CallEventKind.RINGING, CallEventKind.ANSWERED, CallEventKind.COMPLETED])
async def test_events_that_are_not_endings_change_a_ringing_job_not_at_all(fake_world, kind):
    world = fake_world
    world.seed()
    await tick(scheduler_for(world), world)
    (job,) = world.repo.list_jobs(10)
    before = world.repo.get_job(job["id"])

    await world.service.handle_call_event(CallEvent(kind, job["id"], "fake", "FAKE-1"))

    assert world.repo.get_job(job["id"]) == before


async def test_events_about_another_call_an_unknown_job_or_a_web_job_are_ignored(fake_world):
    world = fake_world
    world.seed()
    await tick(scheduler_for(world), world)
    (job,) = world.repo.list_jobs(10)
    before = world.repo.get_job(job["id"])

    await world.service.handle_call_event(CallEvent(CallEventKind.FAILED, job["id"], "fake", "SOMEONE-ELSES-CALL"))
    await world.service.handle_call_event(CallEvent(CallEventKind.FAILED, "JOB-NOPE", "fake", "FAKE-1"))
    assert world.repo.get_job(job["id"]) == before

    web, _ = await world.service.create_job(CallJobRequest(reference="w", profile_id="bank", channel="web", callee=Callee(name="P", phone=PHONE), reason="x"))
    await world.service.handle_call_event(CallEvent(CallEventKind.FAILED, web["job_id"], "fake", None))
    assert world.repo.get_job(web["job_id"])["status"] == "ringing"


async def test_a_completed_event_finalizes_a_call_whose_line_the_provider_says_is_gone(fake_world):
    world = fake_world
    world.seed()
    await tick(scheduler_for(world), world)
    (job,) = world.repo.list_jobs(10)
    session, _ = await world.service.answer_job_phone(job["id"], "FAKE-1")

    await world.service.handle_call_event(CallEvent(CallEventKind.COMPLETED, job["id"], "fake", "FAKE-1"))

    assert session.ended and session.end_reason == "hangup"
    assert world.repo.get_job(job["id"])["status"] == "completed" and len(results(world)) == 1


async def test_a_ring_timeout_hangs_up_the_ringing_phone_through_the_adapter(fake_world):
    world = fake_world
    world.seed()
    await tick(scheduler_for(world), world)
    (job,) = world.repo.list_jobs(10)

    world.clock.to(NOW + timedelta(seconds=121))
    await world.service.sweep()

    assert world.repo.get_job(job["id"])["status"] == "no_answer"
    assert world.fake.hung_up == [ProviderCall("fake", "FAKE-1")]
    assert world.twilio.hangups == []


async def test_a_scheduled_job_is_still_never_dialed_by_the_provider_layer(fake_world):
    world = fake_world
    ids = world.seed()
    job = await world.scheduled(ids)  # the engine alone

    assert world.repo.get_job(job["id"])["status"] == SCHEDULED and world.fake.placed == []


# === 4. the dependency direction ===================================================================================


def imports(path: Path) -> set[str]:
    found: set[str] = set()

    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            found |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            found.add(node.module or "")
            found |= {f"{node.module}.{alias.name}" for alias in node.names}

    return found


@pytest.mark.parametrize("path", sorted(BACKEND.joinpath("app/workflows").glob("*.py")), ids=lambda p: p.name)
def test_the_workflow_layer_imports_no_telephony_provider(path):
    assert not [m for m in imports(path) if "twilio" in m.lower() or "deepgram" in m.lower()], path.name


@pytest.mark.parametrize("name", ["scheduler", "dispatcher", "engine", "eligibility", "triggers", "results", "clock"])
def test_the_scheduler_dispatcher_and_engine_are_telephony_free(name):
    assert not [m for m in imports(BACKEND / f"app/workflows/{name}.py") if "telephony" in m], name


def test_the_service_depends_on_the_interface_and_never_on_twilio():
    path = BACKEND / "app/service.py"
    tree = ast.parse(path.read_text())
    used = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)} | {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}

    assert not [m for m in imports(path) if "twilio" in m.lower()], imports(path)
    assert "app.telephony.base" in imports(path)
    assert not used & {"twilio", "TwilioError", "TwilioClient", "TwilioTelephony", "stream_twiml", "sign_stream_token", "verify_stream_token", "validate_signature", "auth_token", "ws_url", "status_url"}
    assert "handle_twilio_status" not in {n.name for n in ast.walk(tree) if isinstance(n, (ast.AsyncFunctionDef, ast.FunctionDef))}


def test_importing_the_service_or_the_workflows_loads_no_provider():
    code = "import app.service, app.workflows, sys; print(sorted(m for m in sys.modules if m.startswith('app.telephony')))"
    out = subprocess.run([sys.executable, "-c", code], cwd=BACKEND, capture_output=True, text=True, timeout=60)

    assert out.returncode == 0, out.stderr
    assert ast.literal_eval(out.stdout.strip()) == ["app.telephony", "app.telephony.base"], "only the neutral interface"


def test_no_class_in_the_service_defines_a_method_twice():
    tree = ast.parse((BACKEND / "app/service.py").read_text())

    for cls in (n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)):
        names = [n.name for n in cls.body if isinstance(n, (ast.AsyncFunctionDef, ast.FunctionDef))]
        assert len(names) == len(set(names)), [n for n in names if names.count(n) > 1]


def test_the_phone_helper_belongs_to_no_provider_and_is_still_importable_from_twilio():
    from app.phone import to_e164
    from app.telephony.twilio import to_e164 as from_twilio

    assert to_e164 is from_twilio and to_e164("+91 98765-43210") == E164
    assert imports(BACKEND / "app/phone.py") == {"re"}


def test_the_database_column_keeps_its_name():
    assert "twilio_call_sid" in JobRow.__table__.columns
