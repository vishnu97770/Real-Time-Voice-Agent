import time

import httpx
import pytest
from sqlalchemy import text

from app import cli
from app.config import Settings
from app.db import Repository
from app.main import create_app
from app.security import RateLimiter, hash_token
from tests.helpers import ScriptedBrain

EMAIL = "op@example.com"
PASSWORD = "correct horse battery"
JOB = {"profile_id": "bank", "callee": {"name": "P", "phone": "+91 98765 43210"}, "reason": "x"}


class World:
    def __init__(self, **settings):
        self.repo = Repository("sqlite://")
        self.settings = Settings(
            **{"database_url": "sqlite://", "auth_required": True, "api_key": "test-key", **settings}
        )
        self.app = create_app(ScriptedBrain(), self.settings, repo=self.repo)
        self.auth = self.app.state.auth
        self.user = self.auth.create_user(EMAIL, PASSWORD, "operator")

    def client(self, **kwargs):
        return httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://test", **kwargs)

    async def login(self, client, email=EMAIL, password=PASSWORD, **headers):
        return await client.post("/api/auth/login", json={"email": email, "password": password}, headers=headers)


# --- passwords & users -----------------------------------------------------------


def test_passwords_are_hashed_and_weak_ones_refused():
    world = World()
    row = world.repo.get_user_by_email(EMAIL)

    assert row["password_hash"].startswith("scrypt$") and PASSWORD not in row["password_hash"]

    for bad in ("short", "elevenchars"):
        with pytest.raises(ValueError, match="at least 12"):
            world.auth.create_user("x@example.com", bad)

    with pytest.raises(ValueError, match="role"):
        world.auth.create_user("y@example.com", PASSWORD, "superuser")
    with pytest.raises(Exception):
        world.auth.create_user(EMAIL, PASSWORD)  # duplicate


def test_the_cli_creates_users_without_ever_taking_a_password_on_the_command_line(tmp_path, monkeypatch, capsys):
    url = f"sqlite:///{tmp_path / 'cli.db'}"
    monkeypatch.setattr(cli, "get_settings", lambda: Settings(database_url=url))
    monkeypatch.setenv("VOICE_AGENT_NEW_PASSWORD", PASSWORD)

    assert cli.main(["create-user", "Admin@Example.com", "--role", "admin"]) == 0
    assert cli.main(["create-user", "admin@example.com"]) == 1  # already exists (case-insensitive)
    assert cli.main(["list-users"]) == 0
    assert "admin@example.com" in capsys.readouterr().out

    monkeypatch.setenv("VOICE_AGENT_NEW_PASSWORD", "tooshort")
    assert cli.main(["create-user", "b@example.com"]) == 1

    repo = Repository(url)
    assert repo.get_user_by_email("admin@example.com")["role"] == "admin"


# --- signing in --------------------------------------------------------------------


async def test_signing_in_sets_a_locked_down_cookie_and_me_works():
    world = World()
    async with world.client() as client:
        assert (await client.get("/api/auth/me")).status_code == 401

        response = await world.login(client)
        cookie = response.headers["set-cookie"].lower()

        assert response.status_code == 200 and response.json()["user"]["email"] == EMAIL
        assert "httponly" in cookie and "samesite=lax" in cookie and "secure" not in cookie
        assert "password" not in response.text
        assert (await client.get("/api/auth/me")).json()["user"]["role"] == "operator"


async def test_the_cookie_is_marked_secure_when_configured():
    world = World(cookie_secure=True)
    async with world.client() as client:
        assert "secure" in (await world.login(client)).headers["set-cookie"].lower()


async def test_a_wrong_password_and_an_unknown_email_look_identical():
    world = World()
    async with world.client() as client:
        wrong = await world.login(client, password="not the password!")
        unknown = await world.login(client, email="nobody@example.com")

    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json() == {"detail": "Invalid email or password"}
    assert "set-cookie" not in wrong.headers


async def test_only_the_hash_of_the_session_token_is_stored():
    world = World()
    async with world.client() as client:
        await world.login(client)
        token = client.cookies.get("va_session")

    with world.repo.engine.connect() as conn:
        stored = [row[0] for row in conn.execute(text("select token_hash from auth_sessions"))]

    assert stored == [hash_token(token)] and token not in stored


async def test_logging_out_kills_the_session_even_for_someone_who_kept_the_cookie():
    world = World()
    async with world.client() as client:
        await world.login(client)
        token = client.cookies.get("va_session")
        assert (await client.post("/api/auth/logout")).status_code == 204
        assert (await client.get("/api/auth/me")).status_code == 401

    async with world.client(cookies={"va_session": token}) as replay:
        assert (await replay.get("/api/auth/me")).status_code == 401


async def test_an_expired_session_is_refused():
    world = World()
    world.repo.create_auth_session(hash_token("old-token"), world.user["id"], time.time() - 1)

    async with world.client(cookies={"va_session": "old-token"}) as client:
        assert (await client.get("/api/auth/me")).status_code == 401


async def test_disabling_a_user_signs_them_out_immediately():
    world = World()
    async with world.client() as client:
        await world.login(client)
        assert (await client.get("/api/auth/me")).status_code == 200

        world.repo.update_user(world.user["id"], disabled=1)

        assert (await client.get("/api/auth/me")).status_code == 401
        assert (await world.login(client)).status_code == 401


# --- what needs a login -----------------------------------------------------------


async def test_the_console_endpoints_need_a_login_unless_sign_in_is_switched_off():
    world = World()
    async with world.client() as client:
        assert (await client.post("/api/calls", json={"profile_id": "bank"})).status_code == 401
        await world.login(client)
        assert (await client.post("/api/calls", json={"profile_id": "bank"})).status_code == 201

    open_world = World(auth_required=False)
    async with open_world.client() as client:
        assert (await client.post("/api/calls", json={"profile_id": "bank"})).status_code == 201


