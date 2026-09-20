import asyncio
import json
import time

import httpx
import pytest

from app.brains.base import Propose, TextDelta, ToolCall
from app.config import Settings
from app.db import Repository
from app.main import create_app
from app.outbound import UnsafeCallbackURL, check_callback_url, sign_payload, verify_signature
from tests.helpers import ScriptedBrain
from tests.test_api import parse_sse

KEY = {"X-API-Key": "test-key"}
PHONE = "+91 98765 43210"


class Receiver:
    """A business's callback endpoint. Records every delivery."""

    def __init__(self, statuses=(200,)):
        self.calls: list[httpx.Request] = []
        self.statuses = list(statuses)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        status = self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]
        return httpx.Response(status)

    @property
    def bodies(self):
        return [json.loads(call.content) for call in self.calls]


async def bank_outbound(ctx):
    if "confirmed who they are" in ctx.text:
        result = await ctx.run_tool("get_flagged_activity", {})
        yield ToolCall("get_flagged_activity", {}, result)
        yield TextDelta("I'm calling about a charge of 18,999 rupees at TechMart. Was that you?")
    elif "not me" in ctx.text:
        yield Propose("freeze_card", {"card": "credit"})
    else:
        yield TextDelta("Understood.")


class Env:
    def __init__(self, script=bank_outbound, receiver=None, **settings):
        self.brain = ScriptedBrain(script)
        self.receiver = receiver or Receiver()
        self.repo = settings.pop("repo", None) or Repository("sqlite://")
        settings = {
            "database_url": "sqlite://",
            "api_key": "test-key",
            "allow_private_callbacks": True,
            "callback_backoff_seconds": 0,
            "public_base_url": "https://agent.example",
            **settings,
        }
        self.settings = Settings(**settings)
        self.app = create_app(
            self.brain,
            self.settings,
            repo=self.repo,
            http=httpx.AsyncClient(transport=httpx.MockTransport(self.receiver)),
        )
        self.service = self.app.state.service
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://test")

    async def job(self, **overrides):
        body = {
            "profile_id": "bank",
            "callee": {"name": "Priya Sharma", "phone": PHONE},
            "reason": "unusual activity on your card",
            "callback_url": "https://crm.example/hooks/calls",
            **overrides,
        }
        response = await self.client.post("/api/call-jobs", json=body, headers=KEY)
        assert response.status_code in (200, 201), response.text
        return response.json()

    async def answer(self, job):
        token = job["answer_url"].split("token=")[1]
        response = await self.client.post(f"/api/call-jobs/{job['job_id']}/answer", json={"token": token})
        return response, token

    async def say(self, call_id, text):
        response = await self.client.post(f"/api/calls/{call_id}/turn", json={"text": text})
        assert response.status_code == 200, response.text
        return parse_sse(response.text)

    async def settle(self):
        """Wait for callback deliveries. If one never finishes, fail with the stuck
        task's stack instead of hanging the whole suite."""
        try:
            await asyncio.wait_for(self.service.drain(), 10)
        except asyncio.TimeoutError:
            import io

            stacks = []
            for task in self.service._tasks:
                buffer = io.StringIO()
                task.print_stack(file=buffer)
                stacks.append(f"{task.get_coro().__qualname__} done={task.done()}\n{buffer.getvalue()}")
            raise AssertionError("callback delivery never finished:\n" + "\n".join(stacks)) from None

    async def status(self, job_id):
        # Reading a job can itself finish it (a ring timeout), which sends a
        # callback: so settle deliveries after the read, then read again.
        await self.client.get(f"/api/call-jobs/{job_id}", headers=KEY)
        await self.settle()
        return (await self.client.get(f"/api/call-jobs/{job_id}", headers=KEY)).json()


@pytest.fixture
async def env():
    e = Env()
    yield e
    await e.client.aclose()


# --- contract & auth ---------------------------------------------------------


async def test_a_wrong_or_unconfigured_api_key_is_refused_and_the_right_one_works():
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app(ScriptedBrain(), Settings(database_url="sqlite://"))),
        base_url="http://t",
    ) as client:
        # No key configured on the server: no key can be valid.
        assert (await client.get("/api/call-jobs", headers=KEY)).status_code == 401

    e = Env(auth_required=True)
    body = {"profile_id": "bank", "callee": {"name": "P", "phone": PHONE}, "reason": "x"}

    assert (await e.client.post("/api/call-jobs", json=body)).status_code == 401  # no credentials at all
    assert (await e.client.post("/api/call-jobs", json=body, headers={"X-API-Key": "nope"})).status_code == 401
    assert (await e.client.post("/api/call-jobs", json=body, headers=KEY)).status_code == 201


