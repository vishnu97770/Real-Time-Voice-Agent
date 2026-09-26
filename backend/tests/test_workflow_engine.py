"""Step 4: the workflow engine and call eligibility.

Every test passes an explicit `now`; none reads the clock. Times are in IST (UTC+05:30) unless a
test says otherwise. The appointment is on 2026-09-25 and the workflow fires one day before, at 10:00."""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import JobRow, Repository
from app.models import Agent, Contact, Organization, Workflow
from app.outbound import Callee, CallJobRequest
from app.service import NotFound
from app.workflows import (
    Reason,
    WorkflowEngine,
    check_contact_eligibility,
    evaluate_trigger,
    evaluate_workflow,
    workflow_reference,
)
from tests.helpers import migrate
from tests.test_outbound import KEY, Env

IST = ZoneInfo("Asia/Kolkata")
TRIGGER = {"reference_field": "appointment_date", "offset_days": -1, "time": "10:00", "timezone": "Asia/Kolkata"}
ACTION = {"profile_id": "bank", "reason": "your appointment tomorrow"}
PHONE = "+919876543210"


def at(day: int, hour: int = 10, minute: int = 0, second: int = 0) -> datetime:
    return datetime(2026, 9, day, hour, minute, second, tzinfo=IST)


DUE = at(24, 10)  # the moment the workflow becomes due


# --- plain objects for the pure functions (no database) -----------------------------------------


def wf(**fields) -> Workflow:
    return Workflow(**{
        "id": 1, "organization_id": 1, "agent_id": 1, "name": "Appointment Reminder", "status": "active",
        "trigger_type": "date_offset", "trigger_config": dict(TRIGGER), "conditions": [],
        "action_config": dict(ACTION), "retry_policy": {}, **fields,
    })


def ct(**fields) -> Contact:
    return Contact(**{
        "id": 1, "organization_id": 1, "name": "Rahul", "phone": PHONE, "status": "active",
        "consent_status": "granted", "metadata_": {"appointment_date": "2026-09-25", "doctor": "Dr. Sharma"},
        "preferred_contact_time": None, **fields,
    })


def ag(**fields) -> Agent:
    return Agent(**{"id": 1, "organization_id": 1, "name": "Patient Follow-up Assistant", "status": "active", **fields})


def og(**fields) -> Organization:
    return Organization(**{"id": 1, "name": "Acme Health", "status": "active", **fields})


def trigger(now: datetime, **config):
    return evaluate_trigger("date_offset", {**TRIGGER, **config}, {"appointment_date": "2026-09-25"}, now)


# === trigger tests ============================================================================


def test_1_a_date_offset_workflow_is_due_from_its_time_on_the_day_before():
    for now in (at(24, 10), at(24, 10, 5), at(24, 15, 30), at(24, 23, 59, 59)):
        result = trigger(now)
        assert result.due and result.reason == Reason.WORKFLOW_DUE, now
        assert result.execution_key == "2026-09-24"
        assert result.due_at == DUE


def test_1b_the_moment_is_judged_in_the_workflows_timezone_whatever_zone_now_is_given_in():
    utc = timezone.utc
    assert trigger(datetime(2026, 9, 24, 4, 30, tzinfo=utc)).due, "04:30 UTC is 10:00 in Kolkata"
    assert trigger(datetime(2026, 9, 24, 4, 29, 59, tzinfo=utc)).reason == Reason.NOT_YET_DUE
    assert trigger(datetime(2026, 9, 24, 18, 29, 59, tzinfo=utc)).due, "still the 24th in Kolkata"
    assert trigger(datetime(2026, 9, 24, 18, 30, tzinfo=utc)).reason == Reason.WINDOW_PASSED, "the 25th in Kolkata"


def test_2_it_is_not_due_before_the_target_time_or_the_day_before_that():
    early = trigger(at(24, 9, 59, 59))
    assert (early.due, early.reason, early.execution_key) == (False, Reason.NOT_YET_DUE, None)
    assert early.due_at == DUE, "and it says when it will be due"
    assert trigger(at(20, 12)).reason == Reason.NOT_YET_DUE


def test_3_it_is_not_due_after_its_day_because_there_is_one_execution_per_date():
    for now in (at(25, 0, 0), at(25, 10), at(30, 10)):
        result = trigger(now)
        assert (result.due, result.reason) == (False, Reason.WINDOW_PASSED)
        assert "2026-09-24" in result.detail


@pytest.mark.parametrize("offset, day", [(0, 25), (-2, 23), (3, 28), (-25, 31 - 6)])
def test_3b_the_offset_may_be_zero_negative_or_positive(offset, day):
    target = datetime(2026, 9, 25, tzinfo=IST).date() + timedelta(days=offset)
    result = trigger(datetime(target.year, target.month, target.day, 10, tzinfo=IST), offset_days=offset)

    assert result.due and result.execution_key == target.isoformat()


def test_3c_the_time_defaults_to_midnight_and_the_timezone_to_utc():
    config = {"reference_field": "appointment_date", "offset_days": -1}
    meta = {"appointment_date": "2026-09-25"}

    assert evaluate_trigger("date_offset", config, meta, datetime(2026, 9, 24, 0, 0, tzinfo=timezone.utc)).due
    assert evaluate_trigger("date_offset", config, meta, datetime(2026, 9, 23, 23, 59, tzinfo=timezone.utc)).reason == Reason.NOT_YET_DUE


