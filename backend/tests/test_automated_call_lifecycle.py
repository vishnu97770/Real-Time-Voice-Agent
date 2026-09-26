"""Step 7: an automated call, end to end, on the existing system.

    active workflow -> scheduler -> workflow engine -> scheduled job -> dispatcher -> ringing job
        -> existing telephony (fake phone network) -> answer -> call -> result -> final job state

Nothing new is being built here: these tests drive the components of steps 1-6 through the real HTTP and
WebSocket endpoints, with the phone network replaced by the fake Twilio and fake speech of the existing
telephony tests. No real call, credential, network or speech service is involved.

The scheduler ticks run inside the application's own event loop (through the test client's portal), as they
would in production, so callbacks and the shared HTTP client behave normally. The service's wall clock is
pinned to the same fake time as the business `now`."""

import asyncio
import json
import logging
import threading
import time
from contextlib import contextmanager
from datetime import timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.brains.base import TextDelta, ToolCall
from app.db import ResultRow
from app.models import Agent, Contact, Organization, Workflow
from app.outbound import SCHEDULED, Callee, CallJobRequest, verify_signature
from app.telephony.twilio import TwilioError
from app.workflows import JobDispatcher, Reason, SchedulerConfig, WorkflowEngine, WorkflowScheduler
from tests.helpers import ScriptedBrain
from tests.test_dispatcher import NOW, clock, world  # noqa: F401  (fixtures)
from tests.test_scheduler import scheduler_for, tick
from tests.test_telephony_routes import KEY, TOKEN, World, bank_outbound, hear, play_finished, speak, status_call
from tests.test_workflow_engine import ACTION, TRIGGER

async def routed(ctx):
    """One brain for both kinds of call, as the real one is: it answers from whatever the call gives it.
    A manual call has a profile (the bank's demo conversation). An automated call has a domain context: the
    brain reads it with the call's one tool and speaks about the contact's own appointment."""
    if ctx.domain is None:
        async for event in bank_outbound(ctx):
            yield event
    elif "confirmed who they are" in ctx.text:
        result = await ctx.run_tool("get_call_context", {})
        yield ToolCall("get_call_context", {}, result)
        details = result["contact"]["details"]
        yield TextDelta(
            f"I'm calling from {result['organization']['name']} about your {details['appointment_type']} "
            f"with {details['doctor']} on {details['appointment_date']}. Does that still suit you?"
        )
    else:
        yield TextDelta("Thank you. Goodbye.")


WINDOW_CLOSED = {"start": "14:00", "end": "18:00", "timezone": "Asia/Kolkata"}
CALLBACK = "https://crm.example/hooks"


class Lifecycle:
    """The phone-network world of the telephony tests, plus a scheduler running in its event loop."""

    def __init__(self, w: World, clock):
        self.w, self.clock, self.repo = w, clock, w.repo
        self.service = w.app.state.service
        self.brain = self.service.brain = ScriptedBrain(routed)
        self.engine = WorkflowEngine(self.service)
        self.scheduler = WorkflowScheduler(
            self.engine, JobDispatcher(self.service), SchedulerConfig(), clock=lambda: clock.now
        )

    @contextmanager
    def db(self):
        """A direct session for the test thread. The in-memory test database is ONE connection shared with the
        application's thread, so this holds the repository's own lock (as every Repository method does): without
        it the session can see a write the app thread is in the middle of, and closing it rolls that write back."""
        with self.repo._lock:
            with Session(self.repo.engine) as session:
                yield session

    def in_app(self, function, *args):
        """Run a coroutine function in the application's own event loop."""
        return self.w.client.portal.call(function, *args)

    def seed(self, name="Acme", callback=False, window=None, **contact) -> SimpleNamespace:
        """An organization with an active agent, an active workflow, and a contact who consented."""
        action = {**ACTION, "channel": "phone", **({"callback_url": CALLBACK} if callback else {})}

        with self.db() as db:
            organization = Organization(name=name)
            db.add(organization)
            db.flush()
            agent = Agent(
                organization_id=organization.id, name=f"{name} assistant", status="active", role="Patient support",
                purpose="Remind patients about their appointments", target_users=["Patients"],
                primary_tasks=["Remind about appointments"], behavior_config={"tone": ["Calm", "Friendly"]},
                instructions={"additional_instructions": "AGENT-GUIDANCE-TEXT: keep it brief"},
            )
            db.add(agent)
            db.flush()
            workflow = Workflow(
                organization_id=organization.id, agent_id=agent.id, name="Appointment Reminder", status="active",
                trigger_type="date_offset", trigger_config=dict(TRIGGER), action_config=action,
            )
            person = Contact(**{
                "organization_id": organization.id, "name": "Priya Sharma", "phone": "+919876543210",
                "consent_status": "granted",
                "metadata_": {"appointment_date": "2026-09-25", "doctor": "Dr. Sharma", "appointment_type": "Follow-up"},
                "preferred_contact_time": window, **contact,
            })
            db.add_all([workflow, person])
            db.commit()

            return SimpleNamespace(organization=organization.id, agent=agent.id, workflow=workflow.id, contact=person.id)

    def schedule_only(self, ids, when=NOW):
        """The workflow engine alone: the job is recorded as scheduled and nothing is dialed."""
        self.clock.to(when)
        outcome = self.in_app(self.engine.run_for_contact, ids.workflow, ids.contact, when)
        assert outcome.reason == Reason.CALL_JOB_CREATED, outcome
        return outcome.job_id

    def tick(self, when=NOW, scheduler=None):
        self.clock.to(when)
        return self.in_app((scheduler or self.scheduler).run_once, when)

    def change(self, model, ident, **fields):
        with self.db() as db:
            row = db.get(model, ident)
            for key, value in fields.items():
                setattr(row, key, value)
            db.commit()

    def results(self) -> list[ResultRow]:
        with self.db() as db:
            return list(db.scalars(select(ResultRow).order_by(ResultRow.started_at)))

    def links(self, job_id):
        row = self.repo.get_job(job_id)
        return row["organization_id"], row["agent_id"], row["contact_id"], row["workflow_id"]


