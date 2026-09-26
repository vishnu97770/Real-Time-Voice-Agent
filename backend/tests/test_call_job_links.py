"""Step 3: a call job knows its organization, agent, contact and workflow, and can never point
across organizations. The first half tests the database itself (foreign keys enforced, as
PostgreSQL does); the second half tests the existing outbound API and call flow with those links."""

import pytest
from sqlalchemy import inspect, select
from sqlalchemy.exc import IntegrityError

from app.db import JobRow, Repository, ResultRow
from app.models import Contact, Workflow
from tests.helpers import migrate
from tests.test_domain_models import agent, db, org  # noqa: F401  (db is a fixture)
from tests.test_outbound import KEY, PHONE, Env

JOB = dict(
    profile_id="bank", callee_name="Rahul", callee_phone="+919876543210", reason="your appointment",
    status="ringing", answer_token="t", created_at=1.0, expires_at=2.0, max_duration_seconds=300,
    callback_status="none", callback_attempts=0,
)


def contact(db, organization, name="Rahul", **fields) -> Contact:
    row = Contact(organization_id=organization.id, name=name, phone="+919876543210", **fields)
    db.add(row)
    db.commit()
    return row


def workflow(db, organization, owner, name="Reminder") -> Workflow:
    row = Workflow(organization_id=organization.id, agent_id=owner.id, name=name, trigger_type="manual")
    db.add(row)
    db.commit()
    return row


def job(job_id="JOB-1", **fields) -> JobRow:
    return JobRow(id=job_id, **{**JOB, **fields})


# --- the links ---------------------------------------------------------------------------------


def test_a_call_job_can_reference_its_organization(db):
    acme = org(db)
    db.add(job(organization_id=acme.id))
    db.commit()
    db.expire_all()

    row = db.get(JobRow, "JOB-1")
    assert row.organization is acme and acme.call_jobs == [row]


def test_a_call_job_can_reference_an_agent(db):
    acme = org(db)
    assistant = agent(db, acme)
    db.add(job(organization_id=acme.id, agent_id=assistant.id))
    db.commit()
    db.expire_all()

    row = db.get(JobRow, "JOB-1")
    assert row.agent is assistant and assistant.call_jobs == [row]


def test_a_call_job_can_reference_a_contact(db):
    acme = org(db)
    rahul = contact(db, acme, metadata_={"appointment_date": "2026-09-25", "doctor": "Dr. Sharma"})
    db.add(job(organization_id=acme.id, contact_id=rahul.id))
    db.commit()
    db.expire_all()

    row = db.get(JobRow, "JOB-1")
    assert row.contact is rahul and rahul.call_jobs == [row]
    assert row.contact.metadata_["doctor"] == "Dr. Sharma", "the contact's data is reached through the link"


def test_a_call_job_can_reference_a_workflow(db):
    acme = org(db)
    reminder = workflow(db, acme, agent(db, acme))
    db.add(job(organization_id=acme.id, workflow_id=reminder.id))
    db.commit()
    db.expire_all()

    row = db.get(JobRow, "JOB-1")
    assert row.workflow is reminder and reminder.call_jobs == [row]


def test_one_job_resolves_organization_agent_contact_and_workflow(db):
    acme = org(db)
    assistant = agent(db, acme, "Patient Follow-up Assistant")
    rahul, reminder = contact(db, acme), workflow(db, acme, assistant)
    db.add(job(organization_id=acme.id, agent_id=assistant.id, contact_id=rahul.id, workflow_id=reminder.id))
    db.commit()
    db.expire_all()

    row = db.get(JobRow, "JOB-1")
    assert (row.organization, row.agent, row.contact, row.workflow) == (acme, assistant, rahul, reminder)


def test_a_legacy_call_job_needs_none_of_the_links(db):
    db.add(job("JOB-LEGACY"))
    db.commit()
    db.expire_all()

    row = db.get(JobRow, "JOB-LEGACY")
    assert (row.organization_id, row.agent_id, row.contact_id, row.workflow_id) == (None, None, None, None)
    assert (row.organization, row.agent, row.contact, row.workflow) == (None, None, None, None)


def test_a_call_result_still_belongs_to_its_call_job_and_organization(db):
    acme = org(db)
    assistant = agent(db, acme)
    db.add(job(organization_id=acme.id, agent_id=assistant.id, call_id="call-1"))
    db.add(ResultRow(call_id="call-1", job_id="JOB-1", profile_id="bank", started_at=1.0, outcome="completed",
                     payload={"summary": "ok"}, organization_id=acme.id))
    db.commit()

    result = db.scalar(select(ResultRow).where(ResultRow.job_id == "JOB-1"))
    assert result.call_id == db.get(JobRow, "JOB-1").call_id == "call-1"


# --- tenant integrity --------------------------------------------------------------------------


