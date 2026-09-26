"""A self-service account must never see another organization's calls.

Agents, contacts and workflows were tenant-scoped from the start. The call endpoints (jobs, results,
call history, the shared customers table) predate organizations, so with open sign-up they had to be
scoped too: an ordinary operator who belongs to an organization is confined to it, and everyone else
(administrators, accounts that predate organizations, business systems using the API key) sees what
they always saw.
"""

from app.security import hash_password
from tests.test_auth import JOB, PASSWORD, World

KEY = {"X-API-Key": "test-key"}
NEW_PASSWORD = "a long enough password"


async def signup(client, email, workspace):
    response = await client.post("/api/auth/signup", json={"email": email, "password": NEW_PASSWORD, "workspace_name": workspace})

    assert response.status_code == 201, response.text

    return response.json()["user"]


def seed_result(world, call_id, organization_id, **extra):
    """A finished call. `organization_id` is how a result is attributed (a job's, or a console call's)."""
    world.repo.save_result(
        {
            "call_id": call_id,
            "profile_id": "bank",
            "profile_name": "Bank",
            "outcome": "completed",
            "direction": "inbound",
            "started_at": "2026-01-01T00:00:00+00:00",
            "duration_seconds": 30,
            "summary": f"summary of {call_id}",
            "transcript": [{"id": 1, "speaker": "Agent", "text": "secret words", "time": "00:00"}],
            "organization_id": organization_id,
            **extra,
        }
    )


def ids(rows, key="call_id"):
    return sorted(row[key] for row in rows)


# --- jobs ---------------------------------------------------------------------------------------------


async def test_a_confined_account_sees_only_its_own_jobs_and_cannot_fetch_anothers_by_id():
    world = World()

    async with world.client() as a, world.client() as b:
        alice = await signup(a, "alice@example.com", "Alice Co")
        bob = await signup(b, "bob@example.com", "Bob Co")

        made_a = (await a.post("/api/call-jobs", json=JOB)).json()
        made_b = (await b.post("/api/call-jobs", json=JOB)).json()

        assert made_a["organization_id"] == alice["organization_id"]  # stamped with their own, though they never said
        assert made_b["organization_id"] == bob["organization_id"]

        assert ids(((await a.get("/api/call-jobs")).json()), "job_id") == [made_a["job_id"]]
        assert ids(((await b.get("/api/call-jobs")).json()), "job_id") == [made_b["job_id"]]

        assert (await a.get(f"/api/call-jobs/{made_a['job_id']}")).status_code == 200

        theirs = await b.get(f"/api/call-jobs/{made_a['job_id']}")
        missing = await b.get("/api/call-jobs/JOB-doesnotexist")

        assert theirs.status_code == 404
        assert theirs.json() == missing.json()  # no way to tell "someone else's" from "not there"


async def test_a_confined_account_cannot_create_jobs_in_or_reach_into_another_organization():
    world = World()

    async with world.client() as a, world.client() as b:
        alice = await signup(a, "alice@example.com", "Alice Co")
        bob = await signup(b, "bob@example.com", "Bob Co")
        bobs_agent = (await b.post("/api/agents", json={"name": "BobBot"})).json()

        # naming another organization is refused outright
        refused = await a.post("/api/call-jobs", json={**JOB, "organization_id": bob["organization_id"]})

        assert refused.status_code == 403

        # naming her own is fine
        assert (await a.post("/api/call-jobs", json={**JOB, "organization_id": alice["organization_id"]})).status_code == 201

        # and using another organization's agent is impossible, because everything is checked against hers
        reach = await a.post("/api/call-jobs", json={**JOB, "agent_id": bobs_agent["id"]})

        assert reach.status_code == 422

        # nothing of Alice's leaked into Bob's list by any of that
        assert (await b.get("/api/call-jobs")).json() == []


# --- results & call history --------------------------------------------------------------------------


async def test_a_confined_account_sees_only_its_own_call_history_and_results():
    world = World()

    async with world.client() as a, world.client() as b:
        alice = await signup(a, "alice@example.com", "Alice Co")
        bob = await signup(b, "bob@example.com", "Bob Co")

        seed_result(world, "CALL-A1", alice["organization_id"])
        seed_result(world, "CALL-B1", bob["organization_id"])
        seed_result(world, "CALL-OLD", None)  # from before organizations existed

        assert ids((await a.get("/api/calls")).json()) == ["CALL-A1"]
        assert ids((await b.get("/api/calls")).json()) == ["CALL-B1"]

        assert (await a.get("/api/calls/CALL-A1/result")).json()["summary"] == "summary of CALL-A1"

        for other in ("CALL-B1", "CALL-OLD", "CALL-nothing"):
            response = await a.get(f"/api/calls/{other}/result")

            assert response.status_code == 404, other
            assert "secret words" not in response.text


