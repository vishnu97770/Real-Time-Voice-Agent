import asyncio

import pytest

from app.brains.base import Propose, TextDelta, ToolCall
from app.profiles import PROFILES
from app.session import SentenceSplitter, close_session, mark_playback_interrupted, process_turn
from tests.helpers import done, say, spoken, start


def balance_brain(text="Your balance is 84,250.75 rupees. Anything else?"):
    async def script(ctx):
        result = await ctx.run_tool("get_balance", {})
        yield ToolCall("get_balance", {}, result)
        yield TextDelta(text)

    return script


def freeze_brain(card="credit"):
    async def script(ctx):
        if "balance" in ctx.text:
            result = await ctx.run_tool("get_balance", {})
            yield ToolCall("get_balance", {}, result)
            yield TextDelta("Your balance is 84,250.75 rupees.")
        else:
            yield Propose("freeze_card", {"card": card})

    return script


# --- grounded answers ------------------------------------------------------


async def test_answer_streams_sentences_after_the_tool_call():
    session, _ = start("bank", balance_brain())
    events = await say(session, "What's my balance?")

    assert [event["type"] for event in events] == ["tool_call", "sentence", "sentence", "done"]
    assert events[0]["result"]["balance"] == "₹84,250.75"
    assert done(events)["kind"] == "answer"
    assert spoken(events) == "Your balance is 84,250.75 rupees. Anything else?"
    assert session.topics == ["Account balance"]
    assert "Account ending 4821" in session.refs


async def test_a_reply_with_no_tool_call_is_chat_not_a_grounded_answer():
    async def script(ctx):
        yield TextDelta("I can help with your balance and cards.")

    session, _ = start("bank", script)

    assert done(await say(session, "what can you do")).get("kind") == "chat"


async def test_empty_brain_output_gets_a_spoken_fallback():
    session, _ = start("bank")
    events = await say(session, "hello?")

    assert done(events)["kind"] == "fallback"
    assert "didn't catch" in spoken(events)


async def test_unknown_tools_and_broken_tools_return_errors_instead_of_crashing():
    async def script(ctx):
        yield ToolCall("wire_money", {}, await ctx.run_tool("wire_money", {"to": "x"}))
        yield TextDelta("Sorry.")

    session, _ = start("bank", script)
    events = await say(session, "send money")

    assert "no tool called" in events[0]["result"]["error"]
    assert any(event["type"] == "tool_error" for event in session.audit)
    assert done(events)["kind"] == "answer"


# --- consent gate ----------------------------------------------------------


async def test_a_guarded_action_asks_first_and_changes_nothing_until_confirmed():
    session, brain = start("bank", freeze_brain())
    events = await say(session, "Freeze my credit card")

    assert done(events)["kind"] == "confirm"
    assert "freeze your credit card ending 3390" in spoken(events)
    assert "yes to confirm" in spoken(events)
    assert done(events)["pending"]["tool"] == "freeze_card"
    assert session.data["cards"][1]["status"] == "active"

    consulted = len(brain.calls)
    events = await say(session, "yes please")

    assert done(events)["kind"] == "action"
    assert events[0]["type"] == "tool_call" and events[0]["guarded"] is True
    assert session.data["cards"][1]["status"] == "frozen"
    assert done(events)["pending"] is None
    assert len(brain.calls) == consulted, "confirming must not go through the LLM"


async def test_declining_changes_nothing():
    session, _ = start("bank", freeze_brain())
    await say(session, "Freeze my credit card")
    events = await say(session, "no, cancel")

    assert done(events)["kind"] == "declined"
    assert session.data["cards"][1]["status"] == "active"
    assert session.pending is None
    assert session.declined and not session.executed


async def test_hesitation_never_counts_as_consent_and_repeats_the_question():
    session, brain = start("bank", freeze_brain())
    await say(session, "Freeze my credit card")
    consulted = len(brain.calls)
    events = await say(session, "hmm let me think")

    assert done(events)["kind"] == "reprompt"
    assert "yes or no" in spoken(events)
    assert session.pending is not None
    assert session.data["cards"][1]["status"] == "active"
    assert len(brain.calls) == consulted


