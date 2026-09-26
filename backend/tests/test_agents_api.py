"""Step 18B: GET /api/organizations[/{id}] and the Agent CRUD surface
(GET/POST/PUT /api/agents[/{id}]) - authenticated, scoped to the signed-in operator's own
organization, and never letting one organization see or touch another's agents."""

from tests.test_auth import EMAIL, PASSWORD, World

AGENT_PAYLOAD = {
    "name": "Collections Assistant",
    "role": "Collections Reminder Agent",
    "industry": "Banking & Finance",
    "purpose": "Remind customers about overdue payments.",
    "target_users": ["Customers"],
    "primary_tasks": ["Send reminders"],
    "behavior_config": {"tone": ["Professional", "Concise"]},
    "instructions": {"domain_context": "Consumer lending.", "additional_instructions": ""},
    "language": "English",
    "voice": "",
}


class OrgWorld(World):
    """A World whose one user already belongs to an organization, plus a second, unrelated
    organization+user pair for tenant-isolation tests. Organizations are created directly
    through the repository: there is no HTTP endpoint that creates one (by design - see the
    Step 18B report)."""

    def __init__(self, **settings):
        super().__init__(**settings)
        self.org = self.repo.create_organization("Northbridge Bank", "Banking & Finance")
        self.repo.update_user(self.user["id"], organization_id=self.org.id)

        self.other_org = self.repo.create_organization("Meridian Insurance", "Insurance")
        self.other_user = self.auth.create_user("other@example.com", PASSWORD, "operator")
        self.repo.update_user(self.other_user["id"], organization_id=self.other_org.id)


# --- organizations -----------------------------------------------------------------------------


async def test_an_authenticated_operator_lists_and_gets_their_own_organization():
    world = OrgWorld()

    async with world.client() as client:
        await world.login(client)

        listed = (await client.get("/api/organizations")).json()
        assert [o["id"] for o in listed] == [world.org.id]
        assert listed[0]["name"] == "Northbridge Bank"

        got = await client.get(f"/api/organizations/{world.org.id}")
        assert got.status_code == 200 and got.json()["id"] == world.org.id


async def test_unauthenticated_organization_access_is_refused():
    world = OrgWorld()

    async with world.client() as client:
        assert (await client.get("/api/organizations")).status_code == 401
        assert (await client.get(f"/api/organizations/{world.org.id}")).status_code == 401


async def test_an_operator_cannot_get_another_organizations_record():
    world = OrgWorld()

    async with world.client() as client:
        await world.login(client)
        response = await client.get(f"/api/organizations/{world.other_org.id}")
        assert response.status_code == 404


async def test_an_operator_with_no_organization_sees_an_empty_organization_list():
    world = World()  # the plain World: its one user has no organization

    async with world.client() as client:
        await world.login(client)
        assert (await client.get("/api/organizations")).json() == []


# --- agents: listing, creation, retrieval, update -----------------------------------------------


async def test_agent_listing_starts_empty_and_reflects_a_created_agent():
    world = OrgWorld()

    async with world.client() as client:
        await world.login(client)
        assert (await client.get("/api/agents")).json() == []

        created = await client.post("/api/agents", json=AGENT_PAYLOAD)
        assert created.status_code == 201
        body = created.json()
        assert body["name"] == "Collections Assistant"
        assert body["organization_id"] == world.org.id
        assert body["status"] == "active"  # a saved agent is treated as in use, not left "draft"

        listed = (await client.get("/api/agents")).json()
        assert [a["id"] for a in listed] == [body["id"]]


async def test_an_agent_can_be_fetched_by_id_and_updated():
    world = OrgWorld()

    async with world.client() as client:
        await world.login(client)
        created = (await client.post("/api/agents", json=AGENT_PAYLOAD)).json()
        agent_id = created["id"]

        fetched = await client.get(f"/api/agents/{agent_id}")
        assert fetched.status_code == 200 and fetched.json()["role"] == "Collections Reminder Agent"

        updated = await client.put(f"/api/agents/{agent_id}", json={**AGENT_PAYLOAD, "role": "Senior Collections Agent"})
        assert updated.status_code == 200 and updated.json()["role"] == "Senior Collections Agent"

        refetched = await client.get(f"/api/agents/{agent_id}")
        assert refetched.json()["role"] == "Senior Collections Agent"