@pytest.fixture
def life(clock):
    with World() as w:
        yield Lifecycle(w, clock)


def expected_links(ids):
    return ids.organization, ids.agent, ids.contact, ids.workflow


def converse(w: World, params, call_sid):
    """A MANUAL call (the bank profile): the callee confirms who they are, reports a fraud, confirms the freeze, hangs up."""
    ws = w.connect(params, call_sid)
    opener, mark = hear(ws)
    assert "Am I speaking with Priya Sharma?" in opener and "TechMart" not in opener
    play_finished(ws, mark)
    speak(ws, "yes speaking")
    reply, mark = hear(ws)
    assert "TechMart" in reply
    play_finished(ws, mark)
    speak(ws, "that was not me")
    _, mark = hear(ws)
    play_finished(ws, mark)
    speak(ws, "yes please")
    done, _ = hear(ws)
    assert "is now frozen" in done
    ws.send_text(json.dumps({"event": "stop", "streamSid": "MZ1"}))  # the callee hangs up
    ws.__exit__(None, None, None)


def converse_domain(w: World, params, call_sid):
    """An AUTOMATED call: the agent speaks from the contact's own record, and the organization's name."""
    ws = w.connect(params, call_sid)
    opener, mark = hear(ws)
    assert "Am I speaking with Priya Sharma?" in opener and "calling from Acme" in opener and "Northbridge" not in opener
    play_finished(ws, mark)
    speak(ws, "yes speaking")
    reply, mark = hear(ws)
    assert "Follow-up with Dr. Sharma on 2026-09-25" in reply, reply
    assert "TechMart" not in reply and "Northbridge" not in reply, "no demo data"
    play_finished(ws, mark)
    speak(ws, "yes that works")
    goodbye, _ = hear(ws)
    assert "Goodbye" in goodbye
    ws.send_text(json.dumps({"event": "stop", "streamSid": "MZ1"}))
    ws.__exit__(None, None, None)


# === the whole path ================================================================================