async def test_a_job_is_created_ringing_with_a_masked_phone_and_an_answer_link(env):
    job = await env.job()

    assert job["status"] == "ringing"
    assert job["callee"] == {"name": "Priya Sharma", "phone": "***3210"}
    assert job["answer_url"].startswith(f"https://agent.example/?job={job['job_id']}&token=")
    assert "98765" not in json.dumps(job)


async def test_the_same_reference_is_one_job_and_a_clashing_reference_is_refused(env):
    first = await env.job(reference="crm-1")
    again = await env.job(reference="crm-1")

    assert again["job_id"] == first["job_id"]

    clash = await env.client.post(
        "/api/call-jobs",
        headers=KEY,
        json={"reference": "crm-1", "profile_id": "bank", "callee": {"name": "Someone Else", "phone": PHONE}, "reason": "x"},
    )
    assert clash.status_code == 409


@pytest.mark.parametrize(
    "override",
    [
        {"profile_id": "casino"},
        {"callee": {"name": "P", "phone": "abc"}},
        {"callback_url": "ftp://x.example/hook"},
        {"reason": ""},
        {"max_duration_seconds": 5},
    ],
)
async def test_bad_jobs_are_rejected(env, override):
    body = {"profile_id": "bank", "callee": {"name": "P", "phone": PHONE}, "reason": "x", **override}

    assert (await env.client.post("/api/call-jobs", json=body, headers=KEY)).status_code == 422


@pytest.mark.parametrize(
    "url",
    ["http://127.0.0.1/hook", "http://localhost/hook", "http://10.0.0.5/hook", "http://169.254.169.254/latest", "http://[::1]/hook", "ftp://x.example/"],
)
async def test_callback_urls_that_point_inside_the_network_are_refused(url):
    with pytest.raises(UnsafeCallbackURL):
        await check_callback_url(url, allow_private=False)


async def test_a_job_with_an_internal_callback_url_is_refused_at_creation():
    e = Env(allow_private_callbacks=False)
    response = await e.client.post(
        "/api/call-jobs",
        headers=KEY,
        json={"profile_id": "bank", "callee": {"name": "P", "phone": PHONE}, "reason": "x", "callback_url": "http://169.254.169.254/x"},
    )

    assert response.status_code == 422 and "private" in response.text


# --- the call itself -----------------------------------------------------------


async def test_the_answer_link_needs_the_right_token(env):
    job = await env.job()
    url = f"/api/call-jobs/{job['job_id']}"

    assert (await env.client.get(f"{url}/ring", params={"token": "wrong"})).status_code == 404
    assert (await env.client.post(f"{url}/answer", json={"token": "wrong"})).status_code == 404

    token = job["answer_url"].split("token=")[1]
    ring = (await env.client.get(f"{url}/ring", params={"token": token})).json()
    assert ring["status"] == "ringing" and ring["organisation"] == "Northbridge Bank"


async def test_the_agent_says_who_it_is_and_asks_who_answered_before_saying_anything_else(env):
    job = await env.job()
    response, _ = await env.answer(job)
    greeting = response.json()["greeting"]

    assert response.status_code == 201
    assert greeting.startswith("Hello, this is an AI assistant.")
    assert "calling from Northbridge Bank" in greeting
    assert "Am I speaking with Priya Sharma?" in greeting
    for secret in ("TechMart", "18,999", "balance", "3390", "unusual activity"):
        assert secret not in greeting


async def test_nothing_reaches_the_brain_and_no_data_is_read_until_the_callee_confirms(env):
    call_id = (await env.answer(await env.job()))[0].json()["call_id"]

    events = await env.say(call_id, "who is this")
    assert events[-1]["kind"] == "reprompt" and not events[-1]["ended"]
    assert env.brain.calls == []

    session = env.service.store.get(call_id)
    result = await __import__("app.session", fromlist=["_run_tool"])._run_tool(session, "get_balance", {})
    assert "not confirmed" in result["error"]


