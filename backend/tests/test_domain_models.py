import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import JobRow, ResultRow, UserRow
from app.models import Agent, Contact, Organization, Workflow
from tests.helpers import migrate


@pytest.fixture
def db(tmp_path):
    """A database built by the real migrations, with foreign keys enforced as PostgreSQL does."""
    url = f"sqlite:///{tmp_path / 'domain.db'}"
    migrate(url)
    engine = create_engine(url)
    event.listen(engine, "connect", lambda conn, _: conn.execute("PRAGMA foreign_keys=ON"))

    with Session(engine) as session:
        yield session

    engine.dispose()


def org(db: Session, name: str = "Acme Health", industry: str | None = "healthcare") -> Organization:
    organization = Organization(name=name, industry=industry)
    db.add(organization)
    db.commit()
    return organization


def agent(db: Session, organization: Organization, name: str = "Follow-up Assistant", **fields) -> Agent:
    row = Agent(organization_id=organization.id, name=name, **fields)
    db.add(row)
    db.commit()
    return row


def user(email: str, **fields) -> UserRow:
    return UserRow(email=email, password_hash="x", role="operator", disabled=0, created_at=1.0, **fields)


# --- organization ----------------------------------------------------------------------------


def test_an_organization_gets_defaults_from_the_database(db):
    acme = org(db)

    assert acme.id is not None
    assert (acme.name, acme.industry, acme.status) == ("Acme Health", "healthcare", "active")
    assert acme.created_at is not None and acme.updated_at is not None


def test_an_organization_status_is_limited_to_known_values(db):
    db.add(Organization(name="X", status="deleted"))

    with pytest.raises(IntegrityError):
        db.commit()


# --- users -----------------------------------------------------------------------------------


def test_users_belong_to_an_organization_and_old_users_need_none(db):
    acme = org(db)
    db.add_all([user("a@acme.test", organization_id=acme.id), user("legacy@old.test")])
    db.commit()

    assert [u.email for u in acme.users] == ["a@acme.test"]
    assert db.scalar(select(UserRow).where(UserRow.email == "a@acme.test")).organization is acme
    assert db.scalar(select(UserRow).where(UserRow.email == "legacy@old.test")).organization is None


def test_a_user_cannot_belong_to_an_organization_that_does_not_exist(db):
    db.add(user("a@x.test", organization_id=999))

    with pytest.raises(IntegrityError):
        db.commit()


# --- agents ----------------------------------------------------------------------------------


def test_an_agent_belongs_to_an_organization_and_stores_flexible_config(db):
    acme = org(db)
    row = agent(
        db,
        acme,
        role="Patient Support Assistant",
        purpose="Remind patients about appointments",
        target_users=["Patients"],
        primary_tasks=["Schedule appointments", "Provide information"],
        behavior_config={"tone": ["Professional", "Friendly"], "rules": {"never_diagnose": True}},
        instructions={"domain_context": "Monthly follow-ups", "additional_instructions": "Confirm identity first"},
    )
    db.expire_all()

    assert row.organization is acme and acme.agents == [row]
    assert (row.status, row.language, row.voice) == ("draft", "en", None)
    assert row.target_users == ["Patients"]
    assert row.behavior_config["rules"] == {"never_diagnose": True}
    assert row.instructions["domain_context"] == "Monthly follow-ups"


def test_one_agent_shape_serves_any_domain(db):
    acme, shield = org(db), org(db, "Shield Insurance", "insurance")
    care = agent(db, acme, "Patient Follow-up Assistant", industry="healthcare")
    dues = agent(db, shield, "Policy Payment Reminder", industry="insurance", primary_tasks=["Remind about premiums"])

    assert {care.industry, dues.industry} == {"healthcare", "insurance"}
    assert {a.__class__ for a in (care, dues)} == {Agent}


def test_agent_names_are_unique_within_an_organization_only(db):
    acme, shield = org(db), org(db, "Shield Insurance")
    agent(db, acme, "Assistant")
    agent(db, shield, "Assistant")  # another tenant may reuse the name

    db.add(Agent(organization_id=acme.id, name="Assistant"))

    with pytest.raises(IntegrityError):
        db.commit()


def test_an_agent_status_is_limited_to_known_values(db):
    db.add(Agent(organization_id=org(db).id, name="A", status="deleted"))

    with pytest.raises(IntegrityError):
        db.commit()


# --- contacts --------------------------------------------------------------------------------


