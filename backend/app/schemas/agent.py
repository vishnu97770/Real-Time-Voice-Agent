from datetime import datetime
from typing import Any

from pydantic import Field

from app.models.enums import AgentStatus
from app.schemas.base import Label, ResponseSchema, Schema, UpdateSchema


class AgentCreate(Schema):
    """`organization_id` is not accepted: the server sets it from who is asking."""

    name: str = Field(min_length=1, max_length=120)
    role: str | None = Field(default=None, max_length=120)
    industry: str | None = Field(default=None, max_length=64)
    purpose: str | None = Field(default=None, max_length=2000)
    target_users: list[Label] = Field(default_factory=list, max_length=20)
    primary_tasks: list[Label] = Field(default_factory=list, max_length=20)
    behavior_config: dict[str, Any] = Field(default_factory=dict)
    instructions: dict[str, Any] = Field(default_factory=dict)
    language: str = Field(default="en", min_length=2, max_length=16)
    voice: str | None = Field(default=None, max_length=64)
    status: AgentStatus = AgentStatus.DRAFT


class AgentUpdate(UpdateSchema):
    required_fields = frozenset(
        {"name", "target_users", "primary_tasks", "behavior_config", "instructions", "language", "status"}
    )

    name: str | None = Field(default=None, min_length=1, max_length=120)
    role: str | None = Field(default=None, max_length=120)
    industry: str | None = Field(default=None, max_length=64)
    purpose: str | None = Field(default=None, max_length=2000)
    target_users: list[Label] | None = Field(default=None, max_length=20)
    primary_tasks: list[Label] | None = Field(default=None, max_length=20)
    behavior_config: dict[str, Any] | None = None
    instructions: dict[str, Any] | None = None
    language: str | None = Field(default=None, min_length=2, max_length=16)
    voice: str | None = Field(default=None, max_length=64)
    status: AgentStatus | None = None


class AgentResponse(ResponseSchema):
    id: int
    organization_id: int
    name: str
    role: str | None
    industry: str | None
    purpose: str | None
    target_users: list[str]
    primary_tasks: list[str]
    behavior_config: dict[str, Any]
    instructions: dict[str, Any]
    language: str
    voice: str | None
    status: AgentStatus
    created_at: datetime
    updated_at: datetime
