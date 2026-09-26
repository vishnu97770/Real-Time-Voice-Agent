import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.models import Agent, Contact, Organization, Workflow
from app.schemas import (
    AgentCreate,
    AgentResponse,
    AgentUpdate,
    ContactCreate,
    ContactResponse,
    ContactUpdate,
    OrganizationCreate,
    OrganizationResponse,
    WorkflowCreate,
    WorkflowResponse,
    WorkflowUpdate,
)


def test_create_schemas_apply_defaults_and_trim_whitespace():
    org = OrganizationCreate(name="  Acme  ")
    agent = AgentCreate(name="Assistant")
    contact = ContactCreate(name="Priya")
    workflow = WorkflowCreate(agent_id=1, name="Reminder", trigger_type="schedule")

    assert org.name == "Acme" and org.industry is None
    assert (agent.language, agent.status, agent.target_users, agent.instructions) == ("en", "draft", [], {})
    assert (contact.consent_status, contact.status, contact.metadata) == ("unknown", "active", {})
    assert (workflow.status, workflow.conditions, workflow.retry_policy) == ("draft", [], {})


@pytest.mark.parametrize("schema, body", [
    (AgentCreate, {"name": "A", "organization_id": 2}),
    (ContactCreate, {"name": "P", "organization_id": 2}),
    (WorkflowCreate, {"agent_id": 1, "name": "W", "trigger_type": "manual", "organization_id": 2}),
    (OrganizationCreate, {"name": "O", "status": "suspended"}),
    (AgentUpdate, {"organization_id": 2}),
])
def test_clients_cannot_choose_the_organization_or_other_server_owned_fields(schema, body):
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        schema(**body)


@pytest.mark.parametrize("schema, body", [
    (AgentCreate, {"name": ""}),
    (AgentCreate, {"name": "A", "status": "deleted"}),
    (AgentCreate, {"name": "A", "target_users": ["x"] * 21}),
    (ContactCreate, {"name": "P", "consent_status": "maybe"}),
    (WorkflowCreate, {"agent_id": 1, "name": "W", "trigger_type": "Not A Type"}),
    (WorkflowCreate, {"agent_id": 1, "name": "W", "trigger_type": "manual", "conditions": ["not-a-dict"]}),
])
def test_invalid_input_is_rejected(schema, body):
    with pytest.raises(ValidationError):
        schema(**body)


def test_updates_are_partial_and_cannot_null_a_required_field():
    change = AgentUpdate(purpose="New purpose")

    assert change.model_dump(exclude_unset=True) == {"purpose": "New purpose"}
    assert AgentUpdate(role=None).model_dump(exclude_unset=True) == {"role": None}, "a nullable field can be cleared"

    for schema, field in ((AgentUpdate, "name"), (ContactUpdate, "metadata"), (WorkflowUpdate, "conditions")):
        with pytest.raises(ValidationError, match="cannot be null"):
            schema(**{field: None})


def test_responses_are_built_from_orm_objects():
    org = Organization(id=1, name="Acme", industry=None, status="active", created_at=_now(), updated_at=_now())
    agent = Agent(
        id=2, organization_id=1, name="A", role=None, industry=None, purpose=None, target_users=["Patients"],
        primary_tasks=[], behavior_config={}, instructions={}, language="en", voice=None, status="draft",
        created_at=_now(), updated_at=_now(),
    )
    workflow = Workflow(
        id=3, organization_id=1, agent_id=2, name="W", trigger_type="manual", trigger_config={}, conditions=[],
        action_config={}, retry_policy={}, status="draft", created_at=_now(), updated_at=_now(),
    )

    assert OrganizationResponse.model_validate(org).status == "active"
    assert AgentResponse.model_validate(agent).target_users == ["Patients"]
    assert WorkflowResponse.model_validate(workflow).agent_id == 2


def test_contact_metadata_is_read_from_metadata_and_served_as_metadata():
    contact = Contact(
        id=4, organization_id=1, name="Priya", phone=None, email=None, metadata_={"doctor": "Dr. Sharma"},
        consent_status="granted", preferred_language=None, preferred_contact_time=None, status="active",
        created_at=_now(), updated_at=_now(),
    )
    app = FastAPI()

    @app.get("/contact", response_model=ContactResponse)
    def get_contact() -> Contact:
        return contact

    body = TestClient(app).get("/contact").json()  # FastAPI serializes by alias

    assert body["metadata"] == {"doctor": "Dr. Sharma"}
    assert "metadata_" not in body
    assert ContactResponse.model_validate(body).metadata == {"doctor": "Dr. Sharma"}, "and it reads back from JSON"


def _now():
    from datetime import datetime, timezone

    return datetime.now(timezone.utc)
