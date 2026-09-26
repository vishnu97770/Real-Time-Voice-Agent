"""Step 5: the dispatcher turns scheduled call jobs into ringing ones, or closes them, through the
existing telephony path. Real migrated database (foreign keys enforced, real threads), fake Twilio.

Two clocks matter and both are pinned: the `now` passed to the dispatcher (the business's time) and
the wall clock the service reads for expires_at, the sweeper and _dial's ring time (patched to follow
the same fake time), so nothing here depends on the real date."""

import ast
import asyncio
import json
import threading
import xml.etree.ElementTree as ET
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import event
from sqlalchemy.orm import Session

from app.config import Settings
from app.db import Repository
from app.main import create_app
from app.models import Agent, Contact, Organization, Workflow
from app.outbound import SCHEDULED, Callee, CallJobRequest, verify_signature
from app.telephony import Telephony
from app.telephony.twilio import verify_stream_token
from app.workflows import DispatchStatus, JobDispatcher, Reason, WorkflowEngine
from tests.helpers import ScriptedBrain, migrate
from tests.test_call_job_links import values
from tests.test_outbound import Receiver
from tests.test_telephony_pipeline import FakeListener, FakeSpeaker
from tests.test_telephony_routes import PUBLIC, TOKEN, FakeTwilio
from tests.test_workflow_engine import ACTION, TRIGGER, at

NOW = at(24, 10)  # the moment the workflow is first due
PHONE = "+919876543210"
D = DispatchStatus


class Clock:
    """The service's wall clock, following whatever the test says it is."""

    def __init__(self):
        self.now = NOW

    def to(self, moment):
        self.now = moment

    def __call__(self) -> float:
        return self.now.timestamp()


@pytest.fixture
def clock(monkeypatch):
    fake = Clock()
    monkeypatch.setattr("app.service.time", SimpleNamespace(time=fake))
    return fake


class World:
    def __init__(self, tmp_path, clock: Clock, configured: bool = True):
        self.clock = clock
        self._ids: dict[str, SimpleNamespace] = {}
        url = f"sqlite:///{tmp_path / 'dispatch.db'}"
        migrate(url)
        self.repo = Repository(url)  # the application's own: foreign keys enforced, real connections
        self.receiver = Receiver()
        self.twilio = FakeTwilio()
        self.http = httpx.AsyncClient(transport=httpx.MockTransport(self.receiver))

        async def open_listener():
            return FakeListener()

        telephony = Telephony(
            twilio=self.twilio, open_listener=open_listener, speaker=FakeSpeaker(), public_api_url=PUBLIC,
            auth_token=TOKEN, barge_in_min_words=2, inbound_enabled=False, inbound_profile="bank",
        ) if configured else None
        settings = Settings(
            database_url="sqlite://", api_key="test-key", auth_required=False, allow_private_callbacks=True,
            callback_backoff_seconds=0, public_api_url=PUBLIC, public_base_url="https://console.example",
        )
        self.app = create_app(ScriptedBrain(), settings, repo=self.repo, http=self.http, telephony=telephony)
        self.service = self.app.state.service
        self.engine = WorkflowEngine(self.service)
        self.dispatcher = JobDispatcher(self.service)

    async def close(self):
        await self.http.aclose()
        self.repo.engine.dispose()

    # --- data ---------------------------------------------------------------------------------------

    def seed(self, channel="phone", callback=False, window=None, **action) -> SimpleNamespace:
        """An organization with an active agent, an active workflow and one consenting contact."""
        config = {**ACTION, "channel": channel, **action}

        if callback:
            config["callback_url"] = "https://crm.example/hooks"

        with Session(self.repo.engine) as db:
            organization = Organization(name="Acme")
            db.add(organization)
            db.flush()
            agent = Agent(organization_id=organization.id, name="Assistant", status="active")
            db.add(agent)
            db.flush()
            workflow = Workflow(
                organization_id=organization.id, agent_id=agent.id, name="Reminder", status="active",
                trigger_type="date_offset", trigger_config=dict(TRIGGER), action_config=config,
            )
            db.add(workflow)
            db.commit()
            ids = SimpleNamespace(organization=organization.id, agent=agent.id, workflow=workflow.id)

        ids.contact = self.add_contact(ids, preferred_contact_time=window)
        return ids

    def add_contact(self, ids, name="Priya Sharma", **fields) -> int:
        with Session(self.repo.engine) as db:
            contact = Contact(**{
                "organization_id": ids.organization, "name": name, "phone": PHONE, "consent_status": "granted",
                "metadata_": {"appointment_date": "2026-09-25"}, **fields,
            })
            db.add(contact)
            db.commit()
            return contact.id

    def change(self, model, ident: int, **fields) -> None:
        with Session(self.repo.engine) as db:
            row = db.get(model, ident)
            for key, value in fields.items():
                setattr(row, key, value)
            db.commit()

    async def scheduled(self, ids=None, contact=None, when=NOW, **seed) -> dict:
        """Run the real workflow engine for a contact and return the scheduled job it created."""
        ids = ids or self.seed(**seed)
        self.clock.to(when)
        outcome = await self.engine.run_for_contact(ids.workflow, contact or ids.contact, when)
        assert outcome.reason == Reason.CALL_JOB_CREATED, outcome
        job = self.repo.get_job(outcome.job_id)
        assert job["status"] == SCHEDULED
        self._ids[job["id"]] = ids
        return job

    def ids(self, job: dict) -> SimpleNamespace:
        """The organization, agent, workflow and contact a scheduled job was seeded with."""
        return self._ids[job["id"]]

    async def dispatch(self, when=NOW, limit=50):
        self.clock.to(when)
        return await self.dispatcher.dispatch_scheduled_jobs(when, limit=limit)

    def job(self, job_id: str) -> dict:
        return self.repo.get_job(job_id)


