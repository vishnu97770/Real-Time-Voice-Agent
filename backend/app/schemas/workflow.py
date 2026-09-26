from datetime import datetime
from typing import Any

from pydantic import Field

from app.models.enums import WorkflowStatus
from app.schemas.base import ResponseSchema, Schema, UpdateSchema

TRIGGER_TYPE = r"^[a-z][a-z0-9_]*$"  # e.g. "manual", "schedule", "event"


class WorkflowCreate(Schema):
    """Configuration only: nothing schedules or runs a workflow yet. The agent must belong to the
    same organization; the database enforces that."""

    agent_id: int
    name: str = Field(min_length=1, max_length=120)
    trigger_type: str = Field(pattern=TRIGGER_TYPE, max_length=32)
    trigger_config: dict[str, Any] = Field(default_factory=dict)
    conditions: list[dict[str, Any]] = Field(default_factory=list)
    action_config: dict[str, Any] = Field(default_factory=dict)
    retry_policy: dict[str, Any] = Field(default_factory=dict)
    status: WorkflowStatus = WorkflowStatus.DRAFT


class WorkflowUpdate(UpdateSchema):
    required_fields = frozenset(
        {"agent_id", "name", "trigger_type", "trigger_config", "conditions", "action_config", "retry_policy", "status"}
    )

    agent_id: int | None = None
    name: str | None = Field(default=None, min_length=1, max_length=120)
    trigger_type: str | None = Field(default=None, pattern=TRIGGER_TYPE, max_length=32)
    trigger_config: dict[str, Any] | None = None
    conditions: list[dict[str, Any]] | None = None
    action_config: dict[str, Any] | None = None
    retry_policy: dict[str, Any] | None = None
    status: WorkflowStatus | None = None


class WorkflowResponse(ResponseSchema):
    id: int
    organization_id: int
    agent_id: int
    name: str
    trigger_type: str
    trigger_config: dict[str, Any]
    conditions: list[dict[str, Any]]
    action_config: dict[str, Any]
    retry_policy: dict[str, Any]
    status: WorkflowStatus
    created_at: datetime
    updated_at: datetime