def test_an_automated_call_goes_from_a_scheduler_tick_to_a_persisted_result_with_its_links_intact(life):
    w, ids = life.w, life.seed(callback=True)
    expected, seen = expected_links(ids), {}

    # scheduled: the engine records the job and nothing rings
    job_id = life.schedule_only(ids)
    seen["scheduled"] = life.links(job_id)
    assert life.repo.get_job(job_id)["status"] == SCHEDULED and w.twilio.calls == [] and life.results() == []

    # ringing: the dispatcher claims it and the existing telephony path places the call
    report = life.tick()
    assert (report.jobs_created, report.dispatch_report.dispatched) == (0, 1), "already scheduled; now placed"
    row = life.repo.get_job(job_id)
    seen["ringing"] = life.links(job_id)
    assert (row["status"], row["twilio_call_sid"]) == ("ringing", "CA_fake_1") and len(w.twilio.calls) == 1

    # in_progress: the callee answers on the audio stream, and the agent speaks from the contact's own record
    ws = w.connect(w.params(), "CA_fake_1")
    opener, mark = hear(ws)
    assert "Priya Sharma" in opener and "calling from Acme" in opener
    row = life.repo.get_job(job_id)
    seen["in_progress"] = life.links(job_id)
    assert row["status"] == "in_progress" and row["call_id"] and row["answered_at"] is not None
    play_finished(ws, mark)
    speak(ws, "yes speaking")
    reply, mark = hear(ws)
    assert "Follow-up with Dr. Sharma on 2026-09-25" in reply, "the contact's metadata reached the conversation"
    play_finished(ws, mark)
    speak(ws, "yes that works")
    hear(ws)
    ws.send_text(json.dumps({"event": "stop", "streamSid": "MZ1"}))
    ws.__exit__(None, None, None)

    # finished: the result is persisted and the job is finalized
    w.wait_until(lambda: life.repo.get_job(job_id)["callback_status"] == "delivered")
    final = life.repo.get_job(job_id)
    seen["completed"] = life.links(job_id)
    assert (final["status"], final["end_reason"]) == ("completed", "hangup")

    assert seen == {stage: expected for stage in ("scheduled", "ringing", "in_progress", "completed")}, "the links never changed"

    (result,) = life.results()
    assert (result.job_id, result.call_id, result.organization_id) == (job_id, final["call_id"], ids.organization)
    assert result.outcome == "completed" and result.payload["outbound"]["job_id"] == job_id
    assert len(life.repo.list_jobs(10)) == 1, "exactly one job"

    (brain_context,) = [c for c in life.brain.calls if c.domain is not None][:1]
    assert (brain_context.domain.agent.id, brain_context.domain.contact.id, brain_context.domain.workflow.id) == (ids.agent, ids.contact, ids.workflow)
    assert "get_call_context" in {e["tool"] for e in result.payload["audit"] if e["type"] == "tool_call"}

    view = w.job(job_id)
    assert view["result"]["outcome"] == "completed" and view["result"]["outbound"]["identity"] == "confirmed"
    assert (view["organization_id"], view["agent_id"], view["contact_id"], view["workflow_id"]) == expected


def test_a_workflow_calls_result_is_found_from_the_job_and_the_job_from_the_result(life):
    ids = life.seed()
    job_id = life.schedule_only(ids)
    life.tick()
    converse_domain(life.w, life.w.params(), "CA_fake_1")
    life.w.wait_until(lambda: life.repo.get_job(job_id)["status"] == "completed")

    (result,) = life.results()
    job = life.repo.get_job(job_id)

    assert result.job_id == job["id"] and result.call_id == job["call_id"], "ResultRow.job_id is CallJob.id"
    assert life.w.client.get(f"/api/calls/{job['call_id']}/result", headers=KEY).json()["outbound"]["job_id"] == job_id
    history = life.w.client.get("/api/calls", headers=KEY).json()
    assert [(row["call_id"], row["job_id"]) for row in history] == [(job["call_id"], job_id)]


def test_a_workflows_call_behaves_exactly_like_a_manual_call_once_it_is_ringing(life):
    """Same phone network, same script. The only difference is how the job came to be ringing."""
    w = life.w
    manual = w.place().json()
    life.schedule_only(life.seed())
    life.tick()
    (auto,) = [j for j in life.repo.list_jobs(10) if j["id"] != manual["job_id"]]
    assert len(w.twilio.calls) == 2

    ringing = {j: life.repo.get_job(j) for j in (manual["job_id"], auto["id"])}
    differ = {k for k in ringing[manual["job_id"]] if ringing[manual["job_id"]][k] != ringing[auto["id"]][k]}
    assert differ <= {
        "id", "reference", "reason", "answer_token", "twilio_call_sid", "callback_url",
        "organization_id", "agent_id", "contact_id", "workflow_id",
    }, "a ringing workflow job differs from a manual one only in identity, its own wording, tokens and links"
    same = ("status", "channel", "profile_id", "callee_name", "callee_phone", "customer_ref", "max_duration_seconds",
            "callback_status", "callback_attempts", "answered_at", "finished_at", "call_id", "end_reason")
    assert all(ringing[manual["job_id"]][k] == ringing[auto["id"]][k] for k in same), "everything that defines the call is identical"
    assert ringing[manual["job_id"]]["answer_token"] != ringing[auto["id"]]["answer_token"]

    for job_id in (manual["job_id"], auto["id"]):
        assert w.client.post(f"/api/call-jobs/{job_id}/answer", json={"token": ringing[job_id]["answer_token"]}).status_code == 404

    converse(w, w.params(0), "CA_fake_1")  # the manual call: the profile's conversation
    w.wait_until(lambda: life.repo.get_job(manual["job_id"])["status"] == "completed")
    converse_domain(w, w.params(1), "CA_fake_2")  # the automated call: the domain's
    w.wait_until(lambda: life.repo.get_job(auto["id"])["status"] == "completed")

    def shape(job_id):
        """Everything the pipeline decides that does not depend on what was said."""
        view = w.job(job_id)
        result = view["result"]
        return (
            view["status"], view["end_reason"], result["channel"], result["direction"], result["outbound"]["identity"],
            result["disclosure_given"], result["transcript"][0]["speaker"], sorted(result), sorted(view),
        )

    assert shape(manual["job_id"]) == shape(auto["id"]), "one pipeline: the same result shape and the same call lifecycle"
    assert w.job(manual["job_id"])["result"]["outcome"] == "action_completed", "the manual call froze a card (the profile's demo)"
    assert w.job(auto["id"])["result"]["outcome"] == "completed", "the automated call had nothing to change"
    by_kind = [c.domain is not None for c in life.brain.calls]
    assert by_kind == [False, False, True, True], "the manual call's turns carried no domain context; every automated turn did"
    manual_result, auto_result = life.results()
    assert (manual_result.job_id, auto_result.job_id) == (manual["job_id"], auto["id"])
    assert (manual_result.organization_id, auto_result.organization_id) == (None, life.repo.get_job(auto["id"])["organization_id"])