@pytest.fixture
async def world(tmp_path, clock):
    w = World(tmp_path, clock)
    yield w
    await w.close()


@pytest.fixture
def make_world(tmp_path, clock):
    made = []

    def build(configured=True):
        path = tmp_path / f"w{len(made)}"
        path.mkdir()
        made.append(World(path, clock, configured))
        return made[-1]

    return build


# === one job, one dispatch ======================================================================


async def test_a_scheduled_phone_job_is_claimed_and_dialed_once(world):
    job = await world.scheduled()
    report = await world.dispatch()

    assert (report.selected, report.dispatched, report.skipped, report.cancelled, report.failed) == (1, 1, 0, 0, 0)
    (outcome,) = report.outcomes
    assert (outcome.job_id, outcome.status, outcome.reason) == (job["id"], D.DISPATCHED, Reason.DISPATCHED)
    assert outcome.twilio_call_sid == "CA_fake_1" and (outcome.workflow_id, outcome.contact_id) == (job["workflow_id"], job["contact_id"])

    (call,) = world.twilio.calls
    assert call["to"] == PHONE and call["status_callback"] == f"{PUBLIC}/api/telephony/status?job={job['id']}"
    row = world.job(job["id"])
    assert (row["status"], row["twilio_call_sid"]) == ("ringing", "CA_fake_1")

    again = await world.dispatch(NOW + timedelta(minutes=5))
    assert again.selected == 0 and len(world.twilio.calls) == 1, "a job that is ringing is not dispatched again"


async def test_the_claim_gives_a_fresh_expiry_and_token_and_changes_nothing_else(world):
    job = await world.scheduled()
    later = NOW + timedelta(minutes=30)  # the job has been waiting: its stored expiry is long stale
    await world.dispatch(later)
    row = world.job(job["id"])

    assert row["expires_at"] == pytest.approx(later.timestamp() + 120), "the ring time starts at the claim"
    assert row["expires_at"] > job["expires_at"]
    assert row["answer_token"] != job["answer_token"] and len(row["answer_token"]) >= 16
    assert {key for key in row if row[key] != job[key]} == {"status", "expires_at", "answer_token", "twilio_call_sid"}
    assert (row["organization_id"], row["agent_id"], row["contact_id"], row["workflow_id"]) == (
        job["organization_id"], job["agent_id"], job["contact_id"], job["workflow_id"],
    ), "every domain link survives the claim"
    assert world.twilio.calls[0]["ring_seconds"] == 120, "and the call rings for the whole timeout, not what was left of a stale one"


