"""A profile is configuration: who the agent is, what data it can see, what it
may do, and what it must never do. The runtime never changes per deployment."""

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from app.guardrails import AI_DISCLOSURE, NEVER_ASK  # noqa: F401

Data = dict[str, Any]
Args = dict[str, Any]


def make_greeting(introduction: str, offer: str) -> str:
    return f"{AI_DISCLOSURE} {introduction} {NEVER_ASK} {offer}"


def rupees(amount: float) -> str:
    """Format with Indian digit grouping: 1800000 -> ₹18,00,000."""
    paise = f".{round(amount % 1 * 100):02d}" if amount % 1 else ""
    digits = f"{int(amount):d}"

    if len(digits) <= 3:
        return f"₹{digits}{paise}"

    head, tail = digits[:-3], digits[-3:]
    groups = []

    while len(head) > 2:
        groups.insert(0, head[-2:])
        head = head[:-2]

    if head:
        groups.insert(0, head)

    return f"₹{','.join(groups)},{tail}{paise}"


def list_sentence(items: list[str]) -> str:
    if len(items) <= 1:
        return "".join(items)
    return f"{', '.join(items[:-1])} and {items[-1]}"


def object_schema(properties: dict[str, dict], required: list[str] | None = None) -> dict:
    return {"type": "object", "properties": properties, "required": required or []}


@dataclass(frozen=True)
class ToolSpec:
    """A read-only lookup. The agent may state only what these return."""

    name: str
    topic: str
    description: str
    parameters: dict
    run: Callable[[Data, Args], Any]
    # Optional evidence reference, e.g. "Application: APP-1024".
    ref: Callable[[Data, Args], str | None] = lambda data, args: None


@dataclass(frozen=True)
class ActionResult:
    result: Any
    summary: str
    reply: str
    ref: str | None = None


@dataclass(frozen=True)
class ActionSpec:
    """Something that changes state. Always confirmed by the caller first."""

    name: str
    topic: str
    label: str
    description: str
    parameters: dict
    # Returns an error message if the arguments are not acceptable, else None.
    validate: Callable[[Data, Args], str | None]
    # Completes "I can ...": what will happen, in plain words.
    describe: Callable[[Args, Data], str]
    execute: Callable[[Args, Data], ActionResult]


@dataclass(frozen=True)
class OutboundProfile:
    """How a profile behaves when it places the call rather than receives it.

    The tools and actions listed here are the only ones available on an outbound
    call, and `fixed_args` overrides whatever the model passes, so a call to one
    customer can never look up someone else's record.
    """

    organisation: str
    persona: str
    objective: str
    tools: list[str] | None = None  # None means every read tool
    actions: list[str] = field(default_factory=list)
    # {tool name: {argument: value or callable(data) -> value}}
    fixed_args: dict[str, dict[str, Any]] = field(default_factory=dict)


@dataclass(frozen=True)
class Profile:
    id: str
    name: str
    counterparty: str
    persona: str
    greeting: str
    next_steps: list[str]
    data: Data
    tools: dict[str, ToolSpec] = field(default_factory=dict)
    actions: dict[str, ActionSpec] = field(default_factory=dict)
    outbound: OutboundProfile | None = None
    # Who the built-in demo data is about. Where it appears in the greeting or
    # persona it is swapped for the real customer's name.
    demo_name: str = ""

    def personalise(self, text: str, name: str | None) -> str:
        return text.replace(self.demo_name, name) if self.demo_name and name else text

    def check_customer_data(self, data: Any) -> list[str]:
        """Problems that would break this profile's tools, or an empty list.

        The shape must match the demo data, and every read tool must actually run
        against it, so bad data is rejected when it is loaded instead of failing
        in the middle of someone's call."""
        if not isinstance(data, dict):
            return ["data must be an object"]

        problems = []

        for key, sample in self.data.items():
            if key not in data:
                problems.append(f"missing '{key}'")
            elif type(data[key]) is not type(sample) and not (
                isinstance(data[key], (int, float)) and isinstance(sample, (int, float))
            ):
                problems.append(f"'{key}' should be {type(sample).__name__}, got {type(data[key]).__name__}")

        problems += [f"unexpected '{key}'" for key in data if key not in self.data]

        if problems:
            return problems

        import copy

        for tool in self.tools.values():
            try:
                tool.run(copy.deepcopy(data), {})
            except Exception as error:
                problems.append(f"{tool.name} cannot read this data ({type(error).__name__}: {error})")

        return problems
