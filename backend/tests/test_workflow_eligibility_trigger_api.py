"""Step 18E: POST /api/workflows/{workflow_id}/contacts/{contact_id}/eligibility and
.../trigger - the operator-facing surface of the existing WorkflowEngine. Both reuse the engine:
eligibility is purely the judgment (nothing created), trigger is the exact run the scheduler makes
(WorkflowEngine.run_for_contact) with the call job recorded, not dialled.

A refusal is a normal 200 result with a reason (the engine's own convention - see
app/workflows/results.py), and a refusal that is not due must never create a job."""

from datetime import datetime, timezone

from tests.test_agents_api import AGENT_PAYLOAD, OrgWorld
from tests.test_auth import World


def due_now_workflow(agent_id, **fields):
    """A date_offset workflow that is due at the moment the test runs: the contact's reference
    date is today, offset 0, due from 00:00 UTC. `now` is the server's own clock, so every test
    builds its trigger around today's date rather than a fixed fixture date."""
    return {
        "agent_id": agent_id,
        "name": "Today Reminder",
        "trigger_type": "date_offset",
        "trigger_config": {
            "reference_field": "appointment_date",
            "offset_days": 0,
            "time": "00:00",
            "timezone": "UTC",
        },
        "conditions": [],
        "action_config": {"profile_id": "bank", "reason": "your appointment today", "channel": "web"},
        "retry_policy": {},
        "status": "active",
        **fields,
    }


GIVEN_CONTACT = {
    "name": "Priya Sharma",
    "phone": "+919876543210",
    "consent_status": "granted",
    "status": "active",
}

TODAY = datetime.now(timezone.utc).date().isoformat()


def GIVEN_CONTACT_WITH_DATE():
    """An eligible contact whose metadata carries the reference date the due_now_workflow reads
    (today, so the trigger is due the moment the test runs)."""
    return {**GIVEN_CONTACT, "metadata": {"appointment_date": TODAY}}


async def seeded(world, *, workflow=None, contact=None):
    """An agent, a (durable) workflow and an eligible contact, all through the API."""
    agent_id = None

    async with world.client() as client:
        await world.login(client)
        agent_id = (await client.post("/api/agents", json=AGENT_PAYLOAD)).json()["id"]
        workflow_id = (
            await client.post("/api/workflows", json=workflow or due_now_workflow(agent_id))
        ).json()["id"]
        contact_id = (await client.post("/api/contacts", json=contact if contact is not None else GIVEN_CONTACT_WITH_DATE())).json()["id"]

    return agent_id, workflow_id, contact_id


def call(client, path):
    return client.post(path)


# --- eligibility: the pure judgment -------------------------------------------------------------


async def test_eligibility_reports_an_eligible_due_contact():
    world = OrgWorld()
    _, workflow_id, contact_id = await seeded(world)

    async with world.client() as client:
        await world.login(client)
        response = await call(client, f"/api/workflows/{workflow_id}/contacts/{contact_id}/eligibility")

    assert response.status_code == 200
    body = response.json()
    assert body["workflow_id"] == workflow_id and body["contact_id"] == contact_id
    assert body["due"] is True and body["eligible"] is True
    assert body["reason"] == "workflow_due"
    assert body["execution_key"]
    assert body["due_at"]


async def test_eligibility_notes_which_judgment_turned_it_down():
    """The engine's first failure is the answer - the test pins the ones an operator can read."""
    world = OrgWorld()

    # Revoked consent is a hard no even while the workflow is due.
    revoked = {**GIVEN_CONTACT_WITH_DATE(), "consent_status": "revoked"}
    _, workflow_id, contact_id = await seeded(world, contact=revoked)

    async with world.client() as client:
        await world.login(client)
        body = (await call(client, f"/api/workflows/{workflow_id}/contacts/{contact_id}/eligibility")).json()

    assert body["due"] is True and body["eligible"] is False
    assert body["reason"] == "opted_out"

    # An inactive workflow is never due for anyone. Needs a real agent of this organization:
    # the service refuses to even create a workflow on a foreign or non-existent agent.
    agent_id = None

    async with world.client() as client:
        await world.login(client)
        agent_id = (await client.post("/api/agents", json={**AGENT_PAYLOAD, "name": "Second Assistant"})).json()["id"]
        draft_id = (
            await client.post(
                "/api/workflows",
                json={**due_now_workflow(agent_id), "name": "Draft Reminder", "status": "draft"},
            )
        ).json()["id"]
        body = (await call(client, f"/api/workflows/{draft_id}/contacts/{contact_id}/eligibility")).json()

    assert body["due"] is False and body["eligible"] is False
    assert body["reason"] == "workflow_inactive"


async def test_eligibility_never_creates_anything():
    world = OrgWorld()
    _, workflow_id, contact_id = await seeded(world)

    async with world.client() as client:
        await world.login(client)
        await call(client, f"/api/workflows/{workflow_id}/contacts/{contact_id}/eligibility")

    assert world.repo.list_jobs(10) == []


# --- trigger: the scheduler's run, on demand ------------------------------------------------------