async def test_a_full_outbound_call_from_job_to_signed_result(env):
    job = await env.job(reference="crm-77")
    call_id = (await env.answer(job))[0].json()["call_id"]

    assert (await env.status(job["job_id"]))["status"] == "in_progress"

    events = await env.say(call_id, "Yes, speaking")
    assert [e["event"] for e in events][:2] == ["tool_call", "sentence"]
    assert "TechMart" in events[1]["text"]

    opening = env.brain.calls[0]
    assert opening.outbound.callee_name == "Priya Sharma"
    assert opening.outbound.reason == "unusual activity on your card"
    assert set(opening.actions) == {"freeze_card"}

    events = await env.say(call_id, "that was not me")
    assert events[-1]["kind"] == "confirm"
    assert (await env.say(call_id, "yes please"))[-1]["kind"] == "action"

    assert (await env.client.post(f"/api/calls/{call_id}/end")).status_code == 200

    final = await env.status(job["job_id"])
    assert final["status"] == "completed"
    assert final["reference"] == "crm-77"
    assert final["result"]["outcome"] == "action_completed"
    assert final["result"]["disclosure_given"] is True
    assert final["result"]["outbound"]["identity"] == "confirmed"
    assert final["callback"] == {"status": "delivered", "attempts": 1}
    assert {"callee_identity_confirmed", "action_executed", "call_ended"} <= {e["type"] for e in final["result"]["audit"]}

    (call,) = env.receiver.calls
    assert str(call.url) == "https://crm.example/hooks/calls"
    assert verify_signature("test-key", call.content, call.headers["X-Signature"])
    delivered = json.loads(call.content)
    assert delivered["event"] == "call_job.finished"
    assert delivered["job"]["job_id"] == job["job_id"]
    assert delivered["job"]["result"]["transcript"][0]["speaker"] == "Agent"
    assert PHONE.replace(" ", "")[-5:] not in call.content.decode(), "the phone number is never sent back"

    assert (await env.client.get(f"/api/calls/{call_id}/result", headers=KEY)).json()["call_id"] == call_id


async def test_a_wrong_party_hears_nothing_about_the_callee_and_the_call_ends_itself(env):
    job = await env.job()
    call_id = (await env.answer(job))[0].json()["call_id"]

    events = await env.say(call_id, "No, wrong number")

    assert events[-1]["kind"] == "wrong_party" and events[-1]["ended"] is True
    assert env.brain.calls == []

    final = await env.status(job["job_id"])
    assert final["status"] == "wrong_party"
    assert final["end_reason"] == "wrong_party"
    assert final["result"]["outcome"] == "wrong_party"
    assert "TechMart" not in json.dumps(final)
    assert final["callback"]["status"] == "delivered"
    assert (await env.client.post(f"/api/calls/{call_id}/turn", json={"text": "hello"})).status_code == 409


async def test_unclear_answers_twice_end_the_call_without_disclosing_anything(env):
    job = await env.job()
    call_id = (await env.answer(job))[0].json()["call_id"]

    assert (await env.say(call_id, "hmm who's asking"))[-1]["kind"] == "reprompt"
    last = (await env.say(call_id, "what is this about"))[-1]

    assert last["kind"] == "identity_failed" and last["ended"]
    final = await env.status(job["job_id"])
    assert final["result"]["outcome"] == "identity_not_confirmed"
    assert env.brain.calls == []


async def test_a_reprompt_can_still_lead_to_a_confirmed_call(env):
    call_id = (await env.answer(await env.job()))[0].json()["call_id"]

    await env.say(call_id, "who is this")
    events = await env.say(call_id, "yes this is Priya")

    assert "TechMart" in " ".join(e.get("text", "") for e in events)


async def test_an_applicant_call_can_only_ever_see_that_applicants_own_record():
    seen = {}

    async def script(ctx):
        seen["tools"] = set(ctx.tools)
        seen["actions"] = set(ctx.actions)
        # The model tries to look at someone else's application.
        seen["result"] = await ctx.run_tool("get_application", {"application_id": "APP-1031"})
        yield TextDelta("Done.")

    e = Env(script)
    job = await e.job(profile_id="underwriting", callee={"name": "Ravi Menon", "phone": PHONE}, reason="your loan documents")
    call_id = (await e.answer(job))[0].json()["call_id"]
    await e.say(call_id, "yes")

    assert seen["tools"] == {"get_application", "get_missing_documents"}, "no queue, no other applicants"
    assert seen["actions"] == set()
    assert seen["result"]["id"] == "APP-1024" and seen["result"]["applicant"] == "Ravi Menon"
    assert "Sneha" not in json.dumps(seen["result"])