def test_a_call_job_cannot_use_another_organizations_agent(db):
    acme, shield = org(db), org(db, "Shield Insurance")
    db.add(job(organization_id=acme.id, agent_id=agent(db, shield, "Shield Assistant").id))

    with pytest.raises(IntegrityError, match="FOREIGN KEY"):
        db.commit()


def test_a_call_job_cannot_use_another_organizations_contact(db):
    acme, shield = org(db), org(db, "Shield Insurance")
    db.add(job(organization_id=acme.id, contact_id=contact(db, shield, "Arun").id))

    with pytest.raises(IntegrityError, match="FOREIGN KEY"):
        db.commit()


def test_a_call_job_cannot_use_another_organizations_workflow(db):
    acme, shield = org(db), org(db, "Shield Insurance")
    foreign = workflow(db, shield, agent(db, shield, "Shield Assistant"))
    db.add(job(organization_id=acme.id, workflow_id=foreign.id))

    with pytest.raises(IntegrityError, match="FOREIGN KEY"):
        db.commit()


def test_a_call_job_cannot_link_to_something_that_does_not_exist(db):
    acme = org(db)

    for links in ({"agent_id": 999}, {"contact_id": 999}, {"workflow_id": 999}):
        db.add(job(organization_id=acme.id, **links))

        with pytest.raises(IntegrityError):
            db.commit()

        db.rollback()


@pytest.mark.parametrize("make", ["agent", "contact", "workflow"])
def test_a_link_without_an_organization_is_refused(db, make):
    """A composite foreign key is not checked when one of its columns is NULL. Without the CHECK a
    job could name another tenant's agent simply by leaving organization_id empty."""
    shield = org(db, "Shield Insurance")
    owner = agent(db, shield, "Shield Assistant")
    target = {"agent": owner, "contact": contact(db, shield), "workflow": workflow(db, shield, owner)}[make]
    db.add(job(**{f"{make}_id": target.id}))  # organization_id left NULL

    with pytest.raises(IntegrityError, match="CHECK"):
        db.commit()


# --- the schema --------------------------------------------------------------------------------


def test_the_existing_primary_key_and_indexes_are_untouched_and_the_new_ones_exist(db):
    inspector = inspect(db.get_bind())
    columns = {c["name"]: c for c in inspector.get_columns("call_jobs")}

    assert inspector.get_pk_constraint("call_jobs")["constrained_columns"] == ["id"]
    assert str(columns["id"]["type"]) == "VARCHAR(40)"

    for name in ("organization_id", "agent_id", "contact_id", "workflow_id"):
        assert columns[name]["nullable"], f"{name} must stay optional so legacy jobs remain valid"

    indexes = {i["name"]: i["column_names"] for i in inspector.get_indexes("call_jobs")}
    assert {"ix_call_jobs_reference", "ix_call_jobs_status", "ix_call_jobs_twilio_call_sid"} <= set(indexes)
    for name in ("organization", "agent", "contact", "workflow"):
        assert indexes[f"ix_call_jobs_{name}_id"] == [f"{name}_id"]


# --- the repository ----------------------------------------------------------------------------


@pytest.fixture
def repo(tmp_path):
    """The application's own repository over a migrated file database (no test-side pragma: the
    repository's engine enforces foreign keys itself)."""
    url = f"sqlite:///{tmp_path / 'repo.db'}"
    migrate(url)
    repository = Repository(url)
    yield repository
    repository.engine.dispose()


def seed(repo: Repository) -> dict[str, int]:
    """Two organizations, each with an agent, a contact and a workflow."""
    from sqlalchemy.orm import Session

    ids = {}

    with Session(repo.engine) as db:
        for tag in ("acme", "shield"):
            organization = org(db, tag)
            owner = agent(db, organization, f"{tag} assistant")
            ids.update({
                tag: organization.id,
                f"{tag}_agent": owner.id,
                f"{tag}_contact": contact(db, organization, f"{tag} person").id,
                f"{tag}_workflow": workflow(db, organization, owner).id,
            })

    return ids


def values(job_id="JOB-1", **fields) -> dict:
    return {"id": job_id, **JOB, **fields}


def test_the_repository_stores_a_domain_aware_job_and_a_legacy_one_side_by_side(repo):
    ids = seed(repo)
    linked, _ = repo.create_job(values(
        organization_id=ids["acme"], agent_id=ids["acme_agent"], contact_id=ids["acme_contact"],
        workflow_id=ids["acme_workflow"],
    ))
    legacy, _ = repo.create_job(values("JOB-2"))

    assert (linked["organization_id"], linked["agent_id"]) == (ids["acme"], ids["acme_agent"])
    assert (linked["contact_id"], linked["workflow_id"]) == (ids["acme_contact"], ids["acme_workflow"])
    assert [legacy[k] for k in ("organization_id", "agent_id", "contact_id", "workflow_id")] == [None] * 4
    assert repo.get_job("JOB-1")["workflow_id"] == ids["acme_workflow"]