def test_a_manual_call_created_beside_a_running_scheduler_is_left_alone(life):
    manual = life.w.place().json()
    before = life.repo.get_job(manual["job_id"])
    life.schedule_only(life.seed())
    life.tick()
    life.tick(NOW + timedelta(minutes=1))

    assert life.repo.get_job(manual["job_id"]) == before and len(life.w.twilio.calls) == 2


# === running it again ===================================================================================


def test_repeated_scheduler_ticks_give_one_job_one_call_and_one_result(life):
    ids = life.seed()

    for when in (NOW, NOW, NOW + timedelta(minutes=1)):
        life.tick(when)

    (job,) = life.repo.list_jobs(10)
    assert len(life.w.twilio.calls) == 1 and job["status"] == "ringing"

    converse_domain(life.w, life.w.params(), "CA_fake_1")
    life.w.wait_until(lambda: life.repo.get_job(job["id"])["status"] == "completed")

    for when in (NOW + timedelta(minutes=2), NOW + timedelta(minutes=3)):  # after it has finished
        report = life.tick(when)
        assert report.jobs_created == 0 and report.dispatch_report.selected == 0
        assert [o.reason for o in report.workflow_results[0].outcomes] == [Reason.ALREADY_SCHEDULED]

    assert len(life.repo.list_jobs(10)) == 1 and len(life.w.twilio.calls) == 1 and len(life.results()) == 1
    assert life.links(job["id"]) == expected_links(ids)


async def test_two_schedulers_ticking_at_once_make_one_job_one_ringing_transition_and_one_call(world):
    """Real threads on a real database file. A barrier holds both schedulers just before they would
    create the job. There is no lock anywhere: the reference is unique and the claim is a conditional UPDATE."""
    world.seed()
    first, second = scheduler_for(world), scheduler_for(world)
    barrier, arrivals, lock = threading.Barrier(2, timeout=15), [], threading.Lock()
    real_lookup, real_transition, claims = world.repo.get_job_by_reference, world.repo.transition, []

    def held(reference):
        found = real_lookup(reference)

        with lock:
            arrivals.append(reference)
            hold = len(arrivals) <= 2

        if hold:
            barrier.wait()

        return found

    def counting(job_id, from_statuses, to_status, **fields):
        won = real_transition(job_id, from_statuses, to_status, **fields)

        if to_status == "ringing":
            claims.append(won)

        return won

    world.repo.get_job_by_reference, world.repo.transition = held, counting
    reports = await asyncio.gather(tick(first, world), tick(second, world))

    assert len(world.repo.list_jobs(10)) == 1
    assert claims.count(True) == 1, "exactly one scheduled -> ringing transition"
    assert len(world.twilio.calls) == 1
    assert sum(r.dispatch_report.dispatched for r in reports) == 1 and sum(r.jobs_created for r in reports) == 1


# === the world changes between scheduling and dispatch =====================================================


