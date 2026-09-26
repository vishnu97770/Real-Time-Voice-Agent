"""The boundary between recording a call job and placing the call.

    Workflow engine  ->  persisted job (status "scheduled")  ->  [Step 5 dispatcher]  ->  existing telephony

Nothing here implements the dispatcher. These tests pin down what already holds, so Step 5 can rely on it:
the engine never dials, a scheduled job is inert to every part of the live call lifecycle, existing
callers behave exactly as before, and a scheduled job can be claimed and placed with primitives that exist.
See the DISPATCH CONTRACT in app/outbound.py."""

import ast
import asyncio
import threading
import time
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from app.models import Agent, Contact, Organization, Workflow
from app.telephony.twilio import sign_stream_token, verify_stream_token
from app.workflows import Reason, WorkflowEngine
from tests.test_telephony_routes import KEY, PUBLIC, TOKEN, World, status_call
from tests.test_workflow_engine import DUE, TRIGGER

ACTION = {"profile_id": "bank", "reason": "your appointment tomorrow"}


def seed(w: World, channel: str = "phone") -> dict[str, int]:
    """An organization with an active agent, an active workflow and a contact who consented."""
    with Session(w.repo.engine) as db:
        organization = Organization(name="Acme")
        db.add(organization)
        db.flush()
        agent = Agent(organization_id=organization.id, name="Assistant", status="active")
        db.add(agent)
        db.flush()
        workflow = Workflow(
            organization_id=organization.id, agent_id=agent.id, name="Reminder", status="active",
            trigger_type="date_offset", trigger_config=dict(TRIGGER), action_config={**ACTION, "channel": channel},
        )
        contact = Contact(
            organization_id=organization.id, name="Priya Sharma", phone="+919876543210", consent_status="granted",
            metadata_={"appointment_date": "2026-09-25"},
        )
        db.add_all([workflow, contact])
        db.commit()

        return {"organization": organization.id, "agent": agent.id, "workflow": workflow.id, "contact": contact.id}


def schedule(w: World, ids: dict[str, int]):
    """Run the real workflow engine for the seeded contact, as a scheduler would."""
    engine = WorkflowEngine(w.app.state.service)
    return asyncio.run(engine.run_for_contact(ids["workflow"], ids["contact"], DUE))


def scheduled_job(w: World, channel: str = "phone") -> tuple[dict, dict[str, int]]:
    ids = seed(w, channel)
    outcome = schedule(w, ids)
    assert outcome.reason == Reason.CALL_JOB_CREATED, outcome
    return w.repo.get_job(outcome.job_id), ids


# --- the boundary --------------------------------------------------------------------------------------------


def test_the_workflow_engine_never_dials_even_when_telephony_is_fully_configured():
    with World() as w:
        job, _ = scheduled_job(w)

        assert w.twilio.calls == [] and w.twilio.hangups == [], "Twilio was not touched"
        assert (job["status"], job["channel"], job["twilio_call_sid"]) == ("scheduled", "phone", None)
        assert w.listeners == [], "and no speech recogniser was opened"


def test_the_workflow_engine_can_record_a_phone_job_with_telephony_not_configured():
    with World(configured=False) as w:
        job, _ = scheduled_job(w)

        assert job["status"] == "scheduled" and job["callee_phone"] == "+919876543210"


def test_a_manual_phone_job_still_dials_exactly_as_before():
    with World() as w:
        response = w.place(reference="manual-1")
        job = response.json()

        assert response.status_code == 201 and job["status"] == "ringing"
        (call,) = w.twilio.calls
        assert call["to"] == "+919876543210" and 10 <= call["ring_seconds"] <= 120
        assert w.repo.get_job(job["job_id"])["twilio_call_sid"] == "CA_fake_1"
        assert verify_stream_token(TOKEN, "job", job["job_id"], w.params()["token"])


def test_a_manual_web_job_still_gets_its_answer_link_and_still_needs_no_telephony():
    with World(configured=False) as w:
        response = w.place(channel="web")

        assert response.status_code == 201 and response.json()["status"] == "ringing"
        assert response.json()["answer_url"].startswith("https://console.example/?job=JOB-")


def test_a_manual_phone_job_without_telephony_is_still_refused():
    with World(configured=False) as w:
        response = w.place()

        assert response.status_code == 422 and "not configured" in response.json()["detail"]
        assert w.repo.list_jobs(10) == []