@pytest.mark.parametrize("metadata", [{}, {"appointment_date": None}, {"appointment_date": ""}, {"other": "2026-09-25"}])
def test_4_a_missing_reference_field_is_reported(metadata):
    result = evaluate_trigger("date_offset", TRIGGER, metadata, DUE)

    assert (result.due, result.reason) == (False, Reason.REFERENCE_DATE_MISSING)
    assert "appointment_date" in result.detail


@pytest.mark.parametrize("value", ["next friday", "2026-02-30", "25/09/2026", 20260925, ["2026-09-25"], True])
def test_5_an_invalid_reference_date_is_reported(value):
    result = evaluate_trigger("date_offset", TRIGGER, {"appointment_date": value}, DUE)

    assert (result.due, result.reason) == (False, Reason.REFERENCE_DATE_INVALID)


def test_5b_a_reference_datetime_is_read_as_a_date_in_the_workflows_timezone():
    # 23:30 on the 25th in New York is already 09:00 on the 26th in Kolkata
    result = evaluate_trigger("date_offset", {**TRIGGER, "offset_days": 0}, {"appointment_date": "2026-09-25T23:30:00-04:00"}, at(26, 10))
    assert result.due and result.execution_key == "2026-09-26"

    naive = evaluate_trigger("date_offset", {**TRIGGER, "offset_days": 0}, {"appointment_date": "2026-09-25T23:30:00"}, at(25, 10))
    assert naive.due and naive.execution_key == "2026-09-25", "no offset given: the date as written"


@pytest.mark.parametrize("config", [
    {}, {"reference_field": "appointment_date"}, {"offset_days": -1},
    {**TRIGGER, "offset_days": "-1"}, {**TRIGGER, "offset_days": 1.5}, {**TRIGGER, "offset_days": True},
    {**TRIGGER, "reference_field": ""}, {**TRIGGER, "time": "10am"}, {**TRIGGER, "time": "25:00"},
    {**TRIGGER, "timezone": "Mars/Olympus"}, {**TRIGGER, "timezone": None},
])
def test_5c_a_broken_trigger_config_is_reported_not_guessed(config):
    result = evaluate_trigger("date_offset", config, {"appointment_date": "2026-09-25"}, DUE)

    assert (result.due, result.reason) == (False, Reason.TRIGGER_CONFIG_INVALID)


def test_5d_extreme_offsets_do_not_crash():
    result = trigger(DUE, offset_days=10**9)
    assert result.reason == Reason.REFERENCE_DATE_INVALID


def test_6_an_unsupported_trigger_type_is_reported():
    for kind in ("cron", "manual", "event", ""):
        result = evaluate_trigger(kind, TRIGGER, {}, DUE)
        assert (result.due, result.reason) == (False, Reason.UNSUPPORTED_TRIGGER_TYPE)


def test_6b_a_trigger_config_that_is_not_an_object_is_reported():
    assert evaluate_trigger("date_offset", ["x"], {}, DUE).reason == Reason.TRIGGER_CONFIG_INVALID


@pytest.mark.parametrize("status", ["draft", "paused", "archived"])
def test_7_only_an_active_workflow_is_evaluated(status):
    result = evaluate_workflow(wf(status=status), ct(), DUE)

    assert (result.due, result.eligible, result.reason) == (False, False, Reason.WORKFLOW_INACTIVE)
    assert status in result.detail


def test_7b_a_naive_now_is_a_programming_error_not_a_guess():
    with pytest.raises(ValueError, match="timezone-aware"):
        evaluate_workflow(wf(), ct(), datetime(2026, 9, 24, 10, 0))

    with pytest.raises(ValueError, match="timezone-aware"):
        evaluate_trigger("date_offset", TRIGGER, {}, datetime(2026, 9, 24, 10, 0))


# === eligibility tests ========================================================================


def eligible(contact: Contact, now: datetime = DUE, channel: str = "phone", **kw):
    return check_contact_eligibility(contact, now, channel=channel, **kw)


def test_8_an_active_contact_with_consent_and_a_valid_phone_is_eligible():
    result = eligible(ct())

    assert result.eligible and result.reason == Reason.CONTACT_ELIGIBLE


def test_9_an_inactive_contact_is_rejected():
    result = eligible(ct(status="inactive"))

    assert (result.eligible, result.reason) == (False, Reason.CONTACT_INACTIVE)


def test_10_denied_consent_is_rejected_and_reported_as_an_opt_out():
    """The existing consent states are granted / unknown / revoked. Revoked is the only 'no', so it
    is what 'denied' and 'opted out' both mean."""
    result = eligible(ct(consent_status="revoked"))

    assert (result.eligible, result.reason) == (False, Reason.OPTED_OUT)


def test_11_unknown_consent_never_produces_a_call():
    result = eligible(ct(consent_status="unknown"))

    assert (result.eligible, result.reason) == (False, Reason.CONSENT_UNKNOWN)
    assert not eligible(ct(consent_status="something-new")).eligible, "and neither does an unrecognised value"


def test_12_opted_out_is_rejected_even_when_everything_else_is_fine():
    result = evaluate_workflow(wf(), ct(consent_status="revoked"), DUE)

    assert (result.due, result.eligible, result.reason) == (True, False, Reason.OPTED_OUT)
    assert result.execution_key == "2026-09-24", "it was due; the contact is why there is no call"


@pytest.mark.parametrize("phone", [None, "", "   "])
def test_13_a_missing_phone_is_rejected(phone):
    assert eligible(ct(phone=phone)).reason == Reason.PHONE_MISSING