async def test_the_ring_timeout_is_the_one_the_job_was_created_with(world):
    job = await world.scheduled(ring_timeout_seconds=45)
    await world.dispatch(NOW + timedelta(minutes=10))

    assert world.job(job["id"])["expires_at"] == pytest.approx((NOW + timedelta(minutes=10)).timestamp() + 45)
    assert world.twilio.calls[0]["ring_seconds"] == 45


async def test_the_claim_goes_through_the_existing_dial_path(world):
    job = await world.scheduled()
    seen = []
    original = world.service._dial

    async def spy(row):
        seen.append((row["id"], row["status"], row["answer_token"], row["twilio_call_sid"]))
        return await original(row)

    world.service._dial = spy
    await world.dispatch()

    ((job_id, status, token, sid),) = seen
    assert (job_id, status, sid) == (job["id"], "ringing", None), "dialed as a claimed ringing job with no call yet"
    assert token == world.job(job_id)["answer_token"] != job["answer_token"]

    stream = ET.fromstring(world.twilio.calls[0]["twiml"]).find("Connect/Stream")
    params = {p.get("name"): p.get("value") for p in stream.findall("Parameter")}
    assert verify_stream_token(TOKEN, "job", job_id, params["token"]), "the stream token is the one _dial always issues"


async def test_a_claimed_job_is_an_ordinary_ringing_job_to_the_sweeper(world):
    job = await world.scheduled()
    await world.dispatch()

    world.clock.to(NOW + timedelta(seconds=119))
    await world.service.sweep()
    assert world.job(job["id"])["status"] == "ringing", "not expired yet: the ring time counts from the claim"

    world.clock.to(NOW + timedelta(seconds=121))
    await world.service.sweep()
    row = world.job(job["id"])
    assert (row["status"], row["end_reason"]) == ("no_answer", "no_answer")
    assert world.twilio.hangups == ["CA_fake_1"], "and the ringing phone is stopped, as for any ring timeout"


# === concurrency ================================================================================


@pytest.mark.parametrize("workers", [2, 4])
async def test_dispatchers_racing_for_one_job_dial_it_exactly_once(world, monkeypatch, workers):
    """A barrier holds every dispatcher after it has loaded the job and before any can claim it, so all
    of them really do see it scheduled. The database conditional update lets one through."""
    job = await world.scheduled()
    barrier, real = threading.Barrier(workers, timeout=15), world.repo.get_contact

    def held(contact_id):
        contact = real(contact_id)
        barrier.wait()
        return contact

    monkeypatch.setattr(world.repo, "get_contact", held)
    reports = await asyncio.gather(*(world.dispatch() for _ in range(workers)))

    outcomes = [o for r in reports for o in r.outcomes]
    assert sorted(o.status for o in outcomes) == sorted([D.DISPATCHED] + [D.SKIPPED] * (workers - 1))
    assert {o.reason for o in outcomes if o.status == D.SKIPPED} == {Reason.ALREADY_CLAIMED}
    assert len(world.twilio.calls) == 1, "the losers did not dial"
    assert world.job(job["id"])["twilio_call_sid"] == "CA_fake_1"


async def test_a_run_that_decided_to_dial_loses_to_a_run_that_closed_the_job_meanwhile(world, monkeypatch):
    """Run A loads the contact (consenting) and is held there. The contact then opts out and run B
    closes the job. When A is released it still wants to dial, but its claim finds the job is no
    longer scheduled: it must not dial."""
    job = await world.scheduled()
    loaded, release, real, first = threading.Event(), threading.Event(), world.repo.get_contact, []

    def held_once(contact_id):
        contact = real(contact_id)

        if not first:  # only run A
            first.append(True)
            loaded.set()
            assert release.wait(15)

        return contact

    monkeypatch.setattr(world.repo, "get_contact", held_once)
    run_a = asyncio.create_task(world.dispatch())
    assert await asyncio.to_thread(loaded.wait, 15)

    world.change(Contact, world.ids(job).contact, consent_status="revoked")
    closed = await world.dispatch()
    assert [(o.status, o.reason) for o in closed.outcomes] == [(D.CANCELLED, Reason.OPTED_OUT)]

    release.set()
    lost = await run_a

    assert [(o.status, o.reason) for o in lost.outcomes] == [(D.SKIPPED, Reason.ALREADY_CLAIMED)]
    assert world.twilio.calls == [], "the job was closed first, so it was never dialed"
    assert world.job(job["id"])["end_reason"] == "opted_out"


