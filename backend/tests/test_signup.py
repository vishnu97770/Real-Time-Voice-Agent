"""Self-service sign-up: POST /api/auth/signup.

What must hold: a stranger gets an account with a workspace of their own and is signed in; they are
never an administrator; two sign-ups never see each other's data; a taken email or a weak password
leaves nothing behind; and the whole thing can be switched off and is rate limited.
"""

import pytest
from sqlalchemy import text

from tests.test_auth import World

EMAIL = "New.Person@Example.com"
PASSWORD = "a long enough password"
WORKSPACE = "  Northwind   Health "


async def signup(client, email=EMAIL, password=PASSWORD, workspace=WORKSPACE):
    return await client.post("/api/auth/signup", json={"email": email, "password": password, "workspace_name": workspace})


def count(world, table):
    with world.repo._lock, world.repo.engine.connect() as conn:
        return conn.execute(text(f"select count(*) from {table}")).scalar()


async def test_signing_up_creates_an_operator_with_a_workspace_and_signs_them_in():
    world = World()

    async with world.client() as client:
        response = await signup(client)

        assert response.status_code == 201

        user = response.json()["user"]

        assert user["email"] == "new.person@example.com"  # emails are case-insensitive
        assert user["role"] == "operator"  # never admin
        assert user["organization_id"] is not None

        # the session cookie is the same locked-down one login sets, and it works straight away
        cookie = response.headers["set-cookie"].lower()

        assert "va_session=" in cookie and "httponly" in cookie and "samesite=lax" in cookie

        me = await client.get("/api/auth/me")

        assert me.status_code == 200 and me.json()["user"] == user

        workspaces = (await client.get("/api/organizations")).json()

        assert [w["name"] for w in workspaces] == ["Northwind Health"]  # whitespace tidied
        assert workspaces[0]["id"] == user["organization_id"]


async def test_the_password_is_stored_hashed_never_as_typed():
    world = World()

    async with world.client() as client:
        await signup(client)

    stored = world.repo.get_user_by_email("new.person@example.com")["password_hash"]

    assert stored.startswith("scrypt$") and PASSWORD not in stored


async def test_they_can_sign_in_again_later_with_the_same_email_and_password():
    world = World()

    async with world.client() as first:
        await signup(first)
        await first.post("/api/auth/logout")

    async with world.client() as later:
        assert (await world.login(later, email="new.person@example.com", password=PASSWORD)).status_code == 200
        assert (await world.login(later, email=EMAIL, password=PASSWORD)).status_code == 200  # any letter case


async def test_a_new_user_can_actually_work_in_their_workspace_and_no_one_else_can_see_it():
    world = World()

    async with world.client() as alice, world.client() as bob:
        await signup(alice, email="alice@example.com", workspace="Alice Co")
        await signup(bob, email="bob@example.com", workspace="Bob Co")

        made = await alice.post("/api/agents", json={"name": "CareCall"})

        assert made.status_code == 201  # they have an organization to put it in

        agent_id = made.json()["id"]

        assert [a["name"] for a in (await alice.get("/api/agents")).json()] == ["CareCall"]
        assert (await bob.get("/api/agents")).json() == []  # Bob sees none of Alice's
        assert (await bob.get(f"/api/agents/{agent_id}")).status_code == 404
        assert [w["name"] for w in (await bob.get("/api/organizations")).json()] == ["Bob Co"]


async def test_self_service_never_grants_administrator_power():
    world = World()

    async with world.client() as client:
        await signup(client)

        # only administrators (or an API key) may change the customer data shared between organizations
        response = await client.put("/api/customers/bank/anyone", json={"display_name": "X", "data": {"customer": "x"}})

        assert response.status_code == 403


async def test_a_taken_email_is_refused_in_any_letter_case_and_leaves_nothing_behind():
    world = World()

    async with world.client() as client:
        await signup(client, email="taken@example.com")
        organizations, users = count(world, "organizations"), count(world, "users")

        for email in ("taken@example.com", "TAKEN@Example.COM", "op@example.com"):  # the last is the CLI-made user
            response = await signup(client, email=email)

            assert response.status_code == 409, email
            assert response.json()["detail"] == "An account with this email already exists"

    assert count(world, "organizations") == organizations  # no stray workspace from a refused sign-up
    assert count(world, "users") == users


@pytest.mark.parametrize("password", ["short", "elevenchars"])
async def test_a_weak_password_is_refused_with_the_reason_and_creates_nothing(password):
    world = World()
    before = (count(world, "organizations"), count(world, "users"))

    async with world.client() as client:
        response = await signup(client, password=password)

    assert response.status_code == 422
    assert "at least 12" in response.json()["detail"]
    assert (count(world, "organizations"), count(world, "users")) == before


async def test_bad_input_is_refused_before_anything_is_created():
    world = World()
    before = (count(world, "organizations"), count(world, "users"))

    async with world.client() as client:
        no_at = await signup(client, email="not-an-email")
        blank = await signup(client, workspace="    ")
        missing = await client.post("/api/auth/signup", json={"email": "a@b.co", "password": PASSWORD})
        empty = await signup(client, workspace="")

    assert no_at.status_code == 422 and "email" in no_at.json()["detail"].lower()
    assert blank.status_code == 422 and "workspace" in blank.json()["detail"].lower()
    assert missing.status_code == 422 and empty.status_code == 422
    assert (count(world, "organizations"), count(world, "users")) == before


async def test_sign_up_can_be_switched_off_and_the_server_says_so():
    world = World(signup_enabled=False)

    async with world.client() as client:
        response = await signup(client)

        assert response.status_code == 403
        assert "not open" in response.json()["detail"]
        assert (await client.get("/api/health")).json()["signup_enabled"] is False

    assert world.repo.get_user_by_email("new.person@example.com") is None


async def test_the_health_check_says_sign_up_is_open_by_default():
    world = World()

    async with world.client() as client:
        assert (await client.get("/api/health")).json()["signup_enabled"] is True


async def test_sign_up_is_rate_limited_per_address():
    world = World(limit_signup_per_ip=3)

    async with world.client() as client:
        statuses = [(await signup(client, email=f"user{i}@example.com")).status_code for i in range(5)]
        limited = await signup(client, email="one-more@example.com")

    assert statuses[:3] == [201, 201, 201]
    assert statuses[3:] == [429, 429]
    assert limited.status_code == 429 and int(limited.headers["retry-after"]) >= 1
