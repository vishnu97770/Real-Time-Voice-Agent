import copy
import json

import httpx
import pytest

from app import cli
from app.brains.base import Propose, TextDelta, ToolCall
from app.config import Settings
from app.db import Repository
from app.main import create_app
from app.profiles import PROFILES
from tests.helpers import ScriptedBrain, migrate
from tests.test_api import parse_sse

KEY = {"X-API-Key": "test-key"}
PHONE = "+91 98765 43210"


def anita():
    data = copy.deepcopy(PROFILES["bank"].data)
    data["customer"] = "Anita Rao"
    data["account"] = {"type": "current", "last4": "9090", "balance": 1250.5}
    data["cards"] = [{"type": "debit", "last4": "1111", "status": "active"}, {"type": "credit", "last4": "2222", "status": "active"}]
    return data


async def bank_script(ctx):
    if "freeze" in ctx.text:
        yield Propose("freeze_card", {"card": "credit"})
    else:
        result = await ctx.run_tool("get_balance", {})
        yield ToolCall("get_balance", {}, result)
        yield TextDelta(f"Balance {result['balance']}.")


class World:
    def __init__(self, script=bank_script, **settings):
        self.brain = ScriptedBrain(script)
        self.repo = settings.pop("repo", None) or Repository("sqlite://")
        self.settings = Settings(
            **{"database_url": "sqlite://", "api_key": "test-key", "public_base_url": "https://agent.example", **settings}
        )
        self.app = create_app(self.brain, self.settings, repo=self.repo)
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://test")
        self.repo.upsert_customer("bank", "anita", "Anita Rao", anita())

    async def call(self, profile="bank", ref="anita"):
        response = await self.client.post("/api/calls", json={"profile_id": profile, "customer_ref": ref})
        return response

    async def say(self, call_id, text):
        response = await self.client.post(f"/api/calls/{call_id}/turn", json={"text": text})
        return parse_sse(response.text)


# --- data validation -------------------------------------------------------------


@pytest.mark.parametrize("profile_id", list(PROFILES))
def test_every_profiles_demo_data_is_itself_valid(profile_id):
    profile = PROFILES[profile_id]

    assert profile.check_customer_data(profile.data) == []


def test_bad_customer_data_is_caught_when_loaded_not_in_the_middle_of_a_call():
    bank = PROFILES["bank"]

    assert "missing 'cards'" in bank.check_customer_data({k: v for k, v in bank.data.items() if k != "cards"})
    assert any("should be list" in p for p in bank.check_customer_data({**bank.data, "cards": "none"}))
    assert any("unexpected 'extra'" in p for p in bank.check_customer_data({**bank.data, "extra": 1}))
    assert bank.check_customer_data("not an object") == ["data must be an object"]

    # Right shape at the top, wrong inside: a tool would crash on it.
    deep = bank.check_customer_data({**bank.data, "account": {"type": "savings"}})
    assert any("get_balance cannot read this data" in p for p in deep)


# --- the API -------------------------------------------------------------------------


async def test_only_admins_or_a_business_key_may_change_customer_data():
    w = World(auth_required=True)
    body = {"display_name": "Anita Rao", "data": anita()}
    w.app.state.auth.create_user("admin@example.com", "admin password 123", "admin")
    w.app.state.auth.create_user("op@example.com", "operator password 123", "operator")
    url = "/api/customers/bank/anita"

    assert (await w.client.put(url, json=body)).status_code == 401  # nobody
    assert (await w.client.put(url, json=body, headers={"X-API-Key": "wrong"})).status_code == 401
    assert (await w.client.put(url, json=body, headers=KEY)).status_code == 204

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=w.app), base_url="http://test") as op:
        await op.post("/api/auth/login", json={"email": "op@example.com", "password": "operator password 123"})
        assert (await op.put(url, json=body)).status_code == 403, "operators may use customers, not edit them"
        assert (await op.get("/api/customers", params={"profile_id": "bank"})).status_code == 200
        assert (await op.get(url)).status_code == 403

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=w.app), base_url="http://test") as admin:
        await admin.post("/api/auth/login", json={"email": "admin@example.com", "password": "admin password 123"})
        assert (await admin.put(url, json=body)).status_code == 204
        assert (await admin.get(url)).json()["data"]["customer"] == "Anita Rao"