@pytest.mark.parametrize("phone", ["abc", "12345", "+", "call me", "+91-98765-4321x", "98765" * 6, "++919876543210"])
def test_14_an_invalid_phone_is_rejected(phone):
    result = eligible(ct(phone=phone), channel="web")

    assert (result.eligible, result.reason) == (False, Reason.PHONE_INVALID)


def test_14b_a_phone_call_needs_international_format_but_a_web_link_does_not():
    local = "098765 43210"

    assert eligible(ct(phone=local), channel="web").eligible
    result = eligible(ct(phone=local), channel="phone")
    assert (result.eligible, result.reason) == (False, Reason.PHONE_INVALID)
    assert "international" in result.detail


def test_14c_the_phone_number_is_never_rewritten():
    contact = ct(phone="+91 98765 43210")
    assert eligible(contact).eligible
    assert contact.phone == "+91 98765 43210"


WINDOW = {"start": "10:00", "end": "18:00", "timezone": "Asia/Kolkata"}


def test_15_a_contact_outside_their_preferred_window_is_rejected():
    contact = ct(preferred_contact_time=WINDOW)

    assert eligible(contact, at(24, 10)).eligible, "start is inclusive"
    assert eligible(contact, at(24, 17, 59)).eligible
    for now in (at(24, 9, 59), at(24, 18, 0), at(24, 22)):
        result = eligible(contact, now)
        assert (result.eligible, result.reason) == (False, Reason.OUTSIDE_CONTACT_WINDOW), now


def test_15b_the_window_is_read_in_its_own_timezone_and_may_wrap_midnight():
    london = ct(preferred_contact_time={"start": "09:00", "end": "12:00", "timezone": "Europe/London"})
    assert eligible(london, datetime(2026, 9, 24, 10, 0, tzinfo=timezone.utc)).eligible, "11:00 in London (BST)"
    assert not eligible(london, datetime(2026, 9, 24, 12, 0, tzinfo=timezone.utc)).eligible

    night = ct(preferred_contact_time={"start": "20:00", "end": "06:00", "timezone": "UTC"})
    assert eligible(night, datetime(2026, 9, 24, 23, 0, tzinfo=timezone.utc)).eligible
    assert eligible(night, datetime(2026, 9, 24, 5, 59, tzinfo=timezone.utc)).eligible
    assert not eligible(night, datetime(2026, 9, 24, 12, 0, tzinfo=timezone.utc)).eligible


def test_15c_a_window_without_a_timezone_uses_the_workflows_and_no_window_means_any_time():
    bare = ct(preferred_contact_time={"start": "10:00", "end": "18:00"})

    assert eligible(bare, at(24, 12), default_timezone="Asia/Kolkata").eligible
    assert not eligible(bare, at(24, 12), default_timezone="UTC").eligible, "07:00 UTC is outside"
    assert eligible(ct(preferred_contact_time={}), at(24, 3)).eligible
    assert eligible(ct(preferred_contact_time=None), at(24, 3)).eligible


@pytest.mark.parametrize("window", [
    {"start": "10:00"}, {"end": "18:00"}, {"start": "10:00", "end": "10:00"}, {"start": "ten", "end": "18:00"},
    {"start": "10:00", "end": "18:00", "timezone": "Mars/Olympus"}, "10-18", ["10:00", "18:00"],
])
def test_15d_a_window_that_cannot_be_read_refuses_the_call(window):
    result = eligible(ct(preferred_contact_time=window))

    assert (result.eligible, result.reason) == (False, Reason.CONTACT_WINDOW_INVALID)


def test_15e_the_workflow_can_be_due_while_the_contact_window_is_closed():
    contact = ct(preferred_contact_time={**WINDOW, "start": "14:00"})
    result = evaluate_workflow(wf(), contact, DUE)

    assert (result.due, result.eligible, result.reason) == (True, False, Reason.OUTSIDE_CONTACT_WINDOW)


def test_the_first_failing_check_is_the_one_reported():
    worst = ct(status="inactive", consent_status="revoked", phone=None, preferred_contact_time="junk")

    assert eligible(worst).reason == Reason.CONTACT_INACTIVE
    assert eligible(ct(consent_status="revoked", phone=None)).reason == Reason.OPTED_OUT
    assert eligible(ct(phone=None, preferred_contact_time="junk")).reason == Reason.PHONE_MISSING


# === evaluating a whole workflow (still pure) =================================================


def test_a_due_workflow_and_an_eligible_contact_is_actionable_and_carries_its_action():
    result = evaluate_workflow(wf(), ct(), DUE, agent=ag(), organization=og())

    assert result.actionable and (result.due, result.eligible, result.reason) == (True, True, Reason.WORKFLOW_DUE)
    assert result.execution_key == "2026-09-24"
    assert (result.action.profile_id, result.action.channel) == ("bank", "phone"), "an automated call rings a phone"


def test_a_suspended_organization_and_an_agent_that_is_not_active_stop_the_call():
    assert evaluate_workflow(wf(), ct(), DUE, organization=og(status="suspended")).reason == Reason.ORGANIZATION_INACTIVE

    for status in ("draft", "archived"):
        assert evaluate_workflow(wf(), ct(), DUE, agent=ag(status=status)).reason == Reason.AGENT_INACTIVE


def test_conditions_the_engine_cannot_evaluate_stop_the_call_instead_of_being_ignored():
    result = evaluate_workflow(wf(conditions=[{"field": "appointment.status", "op": "eq", "value": "scheduled"}]), ct(), DUE)

    assert (result.due, result.reason) == (False, Reason.CONDITIONS_UNSUPPORTED)