async def test_a_job_call_result_belongs_to_the_organization_of_its_job():
    world = World()

    async with world.client() as a, world.client() as b:
        await signup(a, "alice@example.com", "Alice Co")
        await signup(b, "bob@example.com", "Bob Co")

        job = (await a.post("/api/call-jobs", json=JOB)).json()

        # the job finishes and its result is saved the way the service does it: by job id
        seed_result(world, "CALL-JOB", None, direction="outbound", outbound={"job_id": job["job_id"], "callee_name": "P"})

        assert ids((await a.get("/api/calls")).json()) == ["CALL-JOB"]
        assert (await b.get("/api/calls")).json() == []
        assert (await b.get("/api/calls/CALL-JOB/result")).status_code == 404


async def test_a_console_call_belongs_to_the_account_that_made_it():
    world = World()

    async with world.client() as a, world.client() as b:
        await signup(a, "alice@example.com", "Alice Co")
        await signup(b, "bob@example.com", "Bob Co")

        started = await a.post("/api/calls", json={"profile_id": "bank"})

        assert started.status_code == 201

        call_id = started.json()["call_id"]

        assert (await a.post(f"/api/calls/{call_id}/end")).status_code == 200

        assert ids((await a.get("/api/calls")).json()) == [call_id]  # she can find her own call
        assert (await a.get(f"/api/calls/{call_id}/result")).status_code == 200
        assert (await b.get("/api/calls")).json() == []
        assert (await b.get(f"/api/calls/{call_id}/result")).status_code == 404


# --- the shared customers table -----------------------------------------------------------------------


async def test_a_confined_account_gets_none_of_the_shared_customer_records():
    world = World()
    world.repo.upsert_customer("bank", "demo", "Demo Customer", {"customer": "demo"})

    async with world.client() as legacy, world.client() as a:
        await world.login(legacy)  # the CLI-made operator, who belongs to no organization
        await signup(a, "alice@example.com", "Alice Co")

        assert [c["ref"] for c in (await legacy.get("/api/customers", params={"profile_id": "bank"})).json()] == ["demo"]
        assert (await a.get("/api/customers", params={"profile_id": "bank"})).json() == []

        # and cannot point a call at one, by either route
        assert (await a.post("/api/calls", json={"profile_id": "bank", "customer_ref": "demo"})).status_code == 403
        assert (await a.post("/api/call-jobs", json={**JOB, "customer_ref": "demo"})).status_code == 403


# --- who is NOT confined (behaviour that must not change) -----------------------------------------------


async def test_administrators_legacy_accounts_and_api_keys_still_see_everything():
    world = World()
    admin_hash = hash_password(PASSWORD)
    world.repo.create_account("boss@example.com", admin_hash, "admin", "Boss Co")  # an admin who DOES belong to an organization

    async with world.client() as a, world.client() as boss, world.client() as legacy, world.client() as system:
        alice = await signup(a, "alice@example.com", "Alice Co")

        seed_result(world, "CALL-A1", alice["organization_id"])
        seed_result(world, "CALL-OLD", None)
        job = (await a.post("/api/call-jobs", json=JOB)).json()

        await world.login(boss, email="boss@example.com")
        await world.login(legacy)  # operator with no organization: predates tenancy

        for label, client, headers in (("admin", boss, {}), ("legacy operator", legacy, {}), ("api key", system, KEY)):
            assert ids((await client.get("/api/calls", headers=headers)).json()) == ["CALL-A1", "CALL-OLD"], label
            assert (await client.get("/api/calls/CALL-OLD/result", headers=headers)).status_code == 200, label
            assert ids((await client.get("/api/call-jobs", headers=headers)).json(), "job_id") == [job["job_id"]], label
            assert (await client.get(f"/api/call-jobs/{job['job_id']}", headers=headers)).status_code == 200, label


async def test_a_valid_api_key_wins_over_a_confined_session_cookie_on_the_same_request():
    """`business` lets a valid key stand in for a login; the confinement must follow the same rule."""
    world = World()

    async with world.client() as a:
        alice = await signup(a, "alice@example.com", "Alice Co")

        other = world.repo.create_organization("Other Co")

        seed_result(world, "CALL-A1", alice["organization_id"])
        seed_result(world, "CALL-OTHER", other.id)

        assert ids((await a.get("/api/calls")).json()) == ["CALL-A1"]  # by cookie alone: confined
        assert ids((await a.get("/api/calls", headers=KEY)).json()) == ["CALL-A1", "CALL-OTHER"]  # key present: a business system


async def test_a_business_system_can_still_create_a_job_for_any_organization():
    world = World()

    async with world.client() as a, world.client() as system:
        alice = await signup(a, "alice@example.com", "Alice Co")

        made = await system.post("/api/call-jobs", json={**JOB, "organization_id": alice["organization_id"]}, headers=KEY)

        assert made.status_code == 201 and made.json()["organization_id"] == alice["organization_id"]
        assert ids((await a.get("/api/call-jobs")).json(), "job_id") == [made.json()["job_id"]]  # and she sees it