async def test_the_customer_list_shows_names_never_their_data():
    w = World()
    listing = (await w.client.get("/api/customers", params={"profile_id": "bank"}, headers=KEY)).json()

    assert [c["ref"] for c in listing] == ["anita"] and listing[0]["display_name"] == "Anita Rao"
    assert "data" not in listing[0] and "1250" not in json.dumps(listing)


async def test_bad_customer_data_and_bad_refs_are_refused():
    w = World()
    bad = await w.client.put("/api/customers/bank/x", json={"display_name": "X", "data": {"customer": "x"}}, headers=KEY)

    assert bad.status_code == 422 and "missing 'account'" in bad.text
    assert (await w.client.put("/api/customers/casino/x", json={"display_name": "X", "data": {}}, headers=KEY)).status_code == 404
    assert (await w.client.put("/api/customers/bank/a%20b", json={"display_name": "X", "data": anita()}, headers=KEY)).status_code == 422
    assert w.repo.get_customer("bank", "x") is None, "nothing was stored"


# --- calls about a real customer -----------------------------------------------------------


async def test_an_inbound_call_greets_the_real_customer_and_reads_their_data():
    w = World()
    response = await w.call()
    body = response.json()

    assert response.status_code == 201
    assert "speaking with Anita Rao" in body["greeting"] and "Priya" not in body["greeting"]

    events = await w.say(body["call_id"], "balance please")
    assert "1250.5" in events[0]["result"]["balance"] or "1,250" in events[0]["result"]["balance"]
    assert "Anita Rao" in w.brain.calls[0].persona and "Priya" not in w.brain.calls[0].persona


async def test_an_unknown_customer_is_refused_and_the_demo_data_still_works_without_one():
    w = World()

    assert (await w.call(ref="nobody")).status_code == 404
    demo = await w.client.post("/api/calls", json={"profile_id": "bank"})
    assert "Priya Sharma" in demo.json()["greeting"]


async def test_a_confirmed_action_is_written_back_to_that_customer_only():
    w = World()
    w.repo.upsert_customer("bank", "other", "Someone Else", anita())
    call_id = (await w.call()).json()["call_id"]

    await w.say(call_id, "freeze my credit card")
    assert w.repo.get_customer("bank", "anita")["data"]["cards"][1]["status"] == "active", "nothing before consent"

    assert (await w.say(call_id, "yes"))[-1]["kind"] == "action"

    assert w.repo.get_customer("bank", "anita")["data"]["cards"][1]["status"] == "frozen"
    assert w.repo.get_customer("bank", "other")["data"]["cards"][1]["status"] == "active"
    assert PROFILES["bank"].data["cards"][1]["status"] == "active", "the demo definition is never touched"

    # A later call about the same customer sees the frozen card.
    second = (await w.call()).json()["call_id"]
    events = await w.say(second, "freeze my credit card")
    assert "already frozen" in " ".join(e.get("text", "") for e in events)


async def test_if_the_change_cannot_be_saved_it_is_not_made_and_the_caller_is_told():
    w = World()
    call_id = (await w.call()).json()["call_id"]
    await w.say(call_id, "freeze my credit card")

    def broken(*args, **kwargs):
        raise OSError("disk full, password=hunter2")

    w.repo.save_customer_data = broken
    events = await w.say(call_id, "yes")
    session = w.app.state.service.store.get(call_id)

    assert events[-1]["kind"] == "error"
    assert "nothing was changed" in " ".join(e.get("text", "") for e in events)
    assert session.data["cards"][1]["status"] == "active", "rolled back"
    assert session.executed == [] and session.pending is None
    assert any(e["type"] == "action_failed" for e in session.audit)
    assert "hunter2" not in repr(session.audit), "details of the failure stay out of the audit log"
    assert w.repo.get_customer("bank", "anita")["data"]["cards"][1]["status"] == "active"


