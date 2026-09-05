from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol


class AgentEventType(StrEnum):
    TEXT_DELTA = "agent.text.delta"
    COMPLETE = "agent.complete"
    ERROR = "agent.error"


@dataclass(frozen=True, slots=True)
class AgentEvent:
    event_type: AgentEventType
    session_id: str
    text: str | None = None
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class AgentRequest:
    session_id: str
    text: str


class ConversationalAgent(Protocol):
    provider: str
    available: bool

    async def respond(self, request: AgentRequest) -> tuple[AgentEvent, ...]: ...


class UnavailableAgent:
    provider = "none"
    available = False

    async def respond(self, request: AgentRequest) -> tuple[AgentEvent, ...]:
        return (AgentEvent(AgentEventType.ERROR, request.session_id, detail="LLM provider is not configured"),)
