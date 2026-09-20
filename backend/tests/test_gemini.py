from types import SimpleNamespace

from google.genai import types

from app.brains.base import BrainContext, Propose, TextDelta, ToolCall, Turn
from app.brains.gemini import MAX_TOOL_ROUNDS, GeminiBrain, function_declarations
from app.profiles import get_profile


def text_chunk(text, thought=False):
    return chunk(types.Part(text=text, thought=thought or None))


def call_chunk(name, args):
    return chunk(types.Part(function_call=types.FunctionCall(name=name, args=args)))


def chunk(*parts):
    return SimpleNamespace(
        candidates=[SimpleNamespace(content=types.Content(role="model", parts=list(parts)))]
    )


class FakeClient:
    """Stands in for genai.Client: each generate_content_stream call yields the
    next scripted list of chunks, and records what it was asked."""

    def __init__(self, rounds):
        self.rounds = list(rounds)
        self.requests = []
        self.aio = SimpleNamespace(models=SimpleNamespace(generate_content_stream=self._stream))

    async def _stream(self, *, model, contents, config):
        self.requests.append({"model": model, "contents": list(contents), "config": config})
        chunks = self.rounds.pop(0)

        async def gen():
            for item in chunks:
                yield item

        return gen()


def make_context(client_text="What's my balance?", history=None, profile_id="bank"):
    profile = get_profile(profile_id)
    ran = []

    async def run_tool(name, args):
        ran.append((name, args))
        return {"balance": "₹84,250.75"}

    context = BrainContext(
        profile=profile,
        data=profile.data,
        history=history or [],
        text=client_text,
        run_tool=run_tool,
    )
    return context, ran


async def collect(brain, context):
    return [event async for event in brain.respond(context)]


async def test_plain_text_is_streamed_as_deltas():
    client = FakeClient([[text_chunk("Hello "), text_chunk("there.")]])
    context, _ = make_context()

    events = await collect(GeminiBrain(client, "m"), context)

    assert events == [TextDelta("Hello "), TextDelta("there.")]


async def test_a_read_tool_is_run_and_its_result_is_fed_back_before_the_answer():
    client = FakeClient(
        [
            [call_chunk("get_balance", {})],
            [text_chunk("Your balance is 84,250.75 rupees.")],
        ]
    )
    context, ran = make_context()

    events = await collect(GeminiBrain(client, "m"), context)

    assert ran == [("get_balance", {})]
    assert events == [
        ToolCall("get_balance", {}, {"balance": "₹84,250.75"}),
        TextDelta("Your balance is 84,250.75 rupees."),
    ]

    second = client.requests[1]["contents"]
    assert [content.role for content in second] == ["user", "model", "user"]
    assert second[1].parts[0].function_call.name == "get_balance"
    response = second[2].parts[0].function_response
    assert response.name == "get_balance"
    assert response.response == {"result": {"balance": "₹84,250.75"}}


async def test_an_action_call_becomes_a_proposal_and_is_never_run_or_answered():
    client = FakeClient([[call_chunk("freeze_card", {"card": "credit"})]])
    context, ran = make_context("freeze my credit card")

    events = await collect(GeminiBrain(client, "m"), context)

    assert events == [Propose("freeze_card", {"card": "credit"})]
    assert ran == []
    assert len(client.requests) == 1, "must not go back to the model after proposing"


async def test_thought_parts_are_not_spoken():
    client = FakeClient([[text_chunk("I should look this up", thought=True), text_chunk("Here you go.")]])
    context, _ = make_context()

    assert await collect(GeminiBrain(client, "m"), context) == [TextDelta("Here you go.")]


async def test_runaway_tool_loops_are_stopped():
    client = FakeClient([[call_chunk("get_balance", {})]] * (MAX_TOOL_ROUNDS + 2))
    context, ran = make_context()

    events = await collect(GeminiBrain(client, "m"), context)

    assert len(client.requests) == MAX_TOOL_ROUNDS
    assert len(ran) == MAX_TOOL_ROUNDS
    assert all(isinstance(event, ToolCall) for event in events)


async def test_empty_and_blocked_chunks_do_not_crash():
    client = FakeClient([[SimpleNamespace(candidates=None), SimpleNamespace(candidates=[SimpleNamespace(content=None)])]])
    context, _ = make_context()

    assert await collect(GeminiBrain(client, "m"), context) == []


async def test_request_shape_conversation_tools_and_safety_config():
    client = FakeClient([[text_chunk("ok.")]])
    history = [Turn("agent", "Hello, this is an AI assistant."), Turn("user", "hi"), Turn("agent", "How can I help?")]
    context, _ = make_context("balance?", history)

    await collect(GeminiBrain(client, "gemini-test", thinking_budget=0), context)

    request = client.requests[0]
    assert request["model"] == "gemini-test"
    assert [c.role for c in request["contents"]] == ["user", "model", "user"], "the greeting is dropped: a conversation opens with the caller"
    assert request["contents"][-1].parts[0].text == "balance?"

    config = request["config"]
    assert config.automatic_function_calling.disable is True, "the consent gate must sit inside the tool loop"
    assert config.thinking_config.thinking_budget == 0
    assert "Northbridge Bank" in config.system_instruction
    assert "Never ask for, repeat or accept a password" in config.system_instruction

    names = [decl.name for decl in config.tools[0].function_declarations]
    assert names == ["get_balance", "get_recent_transactions", "get_flagged_activity", "get_cards", "freeze_card"]


def test_tools_without_arguments_are_declared_without_a_schema():
    bank = get_profile("bank")
    declarations = {decl.name: decl for decl in function_declarations(bank.tools, bank.actions)}

    assert declarations["get_balance"].parameters_json_schema is None
    assert declarations["freeze_card"].parameters_json_schema["properties"]["card"]["enum"] == ["debit", "credit"]
