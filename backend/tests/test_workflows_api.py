"""Step 18D: GET/POST/PUT/DELETE /api/workflows[/{id}] - authenticated, scoped to the signed-in
operator's own organization, and never letting one organization see or touch another's workflows.

The routes and service methods this tests already existed at the start of Step 18D (repository,
service and schemas were in place); this file pins the whole CRUD surface - including the guard
that keeps a workflow tied to an agent of its own organization."""

from tests.test_agents_api import AGENT_PAYLOAD, OrgWorld
from tests.test_auth import World

from tests.test_agents_api import OrgWorld as _OrgWorld  # noqa: F401  (re-exported for readers)


def workflow_payload(agent_id, **fields):
    return {
        "agent_id": agent_id,
        "name": "Appointment Reminder",
        "trigger_type": "date_offset",
        "trigger_config": {
            "reference_field": "appointment_date",
            "offset_days": -1,
            "time": "10:00",
            "timezone": "Asia/Kolkata",
        },
        "conditions": [],
        "action_config": {"profile_id": "bank", "reason": "your appointment tomorrow"},
        "retry_policy": {},
        "status": "active",
        **fields,
    }


async def created_agent(client) -> int:
    """An agent of the signed-in operator's organization, created through the API."""
    response = await client.post("/api/agents", json=AGENT_PAYLOAD)
    assert response.status_code == 201
    return response.json()["id"]


# --- listing, creation, retrieval, update ----------------------------------------------------------


async def test_workflow_listing_starts_empty_and_reflects_a_created_workflow():
    world = OrgWorld()

    async with world.client() as client:
        await world.login(client)
        assert (await client.get("/api/workflows")).json() == []

        agent_id = await created_agent(client)
        created = await client.post("/api/workflows", json=workflow_payload(agent_id))
        assert created.status_code == 201
        body = created.json()
        assert body["name"] == "Appointment Reminder"
        assert body["agent_id"] == agent_id
        assert body["organization_id"] == world.org.id
        assert body["status"] == "active"
        assert body["trigger_type"] == "date_offset"
        assert body["trigger_config"]["offset_days"] == -1

        listed = (await client.get("/api/workflows")).json()
        assert [w["id"] for w in listed] == [body["id"]]


async def test_a_workflow_can_be_fetched_by_id_and_updated():
    world = OrgWorld()

    async with world.client() as client:
        await world.login(client)
        agent_id = await created_agent(client)
        created = (await client.post("/api/workflows", json=workflow_payload(agent_id))).json()
        workflow_id = created["id"]

        fetched = await client.get(f"/api/workflows/{workflow_id}")
        assert fetched.status_code == 200 and fetched.json()["name"] == "Appointment Reminder"

        updated = await client.put(f"/api/workflows/{workflow_id}", json={"name": "Reminder v2"})
        assert updated.status_code == 200
        body = updated.json()
        assert body["name"] == "Reminder v2"
        # Untouched fields survive a partial update.
        assert body["agent_id"] == agent_id and body["status"] == "active"

        refetched = await client.get(f"/api/workflows/{workflow_id}")
        assert refetched.json()["name"] == "Reminder v2"


async def test_getting_an_unknown_workflow_is_404():
    world = OrgWorld()

    async with world.client() as client:
        await world.login(client)
        assert (await client.get("/api/workflows/999999")).status_code == 404


async def test_an_operator_with_no_organization_sees_an_empty_list_and_a_clean_conflict_on_create():
    world = World()  # the plain World: its one user has no organization

    async with world.client() as client:
        await world.login(client)
        assert (await client.get("/api/workflows")).json() == []

        response = await client.post(
            "/api/workflows", json=workflow_payload(1)
        )  # agent_id is never reached: the org check comes first
        assert response.status_code == 409


# --- delete ----------------------------------------------------------------------------------------


async def test_a_workflow_can_be_deleted():
    world = OrgWorld()

    async with world.client() as client:
        await world.login(client)
        agent_id = await created_agent(client)
        workflow_id = (await client.post("/api/workflows", json=workflow_payload(agent_id))).json()["id"]

        deleted = await client.delete(f"/api/workflows/{workflow_id}")
        assert deleted.status_code == 204

        assert (await client.get(f"/api/workflows/{workflow_id}")).status_code == 404
        assert (await client.get("/api/workflows")).json() == []


async def test_deleting_an_unknown_workflow_is_404():
    world = OrgWorld()

    async with world.client() as client:
        await world.login(client)
        assert (await client.delete("/api/workflows/999999")).status_code == 404


async def test_deleting_a_workflow_with_a_call_job_is_a_clean_conflict_not_a_crash():
    """Mirror of the contacts counterpart: a call job referencing the workflow (the workflow
    engine's own `wf:` jobs link it) must refuse deletion with a 409, not a 500."""
    world = OrgWorld()

    async with world.client() as client:
        await world.login(client)
        agent_id = await created_agent(client)
        workflow_id = (await client.post("/api/workflows", json=workflow_payload(agent_id))).json()["id"]
        contact_id = (
            await client.post("/api/contacts", json={
                "name": "Priya Sharma", "phone": "+919876543210", "consent_status": "granted",
            })
        ).json()["id"]

    # A call job references the workflow directly through the repository.
    world.repo.create_job({
        "id": "JOB-WORKFLOW1", "reference": "wf:1:contact:1:2026-09-24", "profile_id": "bank",
        "customer_ref": None, "organization_id": world.org.id, "agent_id": agent_id,
        "contact_id": contact_id, "workflow_id": workflow_id, "callee_name": "Priya Sharma",
        "channel": "phone", "callee_phone": "+919876543210", "reason": "x", "callback_url": None,
        "status": "scheduled", "answer_token": "t", "created_at": 0.0, "expires_at": 100.0,
        "max_duration_seconds": 300, "callback_status": "none", "callback_attempts": 0,
    })

    async with world.client() as client:
        await world.login(client)
        response = await client.delete(f"/api/workflows/{workflow_id}")
        assert response.status_code == 409

        # And the workflow is still there: the failed delete did not half-apply.
        assert (await client.get(f"/api/workflows/{workflow_id}")).status_code == 200


