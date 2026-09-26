"""Step 18C: GET/POST/PUT/DELETE /api/contacts[/{id}] - authenticated, scoped to the signed-in
operator's own organization, and never letting one organization see or touch another's contacts.

Contact is a distinct domain concept from the legacy /api/customers demo data (see
test_customers_are_unaffected_by_the_contact_api below): the two are never conflated here."""

from tests.test_agents_api import OrgWorld
from tests.test_auth import World

CONTACT_PAYLOAD = {
    "name": "Priya Sharma",
    "phone": "+919876543210",
    "email": "priya@example.com",
    "metadata": {"policy_number": "POL-1234"},
    "consent_status": "granted",
    "preferred_language": "hi",
    "preferred_contact_time": {"start": "10:00", "end": "18:00", "timezone": "Asia/Kolkata"},
}


# --- listing, creation, retrieval, update, delete -------------------------------------------------


async def test_contact_listing_starts_empty_and_reflects_a_created_contact():
    world = OrgWorld()

    async with world.client() as client:
        await world.login(client)
        assert (await client.get("/api/contacts")).json() == []

        created = await client.post("/api/contacts", json=CONTACT_PAYLOAD)
        assert created.status_code == 201
        body = created.json()
        assert body["name"] == "Priya Sharma"
        assert body["organization_id"] == world.org.id
        assert body["consent_status"] == "granted"
        assert body["metadata"] == {"policy_number": "POL-1234"}  # the metadata_/metadata rename works
        assert body["preferred_contact_time"] == {"start": "10:00", "end": "18:00", "timezone": "Asia/Kolkata"}

        listed = (await client.get("/api/contacts")).json()
        assert [c["id"] for c in listed] == [body["id"]]


async def test_a_contact_can_be_fetched_by_id_and_updated():
    world = OrgWorld()

    async with world.client() as client:
        await world.login(client)
        created = (await client.post("/api/contacts", json=CONTACT_PAYLOAD)).json()
        contact_id = created["id"]

        fetched = await client.get(f"/api/contacts/{contact_id}")
        assert fetched.status_code == 200 and fetched.json()["phone"] == "+919876543210"

        updated = await client.put(f"/api/contacts/{contact_id}", json={"consent_status": "revoked"})
        assert updated.status_code == 200
        assert updated.json()["consent_status"] == "revoked"
        assert updated.json()["name"] == "Priya Sharma"  # untouched fields survive a partial update

        refetched = await client.get(f"/api/contacts/{contact_id}")
        assert refetched.json()["consent_status"] == "revoked"


async def test_updating_metadata_alone_does_not_disturb_other_fields():
    """The metadata_/metadata rename (app/db.py's _contact_columns) is exercised on update too,
    not just create - this is the regression the rename guards against."""
    world = OrgWorld()

    async with world.client() as client:
        await world.login(client)
        created = (await client.post("/api/contacts", json=CONTACT_PAYLOAD)).json()

        updated = await client.put(f"/api/contacts/{created['id']}", json={"metadata": {"policy_number": "POL-9999"}})
        assert updated.status_code == 200
        assert updated.json()["metadata"] == {"policy_number": "POL-9999"}


async def test_a_contact_can_be_deleted():
    world = OrgWorld()

    async with world.client() as client:
        await world.login(client)
        created = (await client.post("/api/contacts", json=CONTACT_PAYLOAD)).json()
        contact_id = created["id"]

        deleted = await client.delete(f"/api/contacts/{contact_id}")
        assert deleted.status_code == 204

        assert (await client.get(f"/api/contacts/{contact_id}")).status_code == 404
        assert (await client.get("/api/contacts")).json() == []


async def test_deleting_an_unknown_contact_is_404():
    world = OrgWorld()

    async with world.client() as client:
        await world.login(client)
        assert (await client.delete("/api/contacts/999999")).status_code == 404


async def test_deleting_a_contact_with_a_call_job_is_a_clean_conflict_not_a_crash():
    world = OrgWorld()

    async with world.client() as client:
        await world.login(client)
        contact_id = (await client.post("/api/contacts", json=CONTACT_PAYLOAD)).json()["id"]

    # A call job references the contact directly through the repository (no need for the full
    # outbound HTTP flow to prove the delete path is handled).
    world.repo.create_job({
        "id": "JOB-CONTACT1", "reference": None, "profile_id": "bank", "customer_ref": None,
        "organization_id": world.org.id, "agent_id": None, "contact_id": contact_id, "workflow_id": None,
        "callee_name": "Priya Sharma", "channel": "web", "callee_phone": "+919876543210", "reason": "x",
        "callback_url": None, "status": "ringing", "answer_token": "t", "created_at": 0.0, "expires_at": 100.0,
        "max_duration_seconds": 300, "callback_status": "none", "callback_attempts": 0,
    })

    async with world.client() as client:
        await world.login(client)
        response = await client.delete(f"/api/contacts/{contact_id}")
        assert response.status_code == 409

        # And the contact is still there: the failed delete did not half-apply.
        assert (await client.get(f"/api/contacts/{contact_id}")).status_code == 200


async def test_getting_an_unknown_contact_is_404():
    world = OrgWorld()

    async with world.client() as client:
        await world.login(client)
        assert (await client.get("/api/contacts/999999")).status_code == 404
        assert (await client.put("/api/contacts/999999", json=CONTACT_PAYLOAD)).status_code == 404


# --- unauthenticated access ------------------------------------------------------------------------