async def test_trigger_of_a_due_eligible_contact_records_a_scheduled_job():
    world = OrgWorld()
    _, workflow_id, contact_id = await seeded(world)

    async with world.client() as client:
        await world.login(client)
        response = await call(client, f"/api/workflows/{workflow_id}/contacts/{contact_id}/trigger")

    assert response.status_code == 200
    body = response.json()
    assert body["due"] is True and body["eligible"] is True
    assert body["call_job_created"] is True
    assert body["reason"] == "call_job_created"
    assert body["job_id"]

    job = world.repo.get_job(body["job_id"])
    assert job is not None and job["status"] == "scheduled"  # recorded, not dialled
    assert (job["organization_id"], job["workflow_id"], job["contact_id"]) == (
        world.org.id, workflow_id, contact_id,
    )

    # The job is found through the business /api/call-jobs surface too, if the API key is set.
    assert job["profile_id"] == "bank"


async def test_trigger_is_idempotent_for_the_same_execution():
    world = OrgWorld()
    _, workflow_id, contact_id = await seeded(world)

    async with world.client() as client:
        await world.login(client)
        first = (await call(client, f"/api/workflows/{workflow_id}/contacts/{contact_id}/trigger")).json()
        second = (await call(client, f"/api/workflows/{workflow_id}/contacts/{contact_id}/trigger")).json()

    assert first["call_job_created"] is True
    assert second["reason"] == "already_scheduled"
    assert second["call_job_created"] is False
    assert second["job_id"] == first["job_id"]
    assert len(world.repo.list_jobs(10)) == 1  # the unique reference let only one job exist


async def test_trigger_refuses_an_ineligible_contact_without_creating_a_job():
    world = OrgWorld()
    revoked = {**GIVEN_CONTACT_WITH_DATE(), "consent_status": "revoked"}
    _, workflow_id, contact_id = await seeded(world, contact=revoked)

    async with world.client() as client:
        await world.login(client)
        body = (await call(client, f"/api/workflows/{workflow_id}/contacts/{contact_id}/trigger")).json()

    assert body["call_job_created"] is False
    assert body["reason"] == "opted_out"
    assert world.repo.list_jobs(10) == []


# --- tenant isolation and unknown ids ----------------------------------------------------------------


async def test_eligibility_and_trigger_cannot_see_another_organizations_workflow_or_contact():
    world = OrgWorld()
    _, workflow_id, contact_id = await seeded(world)

    async with world.client() as other_client:
        await world.login(other_client, email="other@example.com")

        for endpoint in ("eligibility", "trigger"):
            on_wf = await other_client.post(f"/api/workflows/{workflow_id}/contacts/{contact_id}/{endpoint}")
            assert on_wf.status_code == 404, endpoint

    # A workflow of the other organization with one of ours is refused the same way ("Unknown
    # workflow" first, exact same 404 for both, so the boundary leaks nothing).
    async with world.client() as other_client:
        await world.login(other_client, email="other@example.com")
        other_agent_id = (await other_client.post("/api/agents", json=AGENT_PAYLOAD)).json()["id"]
        other_wf_id = (
            await other_client.post("/api/workflows", json=due_now_workflow(other_agent_id, name="Other"))
        ).json()["id"]

    async with world.client() as client:
        await world.login(client)
        mixed = await call(client, f"/api/workflows/{other_wf_id}/contacts/{contact_id}/eligibility")
        assert mixed.status_code == 404


async def test_unknown_workflow_or_contact_is_404():
    world = OrgWorld()
    _, workflow_id, contact_id = await seeded(world)

    async with world.client() as client:
        await world.login(client)

        for endpoint in ("eligibility", "trigger"):
            assert (await call(client, f"/api/workflows/999999/contacts/{contact_id}/{endpoint}")).status_code == 404
            assert (await call(client, f"/api/workflows/{workflow_id}/contacts/999999/{endpoint}")).status_code == 404


async def test_eligibility_and_trigger_require_sign_in():
    world = OrgWorld()
    _, workflow_id, contact_id = await seeded(world)

    async with world.client() as client:
        for endpoint in ("eligibility", "trigger"):
            assert (await call(client, f"/api/workflows/{workflow_id}/contacts/{contact_id}/{endpoint}")).status_code == 401


# --- a contact whose reference date has passed is never called ---------------------------------------


async def test_a_window_that_has_passed_is_reported_not_called():
    world = OrgWorld()
    past = {**GIVEN_CONTACT, "metadata": {"appointment_date": "2020-01-01"}}
    _, workflow_id, contact_id = await seeded(world, contact=past)

    async with world.client() as client:
        await world.login(client)
        eligible = (await call(client, f"/api/workflows/{workflow_id}/contacts/{contact_id}/eligibility")).json()
        outcome = (await call(client, f"/api/workflows/{workflow_id}/contacts/{contact_id}/trigger")).json()

    assert eligible["eligible"] is False
    assert eligible["reason"] == "window_passed"
    assert outcome["call_job_created"] is False


# --- dev-mode anonymity (AUTH_REQUIRED=false) ----------------------------------------------------------


async def test_the_anonymous_dev_mode_operator_sees_no_workflows_and_cannot_run_them():
    world = OrgWorld(auth_required=False)

    async with world.client() as client:
        assert (await client.get("/api/workflows")).json() == []
        # There is nothing to run: the anonymous operator's organization is None.
        assert (await client.post("/api/workflows/1/contacts/1/trigger")).status_code == 404