async def test_closing_a_job_loses_to_a_claim_that_got_there_first(world):
    job = await world.scheduled()
    assert world.repo.transition(job["id"], [SCHEDULED], "ringing", expires_at=NOW.timestamp() + 90)  # a rival claim
    won = await world.service._finish_without_call(job["id"], "failed", reason="opted_out", cancel_line=False, from_statuses=[SCHEDULED])

    assert won is False and world.job(job["id"])["status"] == "ringing", "a job that is ringing cannot be closed as unplaced"


# === what is selected ============================================================================


@pytest.mark.parametrize("status", ["ringing", "in_progress", "completed", "wrong_party", "declined", "no_answer", "failed"])
async def test_only_scheduled_jobs_are_selected(world, status):
    world.repo.create_job(values(f"JOB-{status}", status=status, channel="phone"))
    before = world.job(f"JOB-{status}")
    report = await world.dispatch()

    assert report.selected == 0 and world.twilio.calls == []
    assert world.job(f"JOB-{status}") == before


async def test_a_scheduled_web_job_is_not_selected(world):
    job = await world.scheduled(channel="web")
    report = await world.dispatch()

    assert report.selected == 0 and world.twilio.calls == []
    assert world.job(job["id"])["status"] == "scheduled"


async def test_the_dispatcher_leaves_manual_phone_calls_exactly_as_they_are(world):
    manual, created = await world.service.create_job(CallJobRequest(
        reference="manual-1", profile_id="bank", channel="phone", callee=Callee(name="P", phone=PHONE), reason="x",
    ))
    assert created and manual["status"] == "ringing" and len(world.twilio.calls) == 1, "dispatch=True still dials at creation"
    before = world.job(manual["job_id"])

    await world.scheduled()
    report = await world.dispatch()

    assert report.selected == 1 and len(world.twilio.calls) == 2, "only the scheduled job was placed"
    assert world.job(manual["job_id"]) == before, "the manual call was not touched"


async def test_selection_is_bounded_and_oldest_first(world):
    ids = world.seed()
    jobs = []
    for i in range(5):
        contact = ids.contact if i == 0 else world.add_contact(ids, name=f"Person {i}")
        jobs.append(await world.scheduled(ids, contact, when=NOW + timedelta(seconds=i)))

    report = await world.dispatch(NOW + timedelta(minutes=1), limit=2)

    assert report.selected == 2 and [o.job_id for o in report.outcomes] == [jobs[0]["id"], jobs[1]["id"]]
    assert [world.job(j["id"])["status"] for j in jobs] == ["ringing", "ringing", "scheduled", "scheduled", "scheduled"]

    rest = await world.dispatch(NOW + timedelta(minutes=1), limit=10)
    assert [o.job_id for o in rest.outcomes] == [j["id"] for j in jobs[2:]]


async def test_jobs_with_the_same_creation_time_are_taken_in_id_order(world):
    ids = world.seed()
    jobs = [await world.scheduled(ids, world.add_contact(ids, name=f"P{i}") if i else ids.contact) for i in range(4)]

    report = await world.dispatch(limit=10)

    assert [o.job_id for o in report.outcomes] == sorted(j["id"] for j in jobs)


async def test_the_selection_query_is_limited_and_uses_the_status_index(world):
    await world.scheduled()
    captured = []
    event.listen(world.repo.engine, "before_cursor_execute", lambda conn, cur, stmt, params, *a: captured.append((stmt, params)))
    world.repo.list_scheduled_phone_jobs(7)

    ((statement, params),) = [c for c in captured if "FROM call_jobs" in c[0] and "WHERE" in c[0]]
    assert "LIMIT" in statement and "ORDER BY" in statement and "status" in statement and "channel" in statement
    assert 7 in params, "the limit is in the query, not applied after loading the table"

    with world.repo.engine.connect() as connection:
        plan = " ".join(str(row) for row in connection.exec_driver_sql("EXPLAIN QUERY PLAN " + statement, params).fetchall())
    assert "ix_call_jobs_status" in plan, "the existing index serves it: no migration needed"