@pytest.mark.parametrize("action", [
    {}, {"profile_id": "bank"}, {"reason": "x"}, {"profile_id": "bank", "reason": ""},
    {"profile_id": "bank", "reason": "x" * 201}, {"profile_id": "bank", "reason": "x", "channel": "sms"},
    {"profile_id": "bank", "reason": "x", "organization_id": 99},  # a workflow cannot choose its tenant
    {"profile_id": "bank", "reason": "x", "agent_id": 7},
])
def test_an_invalid_action_config_is_reported(action):
    assert evaluate_workflow(wf(action_config=action), ct(), DUE).reason == Reason.ACTION_CONFIG_INVALID


def test_a_reference_names_one_execution_and_fits_the_column():
    assert workflow_reference(12, 345, "2026-09-24") == "wf:12:contact:345:2026-09-24"
    assert workflow_reference(12, 345, "2026-09-24") != workflow_reference(12, 345, "2026-09-25")
    assert workflow_reference(12, 345, "2026-09-24") != workflow_reference(13, 345, "2026-09-24")

    huge = workflow_reference(2**60, 2**60, "k" * 500)
    assert len(huge) <= 64 and huge == workflow_reference(2**60, 2**60, "k" * 500)


# === call job tests: the engine against a real database =======================================


class NoDial:
    """Stands in for telephony. Placing a call would touch it, and touching it fails the test."""

    def __getattr__(self, name):
        raise AssertionError(f"the workflow engine tried to use telephony ({name})")


class World:
    """Two organizations on a migrated database, each with an active agent, a workflow and a contact."""

    def __init__(self, repo: Repository, env: Env):
        self.repo, self.env = repo, env
        self.engine = WorkflowEngine(env.service)
        self.calls: list[tuple[CallJobRequest, bool]] = []

        original = env.service.create_job

        async def spy(request, *, dispatch=True):
            self.calls.append((request, dispatch))
            return await original(request, dispatch=dispatch)

        env.service.create_job = spy

        with Session(repo.engine) as db:
            for tag in ("acme", "shield"):
                organization = Organization(name=tag)
                db.add(organization)
                db.flush()
                assistant = Agent(organization_id=organization.id, name=f"{tag} assistant", status="active")
                db.add(assistant)
                db.flush()
                flow = Workflow(
                    organization_id=organization.id, agent_id=assistant.id, name="Appointment Reminder", status="active",
                    trigger_type="date_offset", trigger_config=dict(TRIGGER), action_config=dict(ACTION),
                )
                db.add(flow)
                person = Contact(
                    organization_id=organization.id, name=f"{tag} Rahul", phone=PHONE, consent_status="granted",
                    metadata_={"appointment_date": "2026-09-25", "doctor": "Dr. Sharma"},
                )
                db.add(person)
                db.flush()
                setattr(self, tag, organization.id)
                setattr(self, f"{tag}_agent", assistant.id)
                setattr(self, f"{tag}_workflow", flow.id)
                setattr(self, f"{tag}_contact", person.id)

            db.commit()

    def jobs(self) -> list[dict]:
        return self.repo.list_jobs(100)

    def change(self, model, ident: int, **fields) -> None:
        with Session(self.repo.engine) as db:
            row = db.get(model, ident)
            for key, value in fields.items():
                setattr(row, key, value)
            db.commit()

    def add_contact(self, organization_id: int, **fields) -> int:
        with Session(self.repo.engine) as db:
            row = Contact(organization_id=organization_id, **{
                "name": "Extra", "phone": PHONE, "consent_status": "granted",
                "metadata_": {"appointment_date": "2026-09-25"}, **fields})
            db.add(row)
            db.commit()
            return row.id

    async def run(self, now: datetime = DUE):
        return await self.engine.run_for_contact(self.acme_workflow, self.acme_contact, now)


@pytest.fixture
async def world(tmp_path):
    url = f"sqlite:///{tmp_path / 'engine.db'}"
    migrate(url)
    repo = Repository(url)  # the application's own repository: foreign keys enforced
    env = Env(repo=repo)
    env.service.telephony = NoDial()
    yield World(repo, env)
    await env.client.aclose()
    repo.engine.dispose()


async def test_16_an_eligible_workflow_creates_exactly_one_call_job(world):
    outcome = await world.run()

    assert (outcome.due, outcome.eligible, outcome.call_job_created, outcome.reason) == (True, True, True, Reason.CALL_JOB_CREATED)
    assert outcome.job_id and outcome.job_id.startswith("JOB-")
    assert [job["id"] for job in world.jobs()] == [outcome.job_id]


async def test_17_the_call_job_links_organization_agent_contact_and_workflow(world):
    outcome = await world.run()
    job = world.repo.get_job(outcome.job_id)

    assert (job["organization_id"], job["agent_id"], job["contact_id"], job["workflow_id"]) == (
        world.acme, world.acme_agent, world.acme_contact, world.acme_workflow,
    )
    assert (job["callee_name"], job["callee_phone"], job["channel"]) == ("acme Rahul", PHONE, "phone")
    assert (job["profile_id"], job["reason"]) == ("bank", "your appointment tomorrow")
    assert job["reference"] == f"wf:{world.acme_workflow}:contact:{world.acme_contact}:2026-09-24"

    with Session(world.repo.engine) as db:  # and they resolve through the ORM
        row = db.get(JobRow, outcome.job_id)
        assert (row.organization.name, row.agent.name, row.contact.name, row.workflow.name) == (
            "acme", "acme assistant", "acme Rahul", "Appointment Reminder",
        )