async def test_getting_an_unknown_agent_is_404():
    world = OrgWorld()

    async with world.client() as client:
        await world.login(client)
        assert (await client.get("/api/agents/999999")).status_code == 404
        assert (await client.put("/api/agents/999999", json=AGENT_PAYLOAD)).status_code == 404


# --- tenant isolation ----------------------------------------------------------------------------


async def test_tenant_isolation_one_organization_cannot_reach_another_organizations_agent():
    world = OrgWorld()

    async with world.client() as client:
        await world.login(client)
        agent_id = (await client.post("/api/agents", json=AGENT_PAYLOAD)).json()["id"]

    async with world.client() as other_client:
        await world.login(other_client, email="other@example.com")

        assert (await other_client.get(f"/api/agents/{agent_id}")).status_code == 404
        assert (await other_client.get("/api/agents")).json() == []
        assert (await other_client.put(f"/api/agents/{agent_id}", json=AGENT_PAYLOAD)).status_code == 404


async def test_agent_creation_cannot_assign_another_organizations_id():
    world = OrgWorld()

    async with world.client() as client:
        await world.login(client)
        # organization_id is not a field AgentCreate declares at all (Schema forbids unknown
        # fields), so the attempt is refused before any service code runs.
        response = await client.post("/api/agents", json={**AGENT_PAYLOAD, "organization_id": world.other_org.id})
        assert response.status_code == 422

        assert (await client.get("/api/agents")).json() == []  # nothing was created either


# --- validation and conflict handling -------------------------------------------------------------


async def test_validation_errors_are_reported_clearly():
    world = OrgWorld()

    async with world.client() as client:
        await world.login(client)

        missing_name = await client.post("/api/agents", json={**AGENT_PAYLOAD, "name": ""})
        assert missing_name.status_code == 422

        bad_status = await client.post("/api/agents", json={**AGENT_PAYLOAD, "status": "bogus"})
        assert bad_status.status_code == 422


async def test_an_operator_with_no_organization_gets_a_clear_conflict_on_create():
    world = World()

    async with world.client() as client:
        await world.login(client)
        assert (await client.get("/api/agents")).json() == []

        response = await client.post("/api/agents", json=AGENT_PAYLOAD)
        assert response.status_code == 409


async def test_a_duplicate_agent_name_within_an_organization_is_a_clean_conflict():
    world = OrgWorld()

    async with world.client() as client:
        await world.login(client)
        assert (await client.post("/api/agents", json=AGENT_PAYLOAD)).status_code == 201
        second = await client.post("/api/agents", json=AGENT_PAYLOAD)
        assert second.status_code == 409


# --- existing authentication behavior --------------------------------------------------------------


async def test_login_and_me_now_include_organization_id():
    world = OrgWorld()

    async with world.client() as client:
        response = await world.login(client)
        assert response.json()["user"]["organization_id"] == world.org.id
        assert (await client.get("/api/auth/me")).json()["user"]["organization_id"] == world.org.id


async def test_a_user_with_no_organization_reports_it_as_null_not_an_error():
    world = World()

    async with world.client() as client:
        response = await world.login(client)
        assert response.json()["user"]["organization_id"] is None


async def test_existing_authentication_behavior_is_unchanged():
    world = OrgWorld()

    async with world.client() as client:
        assert (await client.get("/api/auth/me")).status_code == 401

        response = await world.login(client)
        assert response.status_code == 200 and response.json()["user"]["email"] == EMAIL

        assert (await client.post("/api/auth/logout")).status_code == 204
        assert (await client.get("/api/auth/me")).status_code == 401


async def test_the_anonymous_dev_mode_user_has_no_organization_and_is_unaffected():
    """AUTH_REQUIRED=false synthesizes an admin user with no database row at all (the operator()
    dependency's fallback, used by every route below - unlike /api/auth/me, which always needs a
    real session and is unaffected by this setting). Step 18B must not break that path, only make
    it (correctly) report "no organization"."""
    world = OrgWorld(auth_required=False)

    async with world.client() as client:
        assert (await client.get("/api/organizations")).json() == []
        assert (await client.get("/api/agents")).json() == []
        assert (await client.post("/api/agents", json=AGENT_PAYLOAD)).status_code == 409
