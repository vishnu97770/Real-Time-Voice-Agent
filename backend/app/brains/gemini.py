"""LLM brain backed by Gemini, with tool calling.

Read tools are executed here and their results fed back to the model, so every
fact in an answer was fetched this call. Guarded actions are never executed: the
first time the model calls one, the brain yields a Propose and stops, and the
session takes it through the caller-confirmation gate.
"""

from collections.abc import AsyncIterator
from typing import Any

from google.genai import types

from app.brains.base import BrainContext, BrainEvent, OutboundBrief, Propose, TextDelta, ToolCall, Turn
from app.profiles.base import ActionSpec, ToolSpec

MAX_TOOL_ROUNDS = 4

INSTRUCTIONS = """\
{persona}

You are an AI assistant on a live voice call, and you have already said so in your greeting.
{outbound}
How to talk:
- Everything you write is read aloud. Use short, natural spoken sentences: one or two for most answers.
- No markdown, bullet points, numbered lists, tables or emoji.
- Say currency as a number followed by "rupees", not with a symbol.

Facts:
- State only facts returned by your tools. Never guess, and never use outside knowledge about the caller's accounts or records.
- Call the relevant tool before answering any question about their data, even if you looked it up earlier, because it can change.
- If the tools do not provide what is needed, say plainly that you cannot see it.

Actions:
- To change anything, call the matching action tool. Never say the change is done: the system asks the caller to confirm first and reports the result itself.
- When calling an action tool, write no other text.
- If it is unclear which option the caller means, ask them instead of calling the action.

Safety:
- Never ask for, repeat or accept a password, PIN, one-time passcode or full card number.
- If the request is outside what your tools cover, say briefly what you can help with.
"""


OUTBOUND_INSTRUCTIONS = """
You placed this call. You are speaking with {callee_name}, who has confirmed who they are.
Reason for the call: {reason}
Your goal: {objective}
- Begin by saying, briefly, why you are calling, using your tools to get the specifics.
- Then help with whatever they need using only your tools.
- Respect their time. If they are busy or want to stop, say goodbye politely.
- Share only what this call needs.
"""


def build_instructions(persona: str, outbound: OutboundBrief | None) -> str:
    brief = ""

    if outbound:
        persona = outbound.persona
        brief = OUTBOUND_INSTRUCTIONS.format(
            callee_name=outbound.callee_name, reason=outbound.reason, objective=outbound.objective
        )

    return INSTRUCTIONS.format(persona=persona, outbound=brief)


def _declaration(name: str, description: str, parameters: dict) -> types.FunctionDeclaration:
    kwargs: dict[str, Any] = {"name": name, "description": description}

    # Gemini rejects an OBJECT schema with no properties, so a tool that takes no
    # arguments is declared without a schema.
    if parameters.get("properties"):
        kwargs["parameters_json_schema"] = parameters

    return types.FunctionDeclaration(**kwargs)


def function_declarations(
    tools: dict[str, ToolSpec], actions: dict[str, ActionSpec]
) -> list[types.FunctionDeclaration]:
    return [
        *[_declaration(t.name, t.description, t.parameters) for t in tools.values()],
        *[_declaration(a.name, a.description, a.parameters) for a in actions.values()],
    ]


def history_contents(history: list[Turn], text: str) -> list[types.Content]:
    """Prior turns plus the new utterance, in Gemini's shape.

    The greeting (leading agent turns) is left out: the conversation must open
    with the caller, and the system instruction already says the agent greeted.
    """
    turns = list(history)

    while turns and turns[0].role == "agent":
        turns.pop(0)

    turns.append(Turn("user", text))

    return [
        types.Content(
            role="model" if turn.role == "agent" else "user",
            parts=[types.Part(text=turn.text)],
        )
        for turn in turns
        if turn.text
    ]


class GeminiBrain:
    name = "gemini"

    def __init__(self, client: Any, model: str, thinking_budget: int = 0) -> None:
        self.client = client
        self.model = model
        self.thinking_budget = thinking_budget

    def _config(self, context: BrainContext) -> types.GenerateContentConfig:
        declarations = function_declarations(context.tools, context.actions)

        return types.GenerateContentConfig(
            system_instruction=build_instructions(context.persona, context.outbound),
            # Only what this call may use is ever offered to the model.
            tools=[types.Tool(function_declarations=declarations)] if declarations else None,
            # We run the tool loop ourselves so the consent gate can sit in it.
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            temperature=0.2,
            max_output_tokens=500,
            # Voice needs the first word fast; 0 turns thinking off, -1 lets the
            # model decide. Set GEMINI_THINKING_BUDGET for models that need it.
            thinking_config=types.ThinkingConfig(thinking_budget=self.thinking_budget),
        )

    async def respond(self, context: BrainContext) -> AsyncIterator[BrainEvent]:
        config = self._config(context)
        contents = history_contents(context.history, context.text)

        for _ in range(MAX_TOOL_ROUNDS):
            stream = await self.client.aio.models.generate_content_stream(
                model=self.model, contents=contents, config=config
            )

            model_parts: list[types.Part] = []
            calls: list[Any] = []

            async for chunk in stream:
                for candidate in chunk.candidates or []:
                    for part in (candidate.content.parts or []) if candidate.content else []:
                        model_parts.append(part)

                        if part.function_call:
                            calls.append(part.function_call)
                        elif part.text and not part.thought:
                            yield TextDelta(part.text)

            if not calls:
                return

            action_call = next((call for call in calls if call.name in context.actions), None)

            if action_call is not None:
                yield Propose(action_call.name, dict(action_call.args or {}))
                return

            # Keep the model's own parts intact (they can carry thought
            # signatures that must be echoed back), then answer every call.
            contents.append(types.Content(role="model", parts=model_parts))

            responses = []

            for call in calls:
                args = dict(call.args or {})
                result = await context.run_tool(call.name, args)

                yield ToolCall(call.name, args, result)
                responses.append(
                    types.Part.from_function_response(name=call.name, response={"result": result})
                )

            contents.append(types.Content(role="user", parts=responses))

        # Too many rounds without an answer: the session speaks its fallback.