async def test_18_the_existing_call_job_path_is_used_and_nothing_is_dialed(world):
    outcome = await world.run()

    ((request, dispatch),) = world.calls
    assert dispatch is False, "asked to record the job, not to place the call"
    assert isinstance(request, CallJobRequest) and request.reference == world.repo.get_job(outcome.job_id)["reference"]
    assert (request.organization_id, request.agent_id, request.contact_id, request.workflow_id) == (
        world.acme, world.acme_agent, world.acme_contact, world.acme_workflow,
    )

    job = world.repo.get_job(outcome.job_id)
    assert job["status"] == "scheduled" and job["twilio_call_sid"] is None
    assert job["answer_token"] and job["callback_status"] == "none", "a normal job in every other way"
    # NoDial would have failed the test on any use of telephony; the Twilio side saw nothing either.
    assert world.env.receiver.calls == []


async def test_18b_a_scheduled_job_is_inert_to_the_existing_call_lifecycle(world):
    job_id = (await world.run()).job_id
    token = world.repo.get_job(job_id)["answer_token"]
    world.repo.update_job(job_id, expires_at=1.0)  # long "expired"

    await world.env.service.sweep()
    assert world.repo.get_job(job_id)["status"] == "scheduled", "the ring-timeout sweeper does not touch it"
    assert (await world.env.status(job_id))["status"] == "scheduled", "reading it does not expire it"

    answer = await world.env.client.post(f"/api/call-jobs/{job_id}/answer", json={"token": token})
    assert answer.status_code in (404, 409), "and nobody can answer a call that has not been placed"
    assert world.repo.get_job(job_id)["status"] == "scheduled"


async def test_18c_creating_a_job_without_dispatch_issues_no_answer_link_but_the_default_still_does(world):
    def request(reference):
        return CallJobRequest(reference=reference, profile_id="bank", channel="web",
                              callee=Callee(name="P", phone=PHONE), reason="x")

    quiet, _ = await world.env.service.create_job(request("plain-1"), dispatch=False)
    normal, _ = await world.env.service.create_job(request("plain-2"))

    assert quiet["status"] == "scheduled" and "answer_url" not in quiet
    assert normal["status"] == "ringing" and "answer_url" in normal


async def test_19_evaluating_the_same_execution_again_creates_no_duplicate(world):
    first = await world.run(at(24, 10, 0))
    second = await world.run(at(24, 10, 5))
    third = await world.run(at(24, 10, 10))
    much_later = await world.run(at(24, 23, 59))

    assert first.call_job_created
    for repeat in (second, third, much_later):
        assert (repeat.due, repeat.eligible, repeat.call_job_created, repeat.reason) == (True, True, False, Reason.ALREADY_SCHEDULED)
    assert len(world.jobs()) == 1
    assert len(world.calls) == 1, "and the job path was not even asked again"


async def test_20_the_existing_job_is_returned_for_the_same_execution(world):
    first = await world.run()
    again = await world.run(at(24, 10, 5))

    assert again.job_id == first.job_id
    assert again.execution_key == first.execution_key == "2026-09-24"


async def test_20b_concurrent_evaluations_still_make_one_job(world):
    import asyncio

    outcomes = await asyncio.gather(*(world.run(at(24, 10, n)) for n in range(6)))

    assert len(world.jobs()) == 1
    assert len({o.job_id for o in outcomes}) == 1
    assert sum(o.call_job_created for o in outcomes) == 1


async def test_20c_editing_the_workflow_after_the_job_exists_does_not_break_or_duplicate_it(world):
    first = await world.run()
    world.change(Workflow, world.acme_workflow, action_config={**ACTION, "reason": "a reworded reason"})
    again = await world.run(at(24, 11))

    assert (again.call_job_created, again.reason, again.job_id) == (False, Reason.ALREADY_SCHEDULED, first.job_id)
    assert len(world.jobs()) == 1


async def test_20d_a_rescheduled_appointment_is_a_new_execution_and_other_workflows_are_separate(world):
    first = await world.run()
    world.change(Contact, world.acme_contact, metadata_={"appointment_date": "2026-09-28"})
    moved = await world.run(at(27, 10))

    assert moved.call_job_created and moved.job_id != first.job_id and moved.execution_key == "2026-09-27"

    with Session(world.repo.engine) as db:  # a second workflow, same contact, same day
        other = Workflow(organization_id=world.acme, agent_id=world.acme_agent, name="Second", status="active",
                         trigger_type="date_offset", trigger_config=dict(TRIGGER, offset_days=-3), action_config=dict(ACTION))
        db.add(other)
        db.commit()
        other_id = other.id

    world.change(Contact, world.acme_contact, metadata_={"appointment_date": "2026-09-25"})
    parallel = await world.engine.run_for_contact(other_id, world.acme_contact, at(22, 10))
    assert parallel.call_job_created and parallel.job_id not in (first.job_id, moved.job_id)
    assert len(world.jobs()) == 3


@pytest.mark.parametrize("change, reason", [
    ({"consent_status": "revoked"}, Reason.OPTED_OUT),
    ({"consent_status": "unknown"}, Reason.CONSENT_UNKNOWN),
    ({"status": "inactive"}, Reason.CONTACT_INACTIVE),
    ({"phone": None}, Reason.PHONE_MISSING),
    ({"phone": "nope"}, Reason.PHONE_INVALID),
    ({"preferred_contact_time": {"start": "14:00", "end": "18:00", "timezone": "Asia/Kolkata"}}, Reason.OUTSIDE_CONTACT_WINDOW),
    ({"metadata_": {}}, Reason.REFERENCE_DATE_MISSING),
])
async def test_a_rejected_contact_gets_a_reason_and_no_call_job(world, change, reason):
    world.change(Contact, world.acme_contact, **change)
    outcome = await world.run()

    assert (outcome.call_job_created, outcome.job_id, outcome.reason) == (False, None, reason)
    assert world.jobs() == [] and world.calls == []