def test_a_contact_belongs_to_an_organization_and_keeps_domain_data_in_metadata(db):
    acme, shield = org(db), org(db, "Shield Insurance", "insurance")
    patient = Contact(
        organization_id=acme.id,
        name="Priya Sharma",
        phone="+919876543210",
        metadata_={"doctor": "Dr. Sharma", "appointment_type": "Monthly Follow-up", "appointment_date": "2026-09-25"},
        preferred_contact_time={"start": "10:00", "end": "18:00", "timezone": "Asia/Kolkata"},
    )
    policyholder = Contact(organization_id=shield.id, name="Arun Rao", metadata_={"policy_number": "POL123"})
    db.add_all([patient, policyholder])
    db.commit()
    db.expire_all()

    assert patient.organization is acme and acme.contacts == [patient]
    assert patient.metadata_["doctor"] == "Dr. Sharma"
    assert patient.preferred_contact_time["timezone"] == "Asia/Kolkata"
    assert policyholder.metadata_ == {"policy_number": "POL123"}
    assert (patient.consent_status, patient.status) == ("unknown", "active")
    assert "doctor_name" not in Contact.__table__.columns, "the core table stays domain-agnostic"


def test_contact_metadata_is_stored_in_a_column_called_metadata(db):
    assert Contact.__table__.columns["metadata"].key == "metadata"
    assert Contact.metadata_.property.columns[0].name == "metadata"


def test_a_contact_consent_status_is_limited_to_known_values(db):
    db.add(Contact(organization_id=org(db).id, name="P", consent_status="maybe"))

    with pytest.raises(IntegrityError):
        db.commit()


# --- workflows -------------------------------------------------------------------------------


def make_workflow(db, organization, assistant, name="Appointment Reminder", **fields) -> Workflow:
    row = Workflow(
        organization_id=organization.id,
        agent_id=assistant.id,
        name=name,
        trigger_type=fields.pop("trigger_type", "schedule"),
        **fields,
    )
    db.add(row)
    db.commit()
    return row


def test_a_workflow_belongs_to_an_agent_and_an_organization(db):
    acme = org(db)
    assistant = agent(db, acme)
    reminder = make_workflow(
        db,
        acme,
        assistant,
        trigger_config={"offset": "-1 day", "relative_to": "appointment.date"},
        conditions=[{"field": "appointment.status", "op": "eq", "value": "scheduled"}],
        action_config={"type": "phone_call"},
        retry_policy={"max_attempts": 3, "window": {"start": "10:00", "end": "18:00"}},
    )
    db.expire_all()

    assert reminder.agent is assistant and assistant.workflows == [reminder]
    assert reminder.organization is acme and acme.workflows == [reminder]
    assert reminder.conditions == [{"field": "appointment.status", "op": "eq", "value": "scheduled"}]
    assert reminder.retry_policy["window"]["end"] == "18:00"
    assert (reminder.status, reminder.trigger_type) == ("draft", "schedule")


def test_a_workflow_cannot_use_another_organizations_agent(db):
    acme, shield = org(db), org(db, "Shield Insurance")
    shield_agent = agent(db, shield, "Shield Assistant")

    db.add(Workflow(organization_id=acme.id, agent_id=shield_agent.id, name="Leak", trigger_type="manual"))

    with pytest.raises(IntegrityError):
        db.commit()


def test_workflow_names_are_unique_within_an_organization_only(db):
    acme, shield = org(db), org(db, "Shield Insurance")
    make_workflow(db, acme, agent(db, acme), "Reminder")
    make_workflow(db, shield, agent(db, shield), "Reminder")

    db.add(Workflow(organization_id=acme.id, agent_id=acme.agents[0].id, name="Reminder", trigger_type="manual"))

    with pytest.raises(IntegrityError):
        db.commit()


# --- existing call tables --------------------------------------------------------------------


def test_call_jobs_and_results_can_belong_to_an_organization_and_old_rows_need_none(db):
    acme = org(db)
    job = dict(
        profile_id="bank", callee_name="P", callee_phone="+911", reason="r", status="completed",
        answer_token="t", created_at=1.0, expires_at=2.0, max_duration_seconds=300,
    )
    db.add_all([
        JobRow(id="JOB-1", organization_id=acme.id, **job),
        JobRow(id="JOB-OLD", **job),
        ResultRow(call_id="c1", profile_id="bank", started_at=1.0, outcome="completed", payload={}, organization_id=acme.id),
    ])
    db.commit()

    assert db.get(JobRow, "JOB-1").organization_id == acme.id
    assert db.get(JobRow, "JOB-OLD").organization_id is None
    assert db.get(ResultRow, "c1").organization_id == acme.id


def test_a_call_job_cannot_belong_to_an_organization_that_does_not_exist(db):
    db.add(JobRow(
        id="JOB-X", organization_id=999, profile_id="bank", callee_name="P", callee_phone="+911", reason="r",
        status="ringing", answer_token="t", created_at=1.0, expires_at=2.0, max_duration_seconds=300,
    ))

    with pytest.raises(IntegrityError):
        db.commit()