def test_a_contact_who_opts_out_after_scheduling_is_never_dialed_and_the_business_is_told(life):
    ids = life.seed(callback=True)
    job_id = life.schedule_only(ids)
    life.change(Contact, ids.contact, consent_status="revoked")

    report = life.tick(NOW + timedelta(minutes=5))

    assert (report.dispatch_report.cancelled, report.dispatch_report.dispatched) == (1, 0)
    row = life.repo.get_job(job_id)
    assert (row["status"], row["end_reason"], row["twilio_call_sid"], row["answered_at"]) == ("failed", "opted_out", None, None)
    assert life.w.twilio.calls == [] and life.w.twilio.hangups == []
    assert life.results() == [] and life.w.client.get("/api/calls", headers=KEY).json() == [], "no result for a call that never happened"
    assert life.links(job_id) == expected_links(ids)

    life.w.wait_until(lambda: life.w.receiver.calls)
    body = json.loads(life.w.receiver.calls[0].content)
    assert (body["event"], body["job"]["status"], body["job"]["end_reason"]) == ("call_job.finished", "failed", "opted_out")
    assert life.tick(NOW + timedelta(minutes=6)).dispatch_report.selected == 0, "and it is not picked up again"


def test_a_job_outside_the_contacts_window_waits_and_is_placed_when_the_window_opens(life):
    ids = life.seed(callback=True, window={"start": "09:00", "end": "18:00", "timezone": "Asia/Kolkata"})
    job_id = life.schedule_only(ids)
    life.change(Contact, ids.contact, preferred_contact_time=WINDOW_CLOSED)
    before = life.repo.get_job(job_id)

    for minute in (5, 6):
        report = life.tick(NOW + timedelta(minutes=minute))
        assert (report.dispatch_report.skipped, report.dispatch_report.dispatched) == (1, 0)

    assert life.repo.get_job(job_id) == before and life.w.twilio.calls == [] and life.results() == []
    assert life.w.receiver.calls == [], "waiting is not an ending: no callback"

    assert life.tick(NOW.replace(hour=14)).dispatch_report.dispatched == 1
    assert life.repo.get_job(job_id)["status"] == "ringing" and len(life.w.twilio.calls) == 1
    assert life.links(job_id) == expected_links(ids)


# === telephony goes wrong ========================================================================================


def test_when_twilio_refuses_the_call_the_job_ends_failed_once_and_is_never_redialed(life):
    ids = life.seed(callback=True)
    life.w.twilio.fail = True
    report = life.tick()

    assert (report.dispatch_report.failed, report.dispatch_report.dispatched) == (1, 0)
    (job,) = life.repo.list_jobs(10)
    assert (job["status"], job["end_reason"], job["twilio_call_sid"]) == ("failed", "telephony_error", None)
    assert life.results() == [] and life.links(job["id"]) == expected_links(ids)

    life.w.wait_until(lambda: life.w.receiver.calls)
    assert json.loads(life.w.receiver.calls[0].content)["job"]["end_reason"] == "telephony_error"

    life.w.twilio.fail = False  # Twilio recovers; nothing retries by itself
    for minute in (1, 2):
        again = life.tick(NOW + timedelta(minutes=minute))
        assert (again.jobs_created, again.dispatch_report.selected) == (0, 0)

    assert life.w.twilio.calls == [] and len(life.repo.list_jobs(10)) == 1 and len(life.w.receiver.calls) == 1


def test_a_process_that_dies_between_the_claim_and_the_dial_leaves_a_job_the_sweeper_ends_and_no_second_call(life):
    """The dispatcher's crash is simulated by doing what it does first and stopping: claim the job."""
    ids = life.seed(callback=True)
    job_id = life.schedule_only(ids)
    assert life.repo.transition(job_id, [SCHEDULED], "ringing", expires_at=NOW.timestamp() + 120, answer_token="claimed")

    for minute in (0, 1):
        report = life.tick(NOW + timedelta(minutes=minute))
        assert report.dispatch_report.selected == 0, "a claimed job is nobody's to dispatch again"

    assert life.w.twilio.calls == [] and life.repo.get_job(job_id)["status"] == "ringing"

    life.clock.to(NOW + timedelta(seconds=121))
    life.in_app(life.service.sweep)
    row = life.repo.get_job(job_id)

    assert (row["status"], row["end_reason"]) == ("no_answer", "no_answer")
    assert life.w.twilio.calls == [] and life.w.twilio.hangups == [], "no call was ever placed, so none is hung up"
    assert life.results() == [] and life.links(job_id) == expected_links(ids)
    life.w.wait_until(lambda: life.w.receiver.calls)
    assert life.tick(NOW + timedelta(minutes=5)).dispatch_report.selected == 0