@pytest.mark.parametrize("model_change, reason", [
    ((Workflow, "acme_workflow", {"status": "paused"}), Reason.WORKFLOW_INACTIVE),
    ((Agent, "acme_agent", {"status": "archived"}), Reason.AGENT_INACTIVE),
    ((Organization, "acme", {"status": "suspended"}), Reason.ORGANIZATION_INACTIVE),
])
async def test_an_inactive_workflow_agent_or_organization_creates_nothing(world, model_change, reason):
    model, attr, fields = model_change
    world.change(model, getattr(world, attr), **fields)
    outcome = await world.run()

    assert (outcome.call_job_created, outcome.reason) == (False, reason)
    assert world.jobs() == []


async def test_a_workflow_that_is_not_due_yet_reports_it_and_creates_nothing(world):
    outcome = await world.run(at(24, 9, 59))

    assert (outcome.due, outcome.eligible, outcome.call_job_created, outcome.reason) == (False, False, False, Reason.NOT_YET_DUE)
    assert world.jobs() == []


async def test_a_job_the_service_refuses_is_a_structured_rejection_not_a_crash(world):
    world.change(Workflow, world.acme_workflow, action_config={"profile_id": "no-such-profile", "reason": "x"})
    outcome = await world.run()

    assert (outcome.due, outcome.eligible, outcome.call_job_created, outcome.reason) == (True, True, False, Reason.CALL_JOB_REJECTED)
    assert "Unknown profile" in outcome.detail and world.jobs() == []


async def test_a_database_failure_is_not_swallowed(world, monkeypatch):
    def broken(*_):
        raise RuntimeError("database is down")

    monkeypatch.setattr(world.repo, "get_job_by_reference", broken)

    with pytest.raises(RuntimeError, match="database is down"):
        await world.run()


async def test_run_covers_every_contact_of_the_organization_and_only_the_right_ones_get_jobs(world):
    opted_out = world.add_contact(world.acme, name="Opted Out", consent_status="revoked")
    later = world.add_contact(world.acme, name="Later", metadata_={"appointment_date": "2026-10-30"})
    unknown = world.add_contact(world.acme, name="Unknown", consent_status="unknown")

    outcomes = {o.contact_id: o for o in await world.engine.run(world.acme_workflow, DUE)}

    assert set(outcomes) == {world.acme_contact, opted_out, later, unknown}, "and never the other organization's contact"
    assert outcomes[world.acme_contact].reason == Reason.CALL_JOB_CREATED
    assert outcomes[opted_out].reason == Reason.OPTED_OUT
    assert outcomes[later].reason == Reason.NOT_YET_DUE
    assert outcomes[unknown].reason == Reason.CONSENT_UNKNOWN
    assert [job["contact_id"] for job in world.jobs()] == [world.acme_contact]

    rerun = await world.engine.run(world.acme_workflow, at(24, 10, 5))
    assert [o.reason for o in rerun if o.contact_id == world.acme_contact] == [Reason.ALREADY_SCHEDULED]
    assert len(world.jobs()) == 1


async def test_evaluate_contacts_answers_who_is_eligible_without_creating_anything(world):
    world.add_contact(world.acme, name="Opted Out", consent_status="revoked")
    results = await world.engine.evaluate_contacts(world.acme_workflow, DUE)

    assert sorted(e.reason for _, e in results) == [Reason.OPTED_OUT, Reason.WORKFLOW_DUE]
    assert [cid for cid, e in results if e.actionable] == [world.acme_contact]
    assert world.jobs() == [] and world.calls == []


async def test_the_outcome_is_a_plain_dict_a_scheduler_can_log(world):
    outcome = (await world.run()).as_dict()

    assert outcome == {
        "workflow_id": world.acme_workflow, "contact_id": world.acme_contact, "due": True, "eligible": True,
        "call_job_created": True, "reason": "call_job_created", "job_id": outcome["job_id"], "detail": None,
        "execution_key": "2026-09-24",
    }


async def test_an_unknown_workflow_or_contact_is_not_found(world):
    with pytest.raises(NotFound):
        await world.engine.run(9999, DUE)

    with pytest.raises(NotFound):
        await world.engine.run_for_contact(world.acme_workflow, 9999, DUE)


# --- tenant tests --------------------------------------------------------------------------------


async def test_21_a_workflow_and_a_contact_from_different_organizations_cannot_produce_a_call_job(world):
    outcome = await world.engine.run_for_contact(world.acme_workflow, world.shield_contact, DUE)

    assert (outcome.call_job_created, outcome.reason) == (False, Reason.ORGANIZATION_MISMATCH)
    assert world.jobs() == [] and world.calls == []

    # Even if the engine's own check were skipped, the existing layers refuse it:
    crossing = CallJobRequest(
        reference="x", profile_id="bank", callee=Callee(name="P", phone=PHONE), reason="x",
        organization_id=world.acme, agent_id=world.acme_agent, contact_id=world.shield_contact,
        workflow_id=world.acme_workflow,
    )
    with pytest.raises(ValueError, match="Unknown contact"):  # the service's check (a 422 on the API)
        await world.env.service.create_job(crossing, dispatch=False)

    with pytest.raises(IntegrityError, match="FOREIGN KEY"):  # and the database's
        world.repo.create_job({"id": "JOB-X", "profile_id": "bank", "callee_name": "P", "callee_phone": PHONE, "reason": "x",
                               "status": "scheduled", "answer_token": "t", "created_at": 1.0, "expires_at": 2.0,
                               "max_duration_seconds": 300, "callback_status": "none", "callback_attempts": 0,
                               "organization_id": world.acme, "contact_id": world.shield_contact})
    assert world.jobs() == []