async def test_limit_and_now_are_validated(world):
    job = await world.scheduled()

    with pytest.raises(ValueError, match="limit"):
        await world.dispatcher.dispatch_scheduled_jobs(NOW, limit=0)

    with pytest.raises(ValueError, match="timezone-aware"):
        await world.dispatcher.dispatch_scheduled_jobs(NOW.replace(tzinfo=None))

    assert world.job(job["id"])["status"] == "scheduled"


# === deciding again, at dispatch time ==============================================================


async def test_a_contact_outside_their_window_leaves_the_job_scheduled_until_it_opens(world):
    job = await world.scheduled(window={"start": "09:00", "end": "18:00", "timezone": "Asia/Kolkata"})
    world.change(Contact, world.ids(job).contact, preferred_contact_time={"start": "14:00", "end": "18:00", "timezone": "Asia/Kolkata"})

    early = await world.dispatch(at(24, 10, 5))
    assert (early.selected, early.skipped, early.cancelled, early.dispatched) == (1, 1, 0, 0)
    assert (early.outcomes[0].status, early.outcomes[0].reason) == (D.SKIPPED, Reason.OUTSIDE_CONTACT_WINDOW)
    assert world.job(job["id"]) == job, "not a single field changed"
    assert world.twilio.calls == []

    assert (await world.dispatch(at(24, 14, 0))).dispatched == 1, "dialed when the window opens"
    assert world.job(job["id"])["status"] == "ringing"


async def test_a_window_that_never_opens_that_day_closes_the_job_the_next_day(world):
    job = await world.scheduled(window={"start": "09:00", "end": "18:00", "timezone": "Asia/Kolkata"})
    world.change(Contact, world.ids(job).contact, preferred_contact_time={"start": "19:00", "end": "20:00", "timezone": "Asia/Kolkata"})

    assert (await world.dispatch(at(24, 18, 30))).skipped == 1
    report = await world.dispatch(at(25, 0, 0))

    assert (report.cancelled, report.outcomes[0].reason) == (1, Reason.WINDOW_PASSED)
    assert world.job(job["id"])["end_reason"] == "window_passed" and world.twilio.calls == []


@pytest.mark.parametrize("change, reason", [
    ((Contact, "contact", {"consent_status": "revoked"}), Reason.OPTED_OUT),
    ((Contact, "contact", {"consent_status": "unknown"}), Reason.CONSENT_UNKNOWN),
    ((Contact, "contact", {"status": "inactive"}), Reason.CONTACT_INACTIVE),
    ((Contact, "contact", {"phone": None}), Reason.PHONE_MISSING),
    ((Contact, "contact", {"metadata_": {}}), Reason.REFERENCE_DATE_MISSING),
    ((Workflow, "workflow", {"status": "paused"}), Reason.WORKFLOW_INACTIVE),
    ((Workflow, "workflow", {"status": "archived"}), Reason.WORKFLOW_INACTIVE),
    ((Agent, "agent", {"status": "archived"}), Reason.AGENT_INACTIVE),
    ((Agent, "agent", {"status": "draft"}), Reason.AGENT_INACTIVE),
    ((Organization, "organization", {"status": "suspended"}), Reason.ORGANIZATION_INACTIVE),
])
async def test_a_job_that_should_no_longer_be_called_is_closed_and_never_dialed(world, change, reason):
    model, attr, fields = change
    job = await world.scheduled()
    world.change(model, getattr(world.ids(job), attr), **fields)

    report = await world.dispatch(NOW + timedelta(minutes=5))

    assert (report.selected, report.cancelled, report.dispatched, report.failed) == (1, 1, 0, 0)
    assert (report.outcomes[0].status, report.outcomes[0].reason) == (D.CANCELLED, reason)
    row = world.job(job["id"])
    assert (row["status"], row["end_reason"]) == ("failed", reason.value), "closed with the existing final status; end_reason says why"
    assert row["finished_at"] == pytest.approx((NOW + timedelta(minutes=5)).timestamp())
    assert (row["twilio_call_sid"], row["answered_at"], row["call_id"]) == (None, None, None), "it never became a call"
    assert (row["organization_id"], row["contact_id"], row["workflow_id"]) == (job["organization_id"], job["contact_id"], job["workflow_id"])
    assert world.twilio.calls == [] and world.twilio.hangups == []