def test_a_call_whose_agent_record_has_gone_is_hung_up_on_and_the_job_ends_failed_with_no_result(life, monkeypatch):
    """The callee picks up but there is nobody coherent to speak as: no session starts, the line is dropped, and the
    job ends through the ordinary path with its callback."""
    ids = life.seed(callback=True)
    life.tick()
    (job,) = life.repo.list_jobs(10)
    monkeypatch.setattr(life.repo, "get_agent", lambda ident: None)

    life.w.expect_dropped(life.w.params(), "CA_fake_1")

    row = life.repo.get_job(job["id"])
    assert (row["status"], row["end_reason"], row["answered_at"], row["call_id"]) == ("failed", "domain_context_invalid", None, None)
    assert life.w.twilio.hangups == ["CA_fake_1"] and life.results() == [] and list(life.service.store.all()) == []
    assert life.links(job["id"]) == expected_links(ids)
    life.w.wait_until(lambda: life.w.receiver.calls)
    assert json.loads(life.w.receiver.calls[0].content)["job"]["end_reason"] == "domain_context_invalid"


# === the other ways a call can end ==============================================================================


def test_a_wrong_number_ends_the_job_wrong_party_and_saves_the_result(life):
    ids = life.seed(callback=True)
    life.tick()
    (job,) = life.repo.list_jobs(10)
    ws = life.w.connect(life.w.params(), "CA_fake_1")
    _, mark = hear(ws)
    play_finished(ws, mark)
    speak(ws, "no, wrong number")
    goodbye, mark = hear(ws)
    assert "TechMart" not in goodbye
    play_finished(ws, mark)
    life.w.wait_until(lambda: life.w.twilio.hangups == ["CA_fake_1"])
    ws.__exit__(None, None, None)
    life.w.wait_until(lambda: life.repo.get_job(job["id"])["status"] == "wrong_party")

    (result,) = life.results()
    assert (result.job_id, result.outcome, result.organization_id) == (job["id"], "wrong_party", ids.organization)
    assert life.links(job["id"]) == expected_links(ids)
    life.w.wait_until(lambda: life.w.receiver.calls)


def test_when_nobody_picks_up_the_job_ends_no_answer_and_there_is_no_result(life):
    ids = life.seed(callback=True)
    life.tick()
    (job,) = life.repo.list_jobs(10)

    assert status_call(life.w, job["id"], {"CallSid": "CA_fake_1", "CallStatus": "no-answer"}).status_code == 204
    life.w.wait_until(lambda: life.repo.get_job(job["id"])["callback_status"] == "delivered")

    row = life.repo.get_job(job["id"])
    assert (row["status"], row["end_reason"]) == ("no_answer", "no_answer")
    assert life.results() == [] and life.links(job["id"]) == expected_links(ids)
    assert life.tick(NOW + timedelta(minutes=1)).dispatch_report.selected == 0 and len(life.w.twilio.calls) == 1


# === the business hears about it, and nothing sensitive comes with it ===============================================


def test_the_callback_for_an_automated_call_is_signed_and_carries_no_secret(life):
    ids = life.seed(callback=True)
    life.tick()
    (job,) = life.repo.list_jobs(10)
    token = job["answer_token"]
    converse_domain(life.w, life.w.params(), "CA_fake_1")
    life.w.wait_until(lambda: life.repo.get_job(job["id"])["callback_status"] == "delivered")

    (call,) = life.w.receiver.calls
    assert str(call.url) == CALLBACK and verify_signature("test-key", call.content, call.headers["X-Signature"])
    body = json.loads(call.content)
    assert body["event"] == "call_job.finished" and body["job"]["job_id"] == job["id"]
    assert (body["job"]["organization_id"], body["job"]["agent_id"], body["job"]["contact_id"], body["job"]["workflow_id"]) == expected_links(ids)
    assert (body["job"]["status"], body["job"]["result"]["outcome"]) == ("completed", "completed")
    assert body["job"]["callee"]["phone"] == "***3210"

    text = call.content.decode()
    assert [s for s in (token, "9876543210", "test-key", TOKEN, "answer_token") if s in text] == []


