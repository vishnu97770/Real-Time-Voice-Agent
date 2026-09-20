import json

import httpx
import pytest

from app.brains.base import Propose, TextDelta, ToolCall
from app.config import Settings
from app.main import create_app
from tests.helpers import ScriptedBrain


async def bank_script(ctx):
    if "freeze" in ctx.text:
        yield Propose("freeze_card", {"card": "credit"})
    else:
        result = await ctx.run_tool("get_balance", {})
        yield ToolCall("get_balance", {}, result)
        yield TextDelta("Your balance is 84,250.75 rupees. Anything else?")


def client_for(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


@pytest.fixture
def app():
    return create_app(ScriptedBrain(bank_script), Settings(gemini_api_key=None, database_url="sqlite://"))


def parse_sse(body: str) -> list[dict]:
    events = []

    for block in body.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines())
        events.append({"event": lines["event"], **json.loads(lines["data"])})

    return events


async def test_health_reports_the_brain_or_its_absence():
    async with client_for(create_app(None, Settings(gemini_api_key=None, database_url="sqlite://"))) as client:
        body = (await client.get("/api/health")).json()

    assert body["status"] == "ok" and body["brain"] is None
    assert set(body["profiles"]) == {"underwriting", "bank", "insurance", "telecom", "admissions"}

    async with client_for(create_app(ScriptedBrain(), Settings(database_url="sqlite://"))) as client:
        assert (await client.get("/api/health")).json()["brain"] == "scripted"


async def test_starting_a_call_without_an_llm_is_a_clear_503_so_clients_can_fall_back():
    async with client_for(create_app(None, Settings(gemini_api_key=None, database_url="sqlite://"))) as client:
        response = await client.post("/api/calls", json={"profile_id": "bank"})

    assert response.status_code == 503


async def test_a_call_opens_with_the_ai_disclosure(app):
    async with client_for(app) as client:
        response = await client.post("/api/calls", json={"profile_id": "bank"})

    body = response.json()
    assert response.status_code == 201
    assert body["greeting"].startswith("Hello, this is an AI assistant.")
    assert body["call_id"].startswith("CALL-")


async def test_unknown_profile_and_unknown_call(app):
    async with client_for(app) as client:
        assert (await client.post("/api/calls", json={"profile_id": "casino"})).status_code == 404
        assert (await client.post("/api/calls/nope/turn", json={"text": "hi"})).status_code == 404
        assert (await client.post("/api/calls/nope/end")).status_code == 404


async def test_a_full_call_over_http(app):
    async with client_for(app) as client:
        call_id = (await client.post("/api/calls", json={"profile_id": "bank"})).json()["call_id"]

        turn = await client.post(f"/api/calls/{call_id}/turn", json={"text": "What's my balance?"})
        assert turn.headers["content-type"].startswith("text/event-stream")
        events = parse_sse(turn.text)
        assert [e["event"] for e in events] == ["tool_call", "sentence", "sentence", "done"]
        assert events[-1]["kind"] == "answer"

        turn = await client.post(f"/api/calls/{call_id}/turn", json={"text": "freeze my credit card"})
        events = parse_sse(turn.text)
        assert events[-1]["kind"] == "confirm"
        assert events[-1]["pending"]["tool"] == "freeze_card"

        turn = await client.post(f"/api/calls/{call_id}/turn", json={"text": "yes"})
        assert parse_sse(turn.text)[-1]["kind"] == "action"

        assert (await client.post(f"/api/calls/{call_id}/events", json={"type": "playback_interrupted"})).status_code == 204

        result = (await client.post(f"/api/calls/{call_id}/end")).json()

    assert result["outcome"] == "action_completed"
    assert result["transcript"][0]["speaker"] == "Agent"
    assert any(entry.get("interrupted") for entry in result["transcript"])
    assert {"ai_disclosed", "action_requested", "action_confirmed", "action_executed", "call_ended"} <= {
        event["type"] for event in result["audit"]
    }


async def test_secrets_never_leave_the_server_in_any_response(app):
    async with client_for(app) as client:
        call_id = (await client.post("/api/calls", json={"profile_id": "bank"})).json()["call_id"]
        turn = await client.post(f"/api/calls/{call_id}/turn", json={"text": "my pin is 4821"})
        result = (await client.post(f"/api/calls/{call_id}/end")).json()

    assert parse_sse(turn.text)[-1]["kind"] == "blocked"
    assert "4821" not in turn.text and "4821" not in json.dumps(result)


async def test_a_finished_call_accepts_no_more_turns(app):
    async with client_for(app) as client:
        call_id = (await client.post("/api/calls", json={"profile_id": "bank"})).json()["call_id"]
        await client.post(f"/api/calls/{call_id}/end")
        response = await client.post(f"/api/calls/{call_id}/turn", json={"text": "hello"})

    assert response.status_code == 409


async def test_request_validation(app):
    async with client_for(app) as client:
        call_id = (await client.post("/api/calls", json={"profile_id": "bank"})).json()["call_id"]

        assert (await client.post(f"/api/calls/{call_id}/turn", json={"text": ""})).status_code == 422
        assert (await client.post(f"/api/calls/{call_id}/turn", json={"text": "x" * 501})).status_code == 422
        assert (await client.post(f"/api/calls/{call_id}/events", json={"type": "explode"})).status_code == 422


async def test_the_number_of_concurrent_calls_is_capped():
    app = create_app(ScriptedBrain(), Settings(max_sessions=2, database_url="sqlite://"))

    async with client_for(app) as client:
        for _ in range(2):
            assert (await client.post("/api/calls", json={"profile_id": "bank"})).status_code == 201

        assert (await client.post("/api/calls", json={"profile_id": "bank"})).status_code == 503


async def test_calls_do_not_share_data():
    app = create_app(ScriptedBrain(bank_script), Settings(database_url="sqlite://"))

    async with client_for(app) as client:
        first = (await client.post("/api/calls", json={"profile_id": "bank"})).json()["call_id"]
        second = (await client.post("/api/calls", json={"profile_id": "bank"})).json()["call_id"]

        for text in ("freeze my credit card", "yes"):
            await client.post(f"/api/calls/{first}/turn", json={"text": text})

        one = (await client.post(f"/api/calls/{first}/end")).json()
        two = (await client.post(f"/api/calls/{second}/end")).json()

    assert one["outcome"] == "action_completed" and two["outcome"] == "completed"