async def test_a_job_that_stopped_being_due_is_closed(world):
    job = await world.scheduled()
    report = await world.dispatch(at(25, 0, 30))  # after the day the job was for

    assert report.outcomes[0].reason == Reason.WINDOW_PASSED and world.job(job["id"])["status"] == "failed"

    moved = await world.scheduled(world.seed(), when=NOW)
    world.change(Contact, world.ids(moved).contact, metadata_={"appointment_date": "2026-10-30"})
    assert (await world.dispatch(NOW)).outcomes[0].reason == Reason.NOT_YET_DUE, "the date it was scheduled for is gone"


def test_every_reason_used_to_close_a_job_fits_the_end_reason_column():
    """end_reason is VARCHAR(24): SQLite would accept a longer one and PostgreSQL would refuse it."""
    assert max(len(reason.value) for reason in Reason) <= 24


# === is it still the same execution? ==================================================================


async def test_a_job_whose_execution_has_been_replaced_is_not_dialed_and_the_new_one_is(world):
    """Scheduled for the 24th. The appointment then moves, so on the 25th the workflow is due for a
    different execution. The old job must not ring; the new execution gets its own job, which does."""
    old = await world.scheduled()
    world.change(Contact, world.ids(old).contact, metadata_={"appointment_date": "2026-09-26"})

    stale = await world.dispatch(at(25, 10))
    assert (stale.outcomes[0].status, stale.outcomes[0].reason) == (D.CANCELLED, Reason.EXECUTION_MISMATCH)
    assert "2026-09-25" in stale.outcomes[0].detail and world.twilio.calls == []
    assert world.job(old["id"])["end_reason"] == "execution_mismatch"

    world.clock.to(at(25, 10))
    fresh = await world.engine.run_for_contact(world.ids(old).workflow, world.ids(old).contact, at(25, 10))
    assert fresh.call_job_created and fresh.job_id != old["id"]

    report = await world.dispatch(at(25, 10, 1))
    assert [(o.job_id, o.status) for o in report.outcomes] == [(fresh.job_id, D.DISPATCHED)]
    assert len(world.twilio.calls) == 1


async def test_a_scheduled_job_whose_reference_is_not_the_workflows_own_is_not_dialed(world):
    ids = world.seed()
    view, _ = await world.service.create_job(CallJobRequest(
        reference="somebody-elses-reference", profile_id="bank", channel="phone", callee=Callee(name="P", phone=PHONE),
        reason="x", organization_id=ids.organization, agent_id=ids.agent, contact_id=ids.contact, workflow_id=ids.workflow,
    ), dispatch=False)
    report = await world.dispatch()

    assert (report.cancelled, report.outcomes[0].reason) == (1, Reason.EXECUTION_MISMATCH)
    assert world.job(view["job_id"])["status"] == "failed" and world.twilio.calls == []


@pytest.mark.parametrize("missing", ["organization", "agent", "contact", "workflow"])
async def test_a_scheduled_job_without_its_execution_identity_is_closed(world, missing):
    ids = world.seed()
    links = {"organization_id": ids.organization, "agent_id": ids.agent, "contact_id": ids.contact, "workflow_id": ids.workflow}
    if missing == "organization":
        links = {}  # the CHECK forbids other links without an organization
    else:
        links.pop(f"{missing}_id")
    view, _ = await world.service.create_job(CallJobRequest(
        reference="wf:x", profile_id="bank", channel="phone", callee=Callee(name="P", phone=PHONE), reason="x", **links,
    ), dispatch=False)

    report = await world.dispatch()

    assert (report.cancelled, report.outcomes[0].reason) == (1, Reason.EXECUTION_INVALID)
    assert world.job(view["job_id"])["end_reason"] == "execution_invalid" and world.twilio.calls == []