def test_the_automated_lifecycle_can_be_followed_in_the_logs_without_any_secret(life, caplog):
    caplog.set_level(logging.INFO)
    ids = life.seed(callback=True)
    life.tick()
    (job,) = life.repo.list_jobs(10)
    token = job["answer_token"]
    converse_domain(life.w, life.w.params(), "CA_fake_1")
    life.w.wait_until(lambda: life.repo.get_job(job["id"])["callback_status"] == "delivered")

    tag = f"job={job['id']} organization={ids.organization} workflow={ids.workflow} agent={ids.agent} contact={ids.contact}"
    reference = f"reference=wf:{ids.workflow}:contact:{ids.contact}:2026-09-24"
    text = caplog.text

    for line in (
        f"workflow {ids.workflow} created {job['id']} for contact {ids.contact} (2026-09-24)",  # the engine
        f"scheduled job placed {tag} {reference}",  # the dispatcher
        f"voice context built {tag} {reference}",  # the service, when it builds the call's context
        f"job answered {tag} {reference} channel=phone",  # the service, on the answer
        f"job finished {tag} {reference} status=completed end_reason=hangup",  # and on the finish
        "scheduler tick finished",
    ):
        assert line in text, line

    assert [s for s in (token, "9876543210", "919876543210", "test-key", TOKEN, "X-Signature", "Priya") if s in text] == []
    assert [s for s in ("AGENT-GUIDANCE", "Dr. Sharma", "Follow-up", "2026-09-25", "Patient support", "Calm") if s in text] == [], \
        "neither the agent's configuration nor the contact's record is ever logged"


def test_closing_a_job_without_a_call_is_logged_with_its_ids_and_reason(life, caplog):
    caplog.set_level(logging.INFO)
    ids = life.seed()
    job_id = life.schedule_only(ids)
    life.change(Contact, ids.contact, consent_status="revoked")
    life.tick(NOW + timedelta(minutes=5))

    assert f"job closed without a call job={job_id} organization={ids.organization} workflow={ids.workflow}" in caplog.text
    assert "status=failed reason=opted_out" in caplog.text and "9876543210" not in caplog.text


# === organizations stay apart ====================================================================================


def test_an_automated_call_can_never_cross_organizations(life):
    acme, shield = life.seed("Acme"), life.seed("Shield")
    life.tick()
    jobs = life.repo.list_jobs(10)

    assert len(jobs) == 2 and len(life.w.twilio.calls) == 2
    for job in jobs:
        owner = {acme.organization: acme, shield.organization: shield}[job["organization_id"]]
        assert (job["agent_id"], job["contact_id"], job["workflow_id"]) == (owner.agent, owner.contact, owner.workflow)

    # Every mechanism that stops a crossing is still there.
    with Session(life.repo.engine) as db:  # a workflow cannot use another organization's agent
        db.add(Workflow(organization_id=acme.organization, agent_id=shield.agent, name="Leak", trigger_type="date_offset"))

        with pytest.raises(IntegrityError, match="FOREIGN KEY"):
            db.commit()

    crossing = CallJobRequest(  # a job cannot be created for another organization's contact
        reference="x", profile_id="bank", callee=Callee(name="P", phone="+919876543210"), reason="x",
        organization_id=acme.organization, agent_id=acme.agent, contact_id=shield.contact, workflow_id=acme.workflow,
    )
    with pytest.raises(ValueError, match="Unknown contact"):
        life.in_app(lambda: life.service.create_job(crossing, dispatch=False))


def test_records_that_disagree_about_the_tenant_are_not_dialed_even_if_the_database_were_wrong(life, monkeypatch):
    ids = life.seed()
    job_id = life.schedule_only(ids)
    real = life.repo.get_contact

    def other_tenant(contact_id):
        contact = real(contact_id)
        contact.organization_id += 1000
        return contact

    monkeypatch.setattr(life.repo, "get_contact", other_tenant)
    report = life.tick(NOW + timedelta(minutes=1))

    assert (report.dispatch_report.cancelled, life.repo.get_job(job_id)["end_reason"]) == (1, "tenant_mismatch")
    assert life.w.twilio.calls == [] and life.results() == []


# === results and the organization =================================================================================


def test_a_result_belongs_to_its_jobs_organization_and_legacy_calls_have_none(life):
    manual = life.w.place().json()  # no organization
    converse(life.w, life.w.params(), "CA_fake_1")
    life.w.wait_until(lambda: life.repo.get_job(manual["job_id"])["status"] == "completed")

    ids = life.seed()
    life.tick()
    converse_domain(life.w, life.w.params(1), "CA_fake_2")
    life.w.wait_until(lambda: len(life.results()) == 2)

    by_job = {r.job_id: r.organization_id for r in life.results()}
    assert by_job[manual["job_id"]] is None
    assert by_job[[j["id"] for j in life.repo.list_jobs(10) if j["organization_id"]][0]] == ids.organization


# === a job whose call was lost ====================================================================================


SLACK = 60  # app.service.ORPHANED_JOB_SLACK_SECONDS


async def answered_job(world, callback=False, limit=None) -> dict:
    """A dispatched job the phone network reports answered, whose call then lost its session (here: never had one)."""
    job = await world.scheduled(world.seed(callback=callback))
    await world.dispatch()

    if limit is not None:
        world.repo.update_job(job["id"], max_duration_seconds=limit)

    assert world.repo.transition(job["id"], ["ringing"], "in_progress", answered_at=NOW.timestamp(), call_id="lost-session")
    return world.repo.get_job(job["id"])