# --- job lifecycle ---------------------------------------------------------------


async def test_declining_finishes_the_job_and_reports_it(env):
    job = await env.job()
    token = job["answer_url"].split("token=")[1]

    assert (await env.client.post(f"/api/call-jobs/{job['job_id']}/decline", json={"token": token})).status_code == 204

    final = await env.status(job["job_id"])
    assert final["status"] == "declined"
    assert final["result"]["outcome"] == "declined" and final["result"]["disclosure_given"] is False
    assert env.receiver.bodies[0]["job"]["status"] == "declined"
    assert (await env.answer(job))[0].status_code == 409


async def test_a_call_nobody_answers_times_out_and_can_no_longer_be_answered(env):
    job = await env.job(ring_timeout_seconds=10)
    env.repo.update_job(job["job_id"], expires_at=time.time() - 1)

    final = await env.status(job["job_id"])

    assert final["status"] == "no_answer" and final["result"]["outcome"] == "no_answer"
    assert env.receiver.bodies[0]["job"]["status"] == "no_answer"
    assert (await env.answer(job))[0].status_code == 409


async def test_the_sweeper_expires_unanswered_jobs_on_its_own(env):
    job = await env.job()
    env.repo.update_job(job["job_id"], expires_at=time.time() - 1)

    await env.service.sweep()

    assert env.repo.get_job(job["job_id"])["status"] == "no_answer"


async def test_two_people_answering_at_once_get_one_call_between_them(env):
    job = await env.job()

    first, second = await asyncio.gather(env.answer(job), env.answer(job))

    assert sorted([first[0].status_code, second[0].status_code]) == [201, 409]
    assert len(env.service.store) == 1, "the loser's session must not be left behind"


async def test_a_callee_who_vanishes_is_hung_up_on_and_the_result_still_arrives(env):
    job = await env.job()
    call_id = (await env.answer(job))[0].json()["call_id"]
    await env.say(call_id, "yes")

    env.service.store.get(call_id).last_active = time.time() - 10_000
    await env.service.sweep()

    final = await env.status(job["job_id"])
    assert final["status"] == "completed" and final["end_reason"] == "idle"
    assert final["result"]["transcript"]
    assert final["callback"]["status"] == "delivered"


async def test_the_agent_ends_a_call_that_runs_past_its_time_limit(env):
    job = await env.job(max_duration_seconds=30)
    call_id = (await env.answer(job))[0].json()["call_id"]
    env.service.store.get(call_id).started_at -= 45

    events = await env.say(call_id, "yes")

    assert events[-1]["kind"] == "time_limit" and events[-1]["ended"]
    assert "out of time" in " ".join(e.get("text", "") for e in events)
    assert env.brain.calls == []
    assert (await env.status(job["job_id"]))["end_reason"] == "time_limit"


async def test_finishing_twice_sends_one_callback(env):
    job = await env.job()
    call_id = (await env.answer(job))[0].json()["call_id"]
    await env.say(call_id, "yes")

    await asyncio.gather(*[env.client.post(f"/api/calls/{call_id}/end") for _ in range(3)])
    await env.settle()

    assert len(env.receiver.calls) == 1


# --- callbacks ---------------------------------------------------------------------


async def test_a_failing_callback_is_retried_then_marked_failed():
    e = Env(receiver=Receiver(statuses=[500]))
    job = await e.job()
    token = job["answer_url"].split("token=")[1]
    await e.client.post(f"/api/call-jobs/{job['job_id']}/decline", json={"token": token})

    final = await e.status(job["job_id"])

    assert final["callback"] == {"status": "failed", "attempts": 3}
    assert len(e.receiver.calls) == 3


async def test_a_callback_that_succeeds_on_retry_is_delivered():
    e = Env(receiver=Receiver(statuses=[503, 200]))
    job = await e.job()
    token = job["answer_url"].split("token=")[1]
    await e.client.post(f"/api/call-jobs/{job['job_id']}/decline", json={"token": token})

    assert (await e.status(job["job_id"]))["callback"] == {"status": "delivered", "attempts": 2}