@pytest.mark.parametrize("record", ["agent", "contact", "workflow"])
async def test_records_that_disagree_about_the_tenant_are_never_dialed(world, monkeypatch, record):
    """The composite foreign keys make this impossible on disk; the dispatcher still refuses it."""
    job = await world.scheduled()
    real = getattr(world.repo, f"get_{record}")

    def other_tenant(ident):
        row = real(ident)
        row.organization_id = row.organization_id + 1000
        return row

    monkeypatch.setattr(world.repo, f"get_{record}", other_tenant)
    report = await world.dispatch()

    assert (report.cancelled, report.outcomes[0].reason) == (1, Reason.TENANT_MISMATCH)
    assert world.job(job["id"])["end_reason"] == "tenant_mismatch" and world.twilio.calls == []


@pytest.mark.parametrize("record", ["organization", "agent", "contact", "workflow"])
async def test_a_record_that_cannot_be_found_closes_the_job(world, monkeypatch, record):
    job = await world.scheduled()
    monkeypatch.setattr(world.repo, f"get_{record}", lambda ident: None)
    report = await world.dispatch()

    assert (report.cancelled, report.outcomes[0].reason) == (1, Reason.EXECUTION_INVALID) and world.twilio.calls == []
    assert world.job(job["id"])["status"] == "failed"


# === closing a job: the callback ====================================================================


async def test_closing_a_job_sends_the_same_finished_callback_as_any_job_that_ends_without_a_call(world):
    job = await world.scheduled(callback=True)
    world.change(Contact, world.ids(job).contact, consent_status="revoked")
    await world.dispatch()
    await world.service.drain()

    (call,) = world.receiver.calls
    body = json.loads(call.content)
    assert body["event"] == "call_job.finished"
    assert (body["job"]["status"], body["job"]["end_reason"], body["job"]["job_id"]) == ("failed", "opted_out", job["id"])
    assert body["job"]["result"]["outcome"] == "failed" and body["job"]["result"]["transcript"] == []
    assert verify_signature("test-key", call.content, call.headers["X-Signature"])
    assert world.job(job["id"])["callback_status"] == "delivered"


async def test_closing_a_job_that_has_no_callback_url_sends_nothing(world):
    job = await world.scheduled()
    world.change(Contact, world.ids(job).contact, consent_status="revoked")
    await world.dispatch()
    await world.service.drain()

    assert world.receiver.calls == [] and world.job(job["id"])["callback_status"] == "none"


async def test_leaving_a_job_scheduled_sends_no_callback(world):
    job = await world.scheduled(callback=True, window={"start": "09:00", "end": "18:00", "timezone": "Asia/Kolkata"})
    world.change(Contact, world.ids(job).contact, preferred_contact_time={"start": "14:00", "end": "18:00", "timezone": "Asia/Kolkata"})
    await world.dispatch(at(24, 10, 5))
    await world.service.drain()

    assert world.receiver.calls == [] and world.job(job["id"])["callback_status"] == "none"


async def test_the_default_finish_still_only_ends_ringing_jobs(world):
    job = await world.scheduled()

    assert await world.service._finish_without_call(job["id"], "no_answer") is False, "existing callers cannot close a scheduled job"
    assert world.job(job["id"])["status"] == "scheduled"


# === failure ====================================================================================


async def test_a_call_twilio_refuses_ends_the_job_the_way_it_always_did(world):
    job = await world.scheduled(callback=True)
    world.twilio.fail = True
    report = await world.dispatch()
    await world.service.drain()

    assert (report.failed, report.dispatched) == (1, 0)
    assert (report.outcomes[0].status, report.outcomes[0].reason) == (D.FAILED, Reason.TELEPHONY_ERROR)
    row = world.job(job["id"])
    assert (row["status"], row["end_reason"], row["twilio_call_sid"]) == ("failed", "telephony_error", None)
    assert len(world.receiver.calls) == 1, "and the business is told, as for any failed call"