async def test_the_business_api_accepts_an_api_key_or_a_session_and_nothing_else():
    world = World()
    async with world.client() as client:
        assert (await client.get("/api/call-jobs")).status_code == 401
        assert (await client.get("/api/call-jobs", headers={"X-API-Key": "wrong"})).status_code == 401
        assert (await client.get("/api/call-jobs", headers={"X-API-Key": "test-key"})).status_code == 200

        await world.login(client)
        assert (await client.get("/api/call-jobs")).status_code == 200
        assert (await client.post("/api/call-jobs", json=JOB)).status_code == 201, "operators can place calls too"


async def test_callee_links_and_health_stay_public():
    world = World()
    async with world.client() as client:
        health = (await client.get("/api/health")).json()
        assert health["auth_required"] is True
        assert (await client.get("/api/call-jobs/JOB-X/ring", params={"token": "t"})).status_code == 404


async def test_callbacks_are_refused_when_there_is_nothing_to_sign_them_with():
    world = World(api_key=None, auth_required=False)
    async with world.client() as client:
        body = {**JOB, "callback_url": "https://crm.example/hook"}
        response = await client.post("/api/call-jobs", json=body)

    assert response.status_code == 422 and "WEBHOOK_SECRET" in response.text


# --- cross-site requests -------------------------------------------------------------


async def test_a_signed_in_browser_cannot_be_driven_from_another_site():
    world = World()
    async with world.client() as client:
        await world.login(client)

        evil = {"Origin": "https://evil.example"}
        assert (await client.post("/api/calls", json={"profile_id": "bank"}, headers=evil)).status_code == 403
        assert (await client.post("/api/call-jobs", json=JOB, headers=evil)).status_code == 403
        assert (await client.get("/api/call-jobs", headers=evil)).status_code == 200, "reads are not state-changing"

        ours = {"Origin": "http://localhost:5173"}
        assert (await client.post("/api/calls", json={"profile_id": "bank"}, headers=ours)).status_code == 201

    async with world.client() as machine:  # an API client has no cookie, so no origin check
        response = await machine.post("/api/call-jobs", json=JOB, headers={"X-API-Key": "test-key", "Origin": "https://x.example"})
        assert response.status_code == 201


# --- rate limits ------------------------------------------------------------------------


async def test_login_is_limited_per_address_even_for_the_right_password():
    world = World(limit_login_per_ip=3)
    async with world.client() as client:
        for n in range(3):
            assert (await world.login(client, email=f"u{n}@example.com")).status_code == 401

        blocked = await world.login(client)

    assert blocked.status_code == 429 and int(blocked.headers["retry-after"]) >= 1


async def test_login_is_limited_per_account_across_addresses():
    world = World(limit_login_per_email=2, trusted_proxy_hops=1)
    async with world.client() as client:
        for n in range(2):
            r = await world.login(client, password="wrong wrong wrong", **{"X-Forwarded-For": f"10.0.0.{n}"})
            assert r.status_code == 401

        # A different address, but the same account being guessed at.
        assert (await world.login(client, **{"X-Forwarded-For": "10.0.0.99"})).status_code == 429


async def test_a_client_cannot_dodge_the_limit_by_lying_about_its_address():
    world = World(limit_login_per_ip=2)  # no trusted proxy: X-Forwarded-For is ignored
    async with world.client() as client:
        codes = [(await world.login(client, email=f"u{n}@example.com", **{"X-Forwarded-For": f"1.1.1.{n}"})).status_code for n in range(3)]

    assert codes == [401, 401, 429]

    world = World(limit_login_per_ip=2, trusted_proxy_hops=1)  # behind one proxy: the last entry is ours
    async with world.client() as client:
        codes = [
            (await world.login(client, email=f"u{n}@example.com", **{"X-Forwarded-For": f"{n}.{n}.{n}.{n}, 9.9.9.9"})).status_code
            for n in range(3)
        ]

    assert codes == [401, 401, 429], "the client-written left part is ignored"


async def test_guessing_call_links_is_throttled():
    world = World(limit_callee_per_ip=5)
    async with world.client() as client:
        codes = [(await client.get("/api/call-jobs/JOB-1/ring", params={"token": f"guess{n}"})).status_code for n in range(7)]

    assert codes == [404] * 5 + [429] * 2


async def test_a_single_call_cannot_be_flooded_with_turns():
    world = World(auth_required=False, limit_turns_per_call=2)
    async with world.client() as client:
        call_id = (await client.post("/api/calls", json={"profile_id": "bank"})).json()["call_id"]
        codes = [(await client.post(f"/api/calls/{call_id}/turn", json={"text": "hi"})).status_code for _ in range(3)]

    assert codes == [200, 200, 429]


async def test_job_creation_is_limited_per_caller():
    world = World(limit_jobs_per_principal=2)
    async with world.client() as client:
        codes = [(await client.post("/api/call-jobs", json=JOB, headers={"X-API-Key": "test-key"})).status_code for _ in range(3)]

    assert codes == [201, 201, 429]


def test_the_limiter_is_a_sliding_window_that_forgets():
    limiter = RateLimiter()

    assert [limiter.check("k", 3, 60, now=t) for t in (0, 10, 20)] == [None, None, None]
    assert limiter.check("k", 3, 60, now=30) == pytest.approx(30)  # told when to come back
    assert limiter.check("other", 3, 60, now=30) is None, "keys are independent"
    assert limiter.check("k", 3, 60, now=61) is None, "the oldest hit has aged out"

    limiter.prune(now=10_000, window_seconds=60)
    assert limiter._hits == {}