def test_the_job_api_cannot_be_told_to_skip_dispatch():
    with World() as w:
        job = w.place(dispatch=False).json()  # an unknown body field: ignored

        assert job["status"] == "ringing" and len(w.twilio.calls) == 1


def test_only_the_workflow_engine_calls_create_job_without_dispatch():
    calls, root = [], Path(__file__).parents[1]

    for path in sorted(root.joinpath("app").rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Call) and any(
                kw.arg == "dispatch" and isinstance(kw.value, ast.Constant) and kw.value.value is False
                for kw in node.keywords
            ):
                calls.append(path.relative_to(root).as_posix())

    assert calls == ["app/workflows/engine.py"]


# --- a scheduled job is inert to the live call lifecycle ------------------------------------------------------


def test_a_scheduled_phone_job_ignores_every_twilio_status_callback():
    with World() as w:
        job, _ = scheduled_job(w)

        for call_status in ("initiated", "ringing", "busy", "no-answer", "canceled", "failed", "completed"):
            assert status_call(w, job["id"], {"CallStatus": call_status}).status_code == 204

        assert w.repo.get_job(job["id"])["status"] == "scheduled"
        assert w.repo.get_job(job["id"])["callback_status"] == "none" and w.receiver.calls == []


def test_a_scheduled_phone_job_cannot_be_joined_from_the_audio_stream_even_with_a_valid_stream_token():
    """The token is what _dial would issue. Without a real dial nobody has one, but even if one existed
    the job is not ringing, so the line is hung up and no session starts."""
    with World() as w:
        job, _ = scheduled_job(w)
        params = {"kind": "job", "job_id": job["id"], "token": sign_stream_token(TOKEN, "job", job["id"], 120)}
        w.expect_dropped(params, call_sid="CA_intruder")

        assert w.twilio.hangups == ["CA_intruder"], "the caller is dropped"
        assert w.repo.get_job(job["id"])["status"] == "scheduled" and list(w.app.state.service.store.all()) == []


def test_a_scheduled_job_never_expires_and_reading_it_does_not_change_it():
    with World() as w:
        job, _ = scheduled_job(w)
        w.repo.update_job(job["id"], expires_at=1.0)  # long past
        asyncio.run(w.app.state.service.sweep())

        assert w.repo.due_ringing(time.time()) == []
        view = w.job(job["id"])
        assert view["status"] == "scheduled" and "result" not in view and view["finished_at"] is None
        assert w.repo.get_job(job["id"])["status"] == "scheduled"


@pytest.mark.parametrize("channel", ["phone", "web"])
def test_a_scheduled_job_cannot_be_answered_declined_or_cancelled_by_the_callee_endpoints(channel):
    with World() as w:
        job, _ = scheduled_job(w, channel)
        token = job["answer_token"]  # not even the real one gets anywhere

        answer = w.client.post(f"/api/call-jobs/{job['id']}/answer", json={"token": token})
        assert answer.status_code in (404, 409), "phone: 'answered by phone, not by link'; web: 'no longer available'"
        assert w.client.post(f"/api/call-jobs/{job['id']}/decline", json={"token": token}).status_code == 204
        assert w.client.get(f"/api/call-jobs/{job['id']}/ring", params={"token": token}).json()["seconds_left"] == 0

        assert w.repo.get_job(job["id"])["status"] == "scheduled", "and it is still exactly as scheduled"
        assert w.repo.get_job(job["id"])["finished_at"] is None
        assert list(w.app.state.service.store.all()) == [], "no call session was created"


def test_a_scheduled_job_is_never_shown_an_answer_token_or_link():
    with World() as w:
        job, _ = scheduled_job(w, "web")
        token = job["answer_token"]
        one = w.client.get(f"/api/call-jobs/{job['id']}", headers=KEY)
        listing = w.client.get("/api/call-jobs", headers=KEY)

        for response in (one, listing):
            assert token not in response.text and "answer_url" not in response.text and "answer_token" not in response.text


