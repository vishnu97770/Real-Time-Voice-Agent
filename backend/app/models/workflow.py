from typing import TYPE_CHECKING, Any

from sqlalchemy import ForeignKey, ForeignKeyConstraint, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, JsonType, TimestampMixin, one_of
from app.models.enums import WorkflowStatus

if TYPE_CHECKING:
    from app.db import JobRow
    from app.models.agent import Agent
    from app.models.organization import Organization


class Workflow(TimestampMixin, Base):
    """When and why an agent acts. Configuration only: nothing runs workflows yet."""

    __tablename__ = "workflows"
    __table_args__ = (
        # (agent, organization) together: the agent must belong to this workflow's organization.
        ForeignKeyConstraint(
            ["agent_id", "organization_id"],
            ["agents.id", "agents.organization_id"],
            name="fk_workflows_agent_id_agents",
        ),
        UniqueConstraint("organization_id", "name", name="uq_workflows_organization_id_name"),
        # Lets call jobs reference (workflow, organization) together.
        UniqueConstraint("id", "organization_id", name="uq_workflows_id_organization_id"),
        Index("ix_workflows_agent_id", "agent_id"),
        one_of("status", WorkflowStatus),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"))
    agent_id: Mapped[int] = mapped_column()
    name: Mapped[str] = mapped_column(String(120))
    trigger_type: Mapped[str] = mapped_column(String(32))  # open-ended: "manual", "schedule", "event", ...
    trigger_config: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)
    conditions: Mapped[list[Any]] = mapped_column(JsonType, default=list)
    action_config: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)
    # e.g. {"max_attempts": 3, "window": {"start": "10:00", "end": "18:00"}}
    retry_policy: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)
    status: Mapped[str] = mapped_column(String(16), server_default=WorkflowStatus.DRAFT)

    # Both relationships fill organization_id; the composite foreign key keeps them consistent.
    organization: Mapped["Organization"] = relationship(back_populates="workflows", overlaps="agent,workflows")
    agent: Mapped["Agent"] = relationship(back_populates="workflows", overlaps="organization,workflows")
    call_jobs: Mapped[list["JobRow"]] = relationship(back_populates="workflow", overlaps="organization,agent,contact,call_jobs")