async def test_moving_on_lets_the_pending_action_lapse_unconfirmed():
    session, _ = start("bank", freeze_brain())
    await say(session, "Freeze my credit card")
    events = await say(session, "actually what is my balance")

    assert done(events)["kind"] == "answer"
    assert session.pending is None
    assert session.data["cards"][1]["status"] == "active"
    assert any(event["type"] == "action_lapsed" for event in session.audit)
    assert session.lapsed


async def test_the_brain_can_only_propose_actions_the_profile_defines():
    async def script(ctx):
        yield Propose("wire_money", {"amount": 1})

    session, _ = start("bank", script)
    events = await say(session, "wire money")

    assert session.pending is None
    assert "not able" in spoken(events)


async def test_invalid_arguments_are_rejected_before_asking_for_consent():
    session, _ = start("bank", freeze_brain(card="platinum"))
    events = await say(session, "freeze my card")

    assert session.pending is None
    assert "Which card" in spoken(events)

    session, _ = start("telecom", lambda ctx: _propose("upgrade_plan", {"plan": "Postpaid 599"}))
    events = await say(session, "upgrade me")

    assert session.pending is None
    assert "already on Postpaid 599" in spoken(events)


async def _propose(tool, args):
    yield Propose(tool, args)


async def test_pending_is_recorded_only_after_the_whole_question_was_delivered():
    session, _ = start("bank", freeze_brain())
    stream = process_turn(session, "Freeze my credit card")

    first = await anext(stream)
    assert first["type"] == "sentence"
    assert session.pending is None, "consent question not fully delivered yet"

    await stream.aclose()

    assert session.pending is None, "an interrupted question must not leave a pending action"
    assert session.transcript[-1].interrupted


# --- guardrails ------------------------------------------------------------


async def test_secrets_are_refused_and_never_reach_the_brain_transcript_or_audit():
    session, brain = start("bank", freeze_brain())
    events = await say(session, "my pin is 4821 and card 4111 1111 1111 1111")

    assert done(events)["kind"] == "blocked" and done(events)["blocked"]
    assert brain.calls == []
    blob = repr(session.audit) + repr(session.transcript) + repr(session.history)
    assert "4821" not in blob and "4111" not in blob


async def test_an_agent_reply_that_solicits_a_secret_is_replaced_before_it_is_spoken():
    async def script(ctx):
        yield TextDelta("Sure. Please tell me your OTP to continue. Then we are done.")

    session, _ = start("bank", script)
    events = await say(session, "verify me")
    text = spoken(events)

    assert "OTP" not in text
    assert "I never ask for passwords" in text
    assert done(events)["blocked"]
    assert any(event["type"] == "guardrail_blocked" for event in session.audit)


# --- failure handling ------------------------------------------------------


async def test_a_brain_error_is_spoken_and_the_call_carries_on():
    async def script(ctx):
        raise RuntimeError("upstream exploded, key=SECRET")
        yield  # pragma: no cover

    session, _ = start("bank", script)
    events = await say(session, "balance")

    assert done(events)["kind"] == "error"
    assert "trouble" in spoken(events)
    assert "SECRET" not in repr(session.audit), "error details must not leak into the audit log"
    assert not session.ended


async def test_a_slow_brain_times_out_instead_of_hanging_the_call():
    async def script(ctx):
        await asyncio.sleep(5)
        yield TextDelta("too late")

    session, _ = start("bank", script, timeout=0.2)
    events = await say(session, "balance")

    assert done(events)["kind"] == "error"
    assert any(event.get("reason") == "TimeoutError" for event in session.audit)


async def test_cutting_a_reply_off_records_what_was_said_and_marks_it_interrupted():
    async def script(ctx):
        yield TextDelta("First sentence. Second sentence. Third sentence. ")

    session, _ = start("bank", script)
    stream = process_turn(session, "talk")
    await anext(stream)
    await stream.aclose()

    entry = session.transcript[-1]
    assert entry.speaker == "Agent" and entry.interrupted
    assert entry.text == "First sentence."
    assert session.history[-1].text == "First sentence."