def test_replaying_a_scheduled_jobs_reference_through_the_job_api_does_not_hand_out_an_answer_link():
    """A caller who repeats the reference of a scheduled web job gets that job back, not a way to answer it."""
    with World() as w:
        job, ids = scheduled_job(w, "web")
        replay = w.client.post("/api/call-jobs", headers=KEY, json={
            "reference": job["reference"], "profile_id": "bank", "channel": "web",
            "callee": {"name": job["callee_name"], "phone": job["callee_phone"]}, "reason": job["reason"],
            "organization_id": ids["organization"], "agent_id": ids["agent"],
            "contact_id": ids["contact"], "workflow_id": ids["workflow"],
        })

        assert replay.status_code == 200 and replay.json()["job_id"] == job["id"], "the same job, not a second"
        assert replay.json()["status"] == "scheduled" and "answer_url" not in replay.json()
        assert w.repo.get_job(job["id"])["status"] == "scheduled"


# --- what the engine leaves to the dispatcher ---------------------------------------------------------------------


def test_the_engine_does_not_cancel_a_scheduled_job_when_the_contact_opts_out_later():
    """The engine decides once, when it schedules. Whatever places the call must decide again."""
    with World() as w:
        job, ids = scheduled_job(w)
        with Session(w.repo.engine) as db:
            db.get(Contact, ids["contact"]).consent_status = "revoked"
            db.commit()

        assert w.repo.get_job(job["id"])["status"] == "scheduled", "still waiting to be placed"

        engine = WorkflowEngine(w.app.state.service)
        ((_, evaluation),) = asyncio.run(engine.evaluate_contacts(ids["workflow"], DUE))
        assert (evaluation.actionable, evaluation.reason) == (False, Reason.OPTED_OUT), "the same evaluator now says no"
        assert evaluation.execution_key == "2026-09-24" and job["reference"].endswith(":2026-09-24")


def test_a_scheduled_job_can_be_claimed_by_exactly_one_dispatcher():
    """The claim is one atomic UPDATE ... WHERE status = 'scheduled'. Many dispatchers may race; one wins."""
    with World() as w:
        job, _ = scheduled_job(w)
        wins, start = [], threading.Barrier(8)

        def dispatcher():
            start.wait()
            wins.append(w.repo.transition(job["id"], ["scheduled"], "ringing", expires_at=time.time() + 120))

        threads = [threading.Thread(target=dispatcher) for _ in range(8)]
        [t.start() for t in threads]
        [t.join() for t in threads]

        assert wins.count(True) == 1 and wins.count(False) == 7
        assert w.repo.get_job(job["id"])["status"] == "ringing"
        assert w.repo.transition(job["id"], ["scheduled"], "ringing") is False, "and it cannot be claimed again"


def test_a_claimed_job_is_placed_by_the_existing_telephony_path_and_then_behaves_like_any_ringing_job():
    with World() as w:
        job, _ = scheduled_job(w)
        service = w.app.state.service

        # 1. claim, resetting expires_at (the ring time starts now, not when the job was scheduled)
        assert w.repo.transition(job["id"], ["scheduled"], "ringing", expires_at=time.time() + 90)
        # 2. place it with the code every phone job already uses
        placed = asyncio.run(service._dial(w.repo.get_job(job["id"])))

        (call,) = w.twilio.calls
        assert call["to"] == "+919876543210" and 10 <= call["ring_seconds"] <= 90
        assert placed["twilio_call_sid"] == "CA_fake_1"
        assert verify_stream_token(TOKEN, "job", job["id"], w.params()["token"])
        assert call["status_callback"] == f"{PUBLIC}/api/telephony/status?job={job['id']}"

        # 3. from here it is an ordinary ringing job: a "no answer" from Twilio ends it in the usual way
        assert status_call(w, job["id"], {"CallSid": "CA_fake_1", "CallStatus": "no-answer"}).status_code == 204
        assert w.repo.get_job(job["id"])["status"] == "no_answer"


def test_a_claim_that_forgets_to_reset_expires_at_is_swept_as_no_answer_at_once():
    """Why the claim must set expires_at: the value stored at scheduling time means nothing later."""
    with World() as w:
        job, _ = scheduled_job(w)
        w.repo.update_job(job["id"], expires_at=time.time() - 3600)  # the job has been waiting for an hour
        assert w.repo.transition(job["id"], ["scheduled"], "ringing")  # ... and is claimed WITHOUT a reset
        asyncio.run(w.app.state.service.sweep())

        assert w.repo.get_job(job["id"])["status"] == "no_answer"
        assert w.twilio.calls == []