# --- tenant isolation --------------------------------------------------------------------------------


async def test_tenant_isolation_one_organization_cannot_reach_anothers_workflow():
    world = OrgWorld()

    async with world.client() as client:
        await world.login(client)
        agent_id = await created_agent(client)
        workflow_id = (await client.post("/api/workflows", json=workflow_payload(agent_id))).json()["id"]

    async with world.client() as other_client:
        await world.login(other_client, email="other@example.com")

        assert (await other_client.get(f"/api/workflows/{workflow_id}")).status_code == 404
        assert (await other_client.get("/api/workflows")).json() == []
        assert (await other_client.put(f"/api/workflows/{workflow_id}", json={"name": "hijacked"})).status_code == 404
        assert (await other_client.delete(f"/api/workflows/{workflow_id}")).status_code == 404


async def test_workflow_creation_cannot_assign_another_organizations_id():
    world = OrgWorld()

    async with world.client() as client:
        await world.login(client)
        # organization_id is not a field WorkflowCreate declares at all (Schema forbids unknown
        # fields), so the attempt is refused before any service code runs.
        response = await client.post(
            "/api/workflows", json=workflow_payload(1, organization_id=world.other_org.id)
        )
        assert response.status_code == 422

        assert (await client.get("/api/workflows")).json() == []  # nothing was created either


async def test_a_workflow_cannot_use_another_organizations_agent():
    """The database's composite foreign key (agent_id, organization_id) would refuse this too; the
    service checks first so the operator gets a specific, readable reason, not a 500."""
    world = OrgWorld()

    async with world.client() as other_client:
        await world.login(other_client, email="other@example.com")
        foreign_agent_id = await created_agent(other_client)

    async with world.client() as client:
        await world.login(client)
        response = await client.post("/api/workflows", json=workflow_payload(foreign_agent_id))
        assert response.status_code == 422
        assert "Unknown agent" in response.json()["detail"]

        # Updating onto another organization's agent is refused the same way.
        agent_id = await created_agent(client)
        workflow_id = (await client.post("/api/workflows", json=workflow_payload(agent_id))).json()["id"]
        updated = await client.put(f"/api/workflows/{workflow_id}", json={"agent_id": foreign_agent_id})
        assert updated.status_code == 422
        assert "Unknown agent" in updated.json()["detail"]

        # And nothing was changed: the workflow still uses its own organization's agent.
        assert (await client.get(f"/api/workflows/{workflow_id}")).json()["agent_id"] == agent_id


# --- validation and conflict handling ------------------------------------------------------------------


async def test_validation_errors_are_reported_clearly():
    world = OrgWorld()

    async with world.client() as client:
        await world.login(client)
        agent_id = await created_agent(client)

        missing_name = await client.post("/api/workflows", json=workflow_payload(agent_id, name=""))
        assert missing_name.status_code == 422

        bad_trigger = await client.post(
            "/api/workflows", json=workflow_payload(agent_id, trigger_type="not a trigger")
        )
        assert bad_trigger.status_code == 422

        bad_status = await client.post("/api/workflows", json=workflow_payload(agent_id, status="bogus"))
        assert bad_status.status_code == 422


async def test_a_duplicate_workflow_name_within_an_organization_is_a_clean_conflict():
    world = OrgWorld()

    async with world.client() as client:
        await world.login(client)
        agent_id = await created_agent(client)
        assert (await client.post("/api/workflows", json=workflow_payload(agent_id))).status_code == 201
        second = await client.post("/api/workflows", json=workflow_payload(agent_id))
        assert second.status_code == 409


async def test_the_same_workflow_name_in_another_organization_is_fine():
    world = OrgWorld()

    async with world.client() as client:
        await world.login(client)
        agent_id = await created_agent(client)
        assert (await client.post("/api/workflows", json=workflow_payload(agent_id))).status_code == 201

    async with world.client() as other_client:
        await world.login(other_client, email="other@example.com")
        other_agent_id = await created_agent(other_client)
        same_name = await other_client.post(
            "/api/workflows", json=workflow_payload(other_agent_id, name="Appointment Reminder")
        )
        assert same_name.status_code == 201
        assert same_name.json()["organization_id"] == world.other_org.id


# --- unauthenticated access -------------------------------------------------------------------------------


async def test_unauthenticated_workflow_access_is_refused():
    world = OrgWorld()

    async with world.client() as client:
        assert (await client.get("/api/workflows")).status_code == 401
        assert (await client.post("/api/workflows", json=workflow_payload(1))).status_code == 401
        assert (await client.get("/api/workflows/1")).status_code == 401
        assert (await client.put("/api/workflows/1", json={"name": "x"})).status_code == 401
        assert (await client.delete("/api/workflows/1")).status_code == 401