async def test_unauthenticated_contact_access_is_refused():
    world = OrgWorld()

    async with world.client() as client:
        assert (await client.get("/api/contacts")).status_code == 401
        assert (await client.post("/api/contacts", json=CONTACT_PAYLOAD)).status_code == 401
        assert (await client.get(f"/api/contacts/1")).status_code == 401
        assert (await client.put(f"/api/contacts/1", json=CONTACT_PAYLOAD)).status_code == 401
        assert (await client.delete(f"/api/contacts/1")).status_code == 401


# --- tenant isolation --------------------------------------------------------------------------------


async def test_tenant_isolation_for_get():
    world = OrgWorld()

    async with world.client() as client:
        await world.login(client)
        contact_id = (await client.post("/api/contacts", json=CONTACT_PAYLOAD)).json()["id"]

    async with world.client() as other_client:
        await world.login(other_client, email="other@example.com")
        assert (await other_client.get(f"/api/contacts/{contact_id}")).status_code == 404
        assert (await other_client.get("/api/contacts")).json() == []


async def test_tenant_isolation_for_put():
    world = OrgWorld()

    async with world.client() as client:
        await world.login(client)
        contact_id = (await client.post("/api/contacts", json=CONTACT_PAYLOAD)).json()["id"]

    async with world.client() as other_client:
        await world.login(other_client, email="other@example.com")
        blocked = await other_client.put(f"/api/contacts/{contact_id}", json={"consent_status": "revoked"})
        assert blocked.status_code == 404

    async with world.client() as client:
        await world.login(client)
        # And it was not, in fact, revoked by the other organization's attempt.
        assert (await client.get(f"/api/contacts/{contact_id}")).json()["consent_status"] == "granted"


async def test_tenant_isolation_for_delete():
    world = OrgWorld()

    async with world.client() as client:
        await world.login(client)
        contact_id = (await client.post("/api/contacts", json=CONTACT_PAYLOAD)).json()["id"]

    async with world.client() as other_client:
        await world.login(other_client, email="other@example.com")
        blocked = await other_client.delete(f"/api/contacts/{contact_id}")
        assert blocked.status_code == 404

    async with world.client() as client:
        await world.login(client)
        # Still there: the other organization's delete attempt did nothing.
        assert (await client.get(f"/api/contacts/{contact_id}")).status_code == 200


# --- validation --------------------------------------------------------------------------------------


async def test_contact_creation_cannot_assign_another_organizations_id():
    world = OrgWorld()

    async with world.client() as client:
        await world.login(client)
        response = await client.post("/api/contacts", json={**CONTACT_PAYLOAD, "organization_id": world.other_org.id})
        assert response.status_code == 422
        assert (await client.get("/api/contacts")).json() == []


async def test_validation_errors_are_reported_clearly():
    world = OrgWorld()

    async with world.client() as client:
        await world.login(client)

        missing_name = await client.post("/api/contacts", json={**CONTACT_PAYLOAD, "name": ""})
        assert missing_name.status_code == 422


async def test_consent_status_must_be_a_known_value():
    world = OrgWorld()

    async with world.client() as client:
        await world.login(client)
        response = await client.post("/api/contacts", json={**CONTACT_PAYLOAD, "consent_status": "maybe"})
        assert response.status_code == 422


async def test_consent_status_defaults_to_unknown_never_silently_to_granted():
    """The domain model's own default (ConsentStatus.UNKNOWN) is preserved: nothing here invents a
    more permissive default just because a contact was created through this API."""
    world = OrgWorld()

    async with world.client() as client:
        await world.login(client)
        minimal = {"name": "No Consent Given Yet"}
        created = (await client.post("/api/contacts", json=minimal)).json()
        assert created["consent_status"] == "unknown"


async def test_preferred_contact_time_round_trips_as_free_json_unchanged():
    """No new scheduling format is invented: whatever shape is sent is stored and returned as-is,
    matching the model's own free-JSON column."""
    world = OrgWorld()
    odd_shape = {"days": ["mon", "wed"], "window": "morning"}

    async with world.client() as client:
        await world.login(client)
        created = (await client.post("/api/contacts", json={**CONTACT_PAYLOAD, "preferred_contact_time": odd_shape})).json()
        assert created["preferred_contact_time"] == odd_shape

        fetched = (await client.get(f"/api/contacts/{created['id']}")).json()
        assert fetched["preferred_contact_time"] == odd_shape


async def test_an_operator_with_no_organization_gets_empty_lists_and_a_clear_conflict_on_create():
    world = World()

    async with world.client() as client:
        await world.login(client)
        assert (await client.get("/api/contacts")).json() == []

        response = await client.post("/api/contacts", json=CONTACT_PAYLOAD)
        assert response.status_code == 409


# --- legacy /api/customers is unaffected -------------------------------------------------------------


async def test_customers_are_unaffected_by_the_contact_api():
    """/api/customers (the legacy per-profile demo data) is a completely separate system: creating,
    listing and reading contacts must not touch it, and it must go on working exactly as before."""
    world = OrgWorld()

    async with world.client() as client:
        await world.login(client)
        await client.post("/api/contacts", json=CONTACT_PAYLOAD)

        # The legacy customers endpoint for the demo "bank" profile still works, unaffected.
        customers = await client.get("/api/customers?profile_id=bank")
        assert customers.status_code == 200
        assert isinstance(customers.json(), list)

        # And nothing about the contact leaked into it.
        assert not any(c.get("display_name") == "Priya Sharma" for c in customers.json())