def test_the_repository_reports_why_a_job_cannot_link(repo):
    ids = seed(repo)
    problem = repo.job_link_problem

    assert problem(ids["acme"], ids["acme_agent"], ids["acme_contact"], ids["acme_workflow"]) is None
    assert problem(ids["acme"], None, None, None) is None
    assert "Unknown organization" in problem(999, None, None, None)
    assert "Unknown agent" in problem(ids["acme"], ids["shield_agent"], None, None)
    assert "Unknown contact" in problem(ids["acme"], None, ids["shield_contact"], None)
    assert "Unknown workflow" in problem(ids["acme"], None, None, ids["shield_workflow"])
    assert problem(ids["acme"], ids["acme_agent"], 999, None) == f"Unknown contact 999 in organization {ids['acme']}"


def test_a_rejected_job_without_a_reference_is_not_mistaken_for_an_existing_one(repo):
    """create_job answers an IntegrityError with 'that reference already exists'. With no reference,
    the lookup would match any legacy job whose reference is also NULL and return it as the result."""
    ids = seed(repo)
    repo.create_job(values("JOB-LEGACY"))  # reference is NULL

    with pytest.raises(IntegrityError):
        repo.create_job(values("JOB-BAD", organization_id=ids["acme"], agent_id=ids["shield_agent"]))

    assert repo.get_job("JOB-BAD") is None


# --- through the outbound API, unchanged call flow ---------------------------------------------


@pytest.fixture
async def linked(repo):
    env = Env(repo=repo)
    yield env, seed(repo)
    await env.client.aclose()


async def test_a_plain_job_is_created_exactly_as_before_with_empty_links(linked):
    env, _ = linked
    created = await env.job()

    assert [created[k] for k in ("organization_id", "agent_id", "contact_id", "workflow_id")] == [None] * 4
    assert created["status"] == "ringing" and "answer_url" in created


async def test_a_domain_aware_job_keeps_its_links_and_runs_a_normal_call_to_a_result(linked):
    env, ids = linked
    links = {
        "organization_id": ids["acme"], "agent_id": ids["acme_agent"],
        "contact_id": ids["acme_contact"], "workflow_id": ids["acme_workflow"],
    }
    created = await env.job(reference="wf-1", **links)

    assert {k: created[k] for k in links} == links
    assert env.repo.get_job(created["job_id"])["contact_id"] == ids["acme_contact"]

    call_id = (await env.answer(created))[0].json()["call_id"]
    await env.say(call_id, "Yes, speaking")
    assert (await env.client.post(f"/api/calls/{call_id}/end")).status_code == 200

    final = await env.status(created["job_id"])
    assert final["status"] == "completed"
    assert {k: final[k] for k in links} == links, "the links survive the whole call"
    assert final["result"]["call_id"] == call_id, "the result is still found through the job"
    assert env.repo.get_result(call_id)["outbound"]["job_id"] == created["job_id"]
    assert env.receiver.bodies[0]["job"]["agent_id"] == ids["acme_agent"], "and reach the callback too"


@pytest.mark.parametrize("link", ["agent", "contact", "workflow"])
async def test_the_api_refuses_another_organizations_agent_contact_or_workflow(linked, link):
    env, ids = linked
    body = {
        "profile_id": "bank", "callee": {"name": "P", "phone": PHONE}, "reason": "x",
        "organization_id": ids["acme"], f"{link}_id": ids[f"shield_{link}"],
    }
    response = await env.client.post("/api/call-jobs", json=body, headers=KEY)

    assert response.status_code == 422
    assert f"Unknown {link}" in response.json()["detail"]
    assert env.repo.list_jobs(10) == [], "nothing was stored"


async def test_the_api_refuses_a_link_without_an_organization_and_an_unknown_organization(linked):
    env, ids = linked
    body = {"profile_id": "bank", "callee": {"name": "P", "phone": PHONE}, "reason": "x"}

    without = await env.client.post("/api/call-jobs", json={**body, "agent_id": ids["acme_agent"]}, headers=KEY)
    unknown = await env.client.post("/api/call-jobs", json={**body, "organization_id": 999}, headers=KEY)

    assert without.status_code == 422 and "organization_id is required" in without.text
    assert unknown.status_code == 422 and "Unknown organization" in unknown.json()["detail"]


async def test_reusing_a_reference_for_another_agent_is_a_conflict_and_the_same_request_is_idempotent(linked):
    env, ids = linked
    body = {
        "reference": "wf-2", "profile_id": "bank", "callee": {"name": "P", "phone": PHONE}, "reason": "x",
        "organization_id": ids["acme"], "agent_id": ids["acme_agent"],
    }
    first = await env.client.post("/api/call-jobs", json=body, headers=KEY)
    again = await env.client.post("/api/call-jobs", json=body, headers=KEY)
    other = await env.client.post("/api/call-jobs", json={**body, "agent_id": None}, headers=KEY)

    assert (first.status_code, again.status_code) == (201, 200)
    assert again.json()["job_id"] == first.json()["job_id"]
    assert other.status_code == 409
