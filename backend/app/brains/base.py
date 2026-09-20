"""The brain turns one caller utterance into a stream of events.

It is the only part of the runtime that is an LLM. The session (consent gate,
guardrails, audit) sits above it and does not care how the events were made.

A brain never executes a guarded action. It can only propose one, and the
session asks the caller for confirmation before anything changes.
"""

from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from app.profiles.base import ActionSpec, Profile, ToolSpec


@dataclass(frozen=True)
class Turn:
    role: str  # "user" | "agent"
    text: str


@dataclass(frozen=True)
class ToolCall:
    """A read-only tool the brain ran to ground its answer."""

    name: str
    args: dict[str, Any]
    result: Any


@dataclass(frozen=True)
class TextDelta:
    text: str


@dataclass(frozen=True)
class Propose:
    """The brain wants to run a guarded action. Nothing has happened yet."""

    tool: str
    args: dict[str, Any]


BrainEvent = ToolCall | TextDelta | Propose

# (tool name, args) -> result. Supplied by the session so tool execution,
# audit and validation stay in one place.
ToolRunner = Callable[[str, dict[str, Any]], Awaitable[Any]]


@dataclass(frozen=True)
class OutboundBrief:
    """Who the agent has called and why. Present only on outbound calls."""

    callee_name: str
    reason: str
    organisation: str
    persona: str
    objective: str


@dataclass
class BrainContext:
    profile: Profile
    data: dict[str, Any]
    history: list[Turn]
    text: str
    run_tool: ToolRunner
    # What this call may use. Defaults to everything in the profile; an outbound
    # call narrows it. The brain must offer the model no more than this.
    tools: dict[str, ToolSpec] | None = None
    actions: dict[str, ActionSpec] | None = None
    outbound: OutboundBrief | None = None
    # The persona with the real customer's name in it. Defaults to the profile's.
    persona: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.tools is None:
            self.tools = self.profile.tools
        if self.actions is None:
            self.actions = self.profile.actions
        if self.persona is None:
            self.persona = self.profile.persona


class Brain(Protocol):
    name: str

    def respond(self, context: BrainContext) -> AsyncIterator[BrainEvent]: ...