def lost_at(row: dict):
    return NOW + timedelta(seconds=row["max_duration_seconds"] + SLACK)


@pytest.mark.parametrize("limit", [30, 300, 1800])
async def test_a_job_whose_session_is_gone_ends_once_it_is_past_its_own_time_limit(world, limit):
    """The live call is an in-memory session. If the process dies mid-call (or the store drops the session), the
    job would stay in_progress in the database for ever: the sweeper only knew ringing jobs and live sessions."""
    row = await answered_job(world, limit=limit)

    world.clock.to(lost_at(row) - timedelta(seconds=1))
    await world.service.sweep()
    assert world.repo.get_job(row["id"])["status"] == "in_progress", "a call may still be running until its limit + slack"

    world.clock.to(lost_at(row))
    await world.service.sweep()
    ended = world.repo.get_job(row["id"])
    assert (ended["status"], ended["end_reason"], ended["finished_at"]) == ("failed", "session_lost", lost_at(row).timestamp())
    assert ended["call_id"] == "lost-session", "the record of which call it was is kept"


async def test_a_lost_job_gets_its_callback_and_its_phone_line_is_hung_up_exactly_once(world):
    row = await answered_job(world, callback=True)
    world.clock.to(lost_at(row))

    for _ in range(3):
        await world.service.sweep()
        await world.service.drain()

    [body] = world.receiver.bodies
    assert (body["event"], body["job"]["status"], body["job"]["end_reason"]) == ("call_job.finished", "failed", "session_lost")
    assert world.repo.get_job(row["id"])["callback_status"] == "delivered"
    assert world.twilio.hangups == ["CA_fake_1"]


async def test_a_job_that_cannot_be_hung_up_at_the_provider_is_still_ended(world):
    async def refuse(sid):
        raise TwilioError("Twilio said 404: call not found")

    world.twilio.hang_up = refuse
    row = await answered_job(world)
    world.clock.to(lost_at(row))
    await world.service.sweep()

    assert world.repo.get_job(row["id"])["status"] == "failed"


async def test_a_job_with_a_live_session_is_left_to_the_session(world):
    row = await answered_job(world)
    world.service.store.add(SimpleNamespace(id="lost-session", last_active=time.time(), outbound=None, ended=False))
    world.clock.to(lost_at(row) + timedelta(hours=1))
    await world.service.sweep()

    assert world.repo.get_job(row["id"])["status"] == "in_progress"


async def test_jobs_in_every_other_state_are_left_alone(world):
    finished = await answered_job(world)
    assert world.repo.transition(finished["id"], ["in_progress"], "completed", finished_at=NOW.timestamp())
    scheduled = await world.scheduled(world.seed())
    world.clock.to(NOW + timedelta(days=30))
    await world.service.sweep()

    assert world.repo.get_job(finished["id"])["status"] == "completed"
    assert world.repo.get_job(finished["id"])["end_reason"] != "session_lost"
    assert world.repo.get_job(scheduled["id"])["status"] == SCHEDULED


async def test_only_overdue_in_progress_jobs_are_selected(world):
    lost = await answered_job(world)

    finished = await answered_job(world)
    assert world.repo.transition(finished["id"], ["in_progress"], "completed", finished_at=NOW.timestamp())

    fresh = await answered_job(world)
    world.repo.update_job(fresh["id"], answered_at=lost_at(lost).timestamp())

    await world.scheduled(world.seed())
    await world.dispatch()  # ringing
    await world.scheduled(world.seed())  # scheduled

    assert world.repo.due_in_progress(lost_at(lost).timestamp(), SLACK) == [(lost["id"], "lost-session")]


async def test_a_job_that_finishes_while_the_sweep_is_running_keeps_its_own_ending(world, monkeypatch):
    row = await answered_job(world)
    world.clock.to(lost_at(row))
    stale = world.repo.due_in_progress(lost_at(row).timestamp(), SLACK)
    assert stale == [(row["id"], "lost-session")]

    assert world.repo.transition(row["id"], ["in_progress"], "completed", finished_at=NOW.timestamp(), end_reason="hangup")
    monkeypatch.setattr(world.repo, "due_in_progress", lambda now, slack: stale)
    await world.service.sweep()

    ended = world.repo.get_job(row["id"])
    assert (ended["status"], ended["end_reason"]) == ("completed", "hangup")
    assert world.twilio.hangups == []