# --- outbound jobs about a customer --------------------------------------------------------


async def test_an_outbound_call_uses_the_customers_record_and_keeps_it_updated():
    w = World()
    job = {"profile_id": "bank", "customer_ref": "anita", "callee": {"name": "Anita Rao", "phone": PHONE}, "reason": "your card"}

    assert (await w.client.post("/api/call-jobs", json={**job, "customer_ref": "nobody"}, headers=KEY)).status_code == 422
    created = (await w.client.post("/api/call-jobs", json=job, headers=KEY)).json()
    assert created["customer_ref"] == "anita"

    token = created["answer_url"].split("token=")[1]
    answered = (await w.client.post(f"/api/call-jobs/{created['job_id']}/answer", json={"token": token})).json()
    call_id = answered["call_id"]
    await w.say(call_id, "yes")

    assert w.brain.calls[0].data["account"]["last4"] == "9090", "the agent sees Anita's record, not the demo's"

    await w.say(call_id, "freeze my credit card")
    await w.say(call_id, "yes")
    assert w.repo.get_customer("bank", "anita")["data"]["cards"][1]["status"] == "frozen"


async def test_an_applicant_call_reads_the_applicants_own_current_application():
    seen = {}

    async def script(ctx):
        seen["result"] = await ctx.run_tool("get_application", {"application_id": "APP-1031"})  # someone else's
        yield TextDelta("ok.")

    w = World(script)
    data = copy.deepcopy(PROFILES["underwriting"].data)
    data["current_id"] = "APP-1031"  # this applicant's application is the second one
    w.repo.upsert_customer("underwriting", "sneha", "Sneha Kulkarni", data)

    job = {"profile_id": "underwriting", "customer_ref": "sneha", "callee": {"name": "Sneha Kulkarni", "phone": PHONE}, "reason": "your documents"}
    created = (await w.client.post("/api/call-jobs", json=job, headers=KEY)).json()
    token = created["answer_url"].split("token=")[1]
    call_id = (await w.client.post(f"/api/call-jobs/{created['job_id']}/answer", json={"token": token})).json()["call_id"]
    await w.say(call_id, "yes")

    assert seen["result"]["id"] == "APP-1031" and seen["result"]["applicant"] == "Sneha Kulkarni"


# --- CLI ----------------------------------------------------------------------------------------


def test_seed_demo_and_import_customers(tmp_path, monkeypatch, capsys):
    url = f"sqlite:///{tmp_path / 'cli.db'}"
    migrate(url)
    monkeypatch.setattr(cli, "get_settings", lambda: Settings(database_url=url))

    assert cli.main(["seed-demo"]) == 0
    repo = Repository(url)
    assert {c["ref"] for c in repo.list_customers("bank")} == {"demo"}
    assert repo.get_customer("bank", "demo")["display_name"] == "Priya Sharma"

    good = tmp_path / "good.json"
    good.write_text(json.dumps([{"ref": "anita", "display_name": "Anita Rao", "data": anita()}]))
    assert cli.main(["import-customers", "bank", str(good)]) == 0
    assert {c["ref"] for c in repo.list_customers("bank")} == {"demo", "anita"}

    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps([
        {"ref": "ok", "display_name": "Fine", "data": anita()},
        {"ref": "broken", "display_name": "Broken", "data": {"customer": "x"}},
    ]))

    with pytest.raises(SystemExit, match="broken"):
        cli.main(["import-customers", "bank", str(bad)])

    assert {c["ref"] for c in repo.list_customers("bank")} == {"demo", "anita"}, "a bad file loads nothing at all"