async def test_an_unexpected_error_while_dialing_leaves_a_ringing_job_that_the_sweeper_ends(world, monkeypatch):
    first = await world.scheduled()
    ids = world.ids(first)
    second = await world.scheduled(ids, world.add_contact(ids, name="Second Person"), when=NOW + timedelta(seconds=1))
    calls = []
    real = world.twilio.create_call

    async def flaky(to, twiml, status_callback, ring_seconds):
        calls.append(to)
        if len(calls) == 1:
            raise RuntimeError("something nobody planned for")

        return await real(to, twiml, status_callback, ring_seconds)

    monkeypatch.setattr(world.twilio, "create_call", flaky)
    report = await world.dispatch()

    assert (report.selected, report.failed, report.dispatched) == (2, 1, 1), "one bad job does not stop the run"
    assert (report.outcomes[0].status, report.outcomes[0].reason) == (D.FAILED, Reason.DISPATCH_ERROR)
    assert "RuntimeError" in report.outcomes[0].detail and report.outcomes[1].status == D.DISPATCHED
    assert world.job(first["id"])["status"] == "ringing", "we cannot know whether Twilio placed it, so it is left to time out"

    world.clock.to(NOW + timedelta(seconds=130))
    await world.service.sweep()
    assert world.job(first["id"])["status"] == "no_answer", "it cannot stay in limbo"
    assert world.job(second["id"])["status"] == "no_answer" and len(world.twilio.calls) == 1


async def test_a_crash_between_claim_and_dial_misses_the_call_but_can_never_place_two(world):
    class Crash(BaseException):
        """The process dying: not an Exception, so nothing in the dispatcher can catch it."""

    job = await world.scheduled()
    real_dial = world.service._dial

    async def die(row):
        raise Crash()

    world.service._dial = die

    with pytest.raises(Crash):
        await world.dispatch()

    row = world.job(job["id"])
    assert (row["status"], row["twilio_call_sid"]) == ("ringing", None) and world.twilio.calls == []
    assert row["expires_at"] == pytest.approx(NOW.timestamp() + 120)

    world.service._dial = real_dial  # the restarted process
    assert (await world.dispatch(NOW + timedelta(seconds=30))).selected == 0, "nobody dials a job that is already claimed"
    world.clock.to(NOW + timedelta(seconds=60))
    await world.service.sweep()
    assert world.job(job["id"])["status"] == "ringing", "the sweeper waits for the ring time"

    world.clock.to(NOW + timedelta(seconds=121))
    await world.service.sweep()
    assert world.job(job["id"])["status"] == "no_answer" and world.twilio.calls == [], "a missed call, never two"
    assert world.twilio.hangups == [], "and there was no line to hang up"


async def test_without_telephony_nothing_is_claimed_but_closing_still_works(make_world):
    w = make_world(configured=False)
    waiting = await w.scheduled()
    other = await w.scheduled(w.seed(), when=NOW)
    w.change(Contact, w.ids(other).contact, consent_status="revoked")

    report = await w.dispatch()

    outcomes = {o.job_id: o for o in report.outcomes}
    assert (outcomes[waiting["id"]].status, outcomes[waiting["id"]].reason) == (D.SKIPPED, Reason.TELEPHONY_UNAVAILABLE)
    assert outcomes[other["id"]].status == D.CANCELLED
    assert w.job(waiting["id"])["status"] == "scheduled", "left exactly as it was"
    await w.close()


# === the shape of the component =====================================================================


async def test_the_report_is_plain_data_a_scheduler_can_log(world):
    await world.scheduled()
    report = await world.dispatch()
    data = json.loads(json.dumps(report.as_dict()))

    assert data["selected"] == data["dispatched"] == 1 and data["skipped"] == data["cancelled"] == data["failed"] == 0
    assert data["outcomes"][0]["status"] == "dispatched" and data["outcomes"][0]["twilio_call_sid"] == "CA_fake_1"


def test_the_dispatcher_is_independent_of_telephony_http_and_any_scheduler():
    tree = ast.parse(Path(__file__).parents[1].joinpath("app/workflows/dispatcher.py").read_text())
    imported = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module)

    forbidden = ("telephony", "twilio", "deepgram", "httpx", "fastapi", "starlette")
    assert not [name for name in imported if any(word in name for word in forbidden)], imported
    assert not [n for n in ast.walk(tree) if isinstance(n, (ast.While, ast.AsyncFor))], "no polling loop"

    called = {n.func.attr if isinstance(n.func, ast.Attribute) else getattr(n.func, "id", "") for n in ast.walk(tree) if isinstance(n, ast.Call)}
    assert not called & {"sleep", "create_task", "ensure_future", "run_in_executor"}, "nothing is scheduled from in here"