async def test_22_an_agent_and_a_workflow_from_different_organizations_cannot_produce_a_call_job(world):
    # Such a workflow cannot even be stored (Step 2's composite foreign key) ...
    with Session(world.repo.engine) as db:
        db.add(Workflow(organization_id=world.acme, agent_id=world.shield_agent, name="Leak", trigger_type="date_offset"))

        with pytest.raises(IntegrityError, match="FOREIGN KEY"):
            db.commit()

    # ... and were one somehow handed to the engine, it refuses to act on it.
    result = evaluate_workflow(wf(organization_id=world.acme), ct(organization_id=world.acme), DUE,
                               agent=ag(organization_id=world.shield))
    assert (result.actionable, result.reason) == (False, Reason.ORGANIZATION_MISMATCH)

    crossing = CallJobRequest(
        reference="y", profile_id="bank", callee=Callee(name="P", phone=PHONE), reason="x",
        organization_id=world.acme, agent_id=world.shield_agent,
    )
    with pytest.raises(ValueError, match="Unknown agent"):
        await world.env.service.create_job(crossing, dispatch=False)
    assert world.jobs() == []


async def test_each_organizations_workflow_only_ever_calls_its_own_contacts(world):
    acme = await world.engine.run(world.acme_workflow, DUE)
    shield = await world.engine.run(world.shield_workflow, DUE)

    assert [o.contact_id for o in acme] == [world.acme_contact]
    assert [o.contact_id for o in shield] == [world.shield_contact]
    by_org = {job["organization_id"]: job for job in world.jobs()}
    assert by_org[world.acme]["contact_id"] == world.acme_contact
    assert by_org[world.shield]["contact_id"] == world.shield_contact and by_org[world.shield]["agent_id"] == world.shield_agent


# --- the existing API is unchanged ---------------------------------------------------------------


async def test_the_job_api_still_creates_a_ringing_job_with_an_answer_link(world):
    created = await world.env.job()

    assert created["status"] == "ringing" and created["answer_url"].startswith("https://agent.example/?job=")


# === review hardening: DST, extreme dates, phone strictness ====================================

NEW_YORK = ZoneInfo("America/New_York")


def ny_trigger(day: str, at_time: str, now: datetime, offset: int = 0):
    config = {"reference_field": "d", "offset_days": offset, "time": at_time, "timezone": "America/New_York"}
    return evaluate_trigger("date_offset", config, {"d": day}, now)


def test_dst_spring_forward_a_time_that_does_not_exist_is_due_at_the_same_instant_however_now_is_written():
    """02:30 on 2026-03-08 never happens in New York (clocks jump 02:00 -> 03:00). It is read as 02:30
    EST, which is 07:30 UTC, i.e. 03:30 EDT. The answer must not depend on which zone `now` is written in."""
    before_utc = datetime(2026, 3, 8, 7, 29, tzinfo=timezone.utc)
    at_utc = datetime(2026, 3, 8, 7, 30, tzinfo=timezone.utc)

    for write_as in (timezone.utc, NEW_YORK, IST):
        early, exact = before_utc.astimezone(write_as), at_utc.astimezone(write_as)
        assert ny_trigger("2026-03-08", "02:30", early).reason == Reason.NOT_YET_DUE, f"written in {write_as}"
        assert ny_trigger("2026-03-08", "02:30", exact).due, f"written in {write_as}"


def test_dst_fall_back_an_ambiguous_time_uses_its_first_occurrence_and_the_repeat_is_the_same_execution():
    """01:30 happens twice on 2026-11-01. The first (EDT, 05:30 UTC) is the due time; the second (EST,
    06:30 UTC) is later the same day, so still due, and still the same execution: no second call."""
    first = datetime(2026, 11, 1, 1, 30, tzinfo=NEW_YORK)  # fold=0
    second = datetime(2026, 11, 1, 1, 30, fold=1, tzinfo=NEW_YORK)

    assert first.astimezone(timezone.utc).hour == 5 and second.astimezone(timezone.utc).hour == 6
    assert ny_trigger("2026-11-01", "01:30", first - timedelta(minutes=1)).reason == Reason.NOT_YET_DUE
    a, b = ny_trigger("2026-11-01", "01:30", first), ny_trigger("2026-11-01", "01:30", second)
    assert a.due and b.due and a.execution_key == b.execution_key == "2026-11-01"


def test_dst_the_local_day_still_ends_at_local_midnight_on_a_23_or_25_hour_day():
    assert ny_trigger("2026-03-08", "00:00", datetime(2026, 3, 9, 3, 59, tzinfo=timezone.utc)).due, "23:59 EDT"
    assert ny_trigger("2026-03-08", "00:00", datetime(2026, 3, 9, 4, 0, tzinfo=timezone.utc)).reason == Reason.WINDOW_PASSED
    assert ny_trigger("2026-11-01", "00:00", datetime(2026, 11, 2, 4, 59, tzinfo=timezone.utc)).due, "23:59 EST, a 25-hour day"
    assert ny_trigger("2026-11-01", "00:00", datetime(2026, 11, 2, 5, 0, tzinfo=timezone.utc)).reason == Reason.WINDOW_PASSED