async def test_results_still_waiting_to_be_sent_are_delivered_after_a_restart():
    repo = Repository("sqlite://")
    first = Env(repo=repo, receiver=Receiver(statuses=[500]), callback_attempts=1)
    job = await first.job()
    token = job["answer_url"].split("token=")[1]
    await first.client.post(f"/api/call-jobs/{job['job_id']}/decline", json={"token": token})
    await first.settle()

    repo.update_job(job["job_id"], callback_status="pending")  # what a crash mid-delivery leaves
    second = Env(repo=repo)
    await second.service.resume_callbacks()
    await second.settle()

    assert len(second.receiver.calls) == 1
    assert repo.get_job(job["job_id"])["callback_status"] == "delivered"


def test_signatures_reject_tampering_replays_and_the_wrong_secret():
    body = b'{"event":"call_job.finished"}'
    header, _ = sign_payload("s3cret", body)

    assert verify_signature("s3cret", body, header)
    assert not verify_signature("s3cret", body + b" ", header)
    assert not verify_signature("other", body, header)
    assert not verify_signature("s3cret", body, "garbage")

    old, _ = sign_payload("s3cret", body, timestamp=int(time.time()) - 3600)
    assert not verify_signature("s3cret", body, old)


# --- persistence -----------------------------------------------------------------


async def test_jobs_and_results_survive_a_restart(tmp_path):
    url = f"sqlite:///{tmp_path / 'agent.db'}"
    first = Env(repo=Repository(url))
    job = await first.job()
    call_id = (await first.answer(job))[0].json()["call_id"]
    await first.say(call_id, "yes")
    await first.client.post(f"/api/calls/{call_id}/end")

    second = Env(repo=Repository(url))  # a fresh process, same database

    got = (await second.client.get(f"/api/call-jobs/{job['job_id']}", headers=KEY)).json()
    assert got["status"] == "completed" and got["result"]["call_id"] == call_id
    assert (await second.client.get("/api/call-jobs", headers=KEY)).json()[0]["job_id"] == job["job_id"]


async def test_inbound_call_results_are_saved_too(env):
    call_id = (await env.client.post("/api/calls", json={"profile_id": "bank"})).json()["call_id"]
    await env.client.post(f"/api/calls/{call_id}/end")

    result = (await env.client.get(f"/api/calls/{call_id}/result", headers=KEY)).json()

    assert result["direction"] == "inbound" and result["disclosure_given"] is True
    assert (await env.client.get("/api/calls/CALL-nope/result", headers=KEY)).status_code == 404


async def test_drain_returns_when_a_finished_task_has_not_been_discarded_yet(env):
    """Regression: asyncio.gather() completes without yielding when all its tasks are
    already done, which made drain() spin forever (and block the event loop) whenever
    a delivery finished just before it ran. The old code hung the whole suite about
    half the time, and would have hung server shutdown the same way."""
    finished = asyncio.create_task(asyncio.sleep(0))
    await finished
    env.service._tasks.add(finished)  # done, but its discard callback has not run

    await env.service.drain()

    assert not env.service._tasks


async def test_call_history_lists_finished_calls_newest_first_with_a_direction_filter(env):
    inbound = (await env.client.post("/api/calls", json={"profile_id": "bank"})).json()["call_id"]
    await env.client.post(f"/api/calls/{inbound}/end")

    job = await env.job()
    outbound = (await env.answer(job))[0].json()["call_id"]
    await env.say(outbound, "yes")
    await env.client.post(f"/api/calls/{outbound}/end")

    rows = (await env.client.get("/api/calls", headers=KEY)).json()

    assert [row["call_id"] for row in rows] == [outbound, inbound]
    assert rows[0]["direction"] == "outbound" and rows[0]["job_id"] == job["job_id"]
    assert rows[0]["callee_name"] == "Priya Sharma" and rows[1]["direction"] == "inbound"
    assert "transcript" not in rows[0] and "audit" not in rows[0], "the list is short; detail is one call away"

    only_out = (await env.client.get("/api/calls", params={"direction": "outbound"}, headers=KEY)).json()
    assert [row["call_id"] for row in only_out] == [outbound]
    assert (await env.client.get("/api/calls", params={"direction": "sideways"}, headers=KEY)).status_code == 422
    assert (await env.client.get("/api/calls")).status_code == 200  # dev mode: sign-in is off in this suite