async def test_turns_in_one_call_are_serialized():
    order = []

    async def script(ctx):
        order.append(f"start {ctx.text}")
        await asyncio.sleep(0.05)
        yield TextDelta("ok.")
        order.append(f"end {ctx.text}")

    session, _ = start("bank", script)
    await asyncio.gather(say(session, "one"), say(session, "two"))

    assert order == ["start one", "end one", "start two", "end two"]


# --- splitting -------------------------------------------------------------


def test_sentence_splitter_keeps_decimals_and_streams_across_deltas():
    splitter = SentenceSplitter()
    out = []
    for piece in ["The score is 0.", "28 which is moderate. Balan", "ce is ₹84,250.75. Done", "!"]:
        out += splitter.feed(piece)
    out += splitter.flush()

    assert out == ["The score is 0.28 which is moderate.", "Balance is ₹84,250.75.", "Done!"]


# --- call result -----------------------------------------------------------


async def test_call_result_has_outcome_actions_transcript_and_a_full_audit_trail():
    session, _ = start("bank", freeze_brain())
    await say(session, "what is my balance")
    await say(session, "Freeze my credit card")
    await say(session, "yes")
    mark_playback_interrupted(session)

    result = close_session(session)

    assert result["outcome"] == "action_completed"
    assert result["profile_id"] == "bank"
    assert "Card ending 3390" in result["evidence"]
    assert "Card ending 3390" not in result["summary"] and "frozen" in result["summary"]
    assert any("Executed after confirmation: Freeze card" in item for item in result["actions_taken"])
    assert [entry["speaker"] for entry in result["transcript"]][:2] == ["Agent", "You"]
    assert result["transcript"][-1].get("interrupted") is True

    types = {event["type"] for event in result["audit"]}
    assert {
        "call_started", "ai_disclosed", "user_turn", "tool_call", "action_requested",
        "action_confirmed", "action_executed", "playback_interrupted", "call_ended",
    } <= types


async def test_closing_twice_is_stable_and_an_unconfirmed_action_lapses_at_hangup():
    session, _ = start("bank", freeze_brain())
    await say(session, "Freeze my credit card")

    first = close_session(session)
    second = close_session(session)

    assert first["duration_seconds"] == second["duration_seconds"]
    assert first["outcome"] == "completed"
    assert any("Not confirmed" in item for item in first["actions_taken"])
    assert session.data["cards"][1]["status"] == "active"
    assert [event["type"] for event in session.audit].count("call_ended") == 1


async def test_no_turns_are_processed_after_the_call_ended():
    session, brain = start("bank", balance_brain())
    close_session(session)

    assert await say(session, "balance") == []
    assert brain.calls == []


# --- profiles --------------------------------------------------------------


@pytest.mark.parametrize("profile_id", list(PROFILES))
def test_every_tool_runs_and_every_action_completes_on_a_fresh_copy_of_the_data(profile_id):
    import copy

    profile = PROFILES[profile_id]
    data = copy.deepcopy(profile.data)

    for tool in profile.tools.values():
        assert tool.run(data, {}) is not None, tool.name
        assert tool.parameters["type"] == "object"

    sample_args = {
        "freeze_card": {"card": "credit"},
        "file_claim": {"type": "hospitalisation"},
        "upgrade_plan": {"plan": "Postpaid 799"},
        "schedule_counselor_call": {"slot": "Thursday at 4 PM"},
        "schedule_followup_call": {},
    }

    for action in profile.actions.values():
        args = sample_args[action.name]
        assert action.validate(data, args) is None, action.name
        assert action.describe(args, data)
        output = action.execute(args, data)
        assert output.reply and output.summary
        assert data != profile.data, f"{action.name} should change the working copy"

    assert copy.deepcopy(profile.data) == profile.data, "the profile definition must not be mutated"