def test_the_execution_reference_is_the_same_whichever_zone_and_moment_of_the_window_now_is_written_in():
    keys = {
        trigger(now).execution_key
        for now in (at(24, 10), at(24, 10).astimezone(timezone.utc), at(24, 10).astimezone(NEW_YORK), at(24, 23, 59, 59), at(24, 10, 0, 1))
    }
    assert keys == {"2026-09-24"}


def test_the_very_last_microsecond_of_the_day_is_still_that_days_execution():
    late = datetime(2026, 9, 24, 23, 59, 59, 999999, tzinfo=IST)

    assert trigger(late).due and trigger(late + timedelta(microseconds=1)).reason == Reason.WINDOW_PASSED


@pytest.mark.parametrize("day, offset", [("9999-12-31", 0), ("9999-12-30", 1), ("0001-01-01", 0), ("0001-01-02", -1)])
def test_dates_at_the_edge_of_the_calendar_are_reported_not_crashed_on(day, offset):
    for tz_name in ("America/New_York", "Asia/Kolkata", "UTC"):
        config = {"reference_field": "d", "offset_days": offset, "time": "23:00", "timezone": tz_name}
        result = evaluate_trigger("date_offset", config, {"d": day}, DUE)

        assert result.due is False and result.reason in (Reason.REFERENCE_DATE_INVALID, Reason.NOT_YET_DUE, Reason.WINDOW_PASSED)


@pytest.mark.parametrize("phone", ["+919876543210\n", "\n+919876543210", "+919876543210\r\n", "+91\n9876543210", "+919876543210\x00"])
def test_a_phone_with_stray_control_characters_is_refused_not_quietly_cleaned(phone):
    """The outbound API refuses these, so the eligibility check must too: 'eligible' must never be
    followed by a job the service then rejects."""
    for channel in ("phone", "web"):
        result = eligible(ct(phone=phone), channel=channel)

        assert (result.eligible, result.reason) == (False, Reason.PHONE_INVALID), (repr(phone), channel)


# === review hardening: idempotency when the pre-check does not help ==============================


def execution_reference(world) -> str:
    return workflow_reference(world.acme_workflow, world.acme_contact, "2026-09-24")


def blind_once(world, monkeypatch):
    """The first reference lookup finds nothing, as it would for the loser of a race: the other
    evaluation has not committed yet. Every later lookup is real."""
    real, seen = world.repo.get_job_by_reference, []

    def lookup(reference):
        seen.append(reference)
        return None if len(seen) == 1 else real(reference)

    monkeypatch.setattr(world.repo, "get_job_by_reference", lookup)


async def test_the_database_is_the_final_guard_when_the_precheck_misses_a_committed_job(world, monkeypatch):
    first = await world.run()
    blind_once(world, monkeypatch)
    loser = await world.run(at(24, 10, 5))

    assert (loser.call_job_created, loser.reason, loser.job_id) == (False, Reason.ALREADY_SCHEDULED, first.job_id)
    assert len(world.jobs()) == 1
    assert len(world.calls) == 2, "the job path WAS asked twice: it was the unique reference that stopped the second"


async def test_losing_the_race_to_a_job_made_from_a_different_workflow_edit_is_recovered_not_raised(world, monkeypatch):
    """create_job answers 'same reference, different content' with Conflict. In a race that just means
    the other evaluation saw the workflow before it was edited: the execution already has its job."""
    first = await world.run()
    world.change(Workflow, world.acme_workflow, action_config={**ACTION, "reason": "edited in between"})
    blind_once(world, monkeypatch)
    loser = await world.run(at(24, 10, 5))

    assert (loser.call_job_created, loser.reason, loser.job_id) == (False, Reason.ALREADY_SCHEDULED, first.job_id)
    assert len(world.jobs()) == 1


async def test_a_job_that_merely_holds_the_reference_is_not_mistaken_for_the_workflows_own(world):
    """Anyone with the business API key chooses references. If one takes 'wf:1:contact:1:2026-09-24' for an
    unrelated call, the workflow must not report that call as its own scheduled job, or silently skip."""
    squatter = await world.env.job(reference=execution_reference(world), reason="something else entirely")
    outcome = await world.run()

    assert (outcome.due, outcome.eligible, outcome.call_job_created, outcome.reason) == (True, True, False, Reason.REFERENCE_CONFLICT)
    assert outcome.job_id is None, "another job's id is not handed out"
    assert [job["id"] for job in world.jobs()] == [squatter["job_id"]]
    assert [c for c in world.calls if c[1] is False] == [], "the engine did not even try to create a job"


async def test_the_same_squatter_is_caught_when_the_precheck_misses_it_too(world, monkeypatch):
    await world.env.job(reference=execution_reference(world), reason="something else entirely")
    blind_once(world, monkeypatch)
    outcome = await world.run()

    assert (outcome.call_job_created, outcome.reason, outcome.job_id) == (False, Reason.REFERENCE_CONFLICT, None)
    assert len(world.jobs()) == 1


async def test_a_constraint_violation_that_is_not_a_duplicate_execution_propagates(world, monkeypatch):
    def broken(_values):
        raise IntegrityError("INSERT", {}, Exception("some other constraint"))

    monkeypatch.setattr(world.repo, "create_job", broken)

    with pytest.raises(IntegrityError):
        await world.run()
