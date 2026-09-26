from typing import TYPE_CHECKING, Any

from sqlalchemy import ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, JsonType, TimestampMixin, one_of
from app.models.enums import AgentStatus

if TYPE_CHECKING:
    from app.db import JobRow
    from app.models.organization import Organization
    from app.models.workflow import Workflow


class Agent(TimestampMixin, Base):
    """A voice agent's configuration. The same shape serves every domain: what makes an agent
    a collections reminder or a patient follow-up is its data, not its columns."""

    __tablename__ = "agents"
    __table_args__ = (
        UniqueConstraint("organization_id", "name", name="uq_agents_organization_id_name"),
        # Lets workflows reference (agent, organization) together, so a workflow can never
        # point at another organization's agent.
        UniqueConstraint("id", "organization_id", name="uq_agents_id_organization_id"),
        one_of("status", AgentStatus),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"))
    name: Mapped[str] = mapped_column(String(120))
    role: Mapped[str | None] = mapped_column(String(120))
    industry: Mapped[str | None] = mapped_column(String(64))
    purpose: Mapped[str | None] = mapped_column(Text)
    target_users: Mapped[list[Any]] = mapped_column(JsonType, default=list)  # who it talks to
    primary_tasks: Mapped[list[Any]] = mapped_column(JsonType, default=list)
    behavior_config: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)  # tone, style, rules
    # Free-form guidance, e.g. {"domain_context": "...", "additional_instructions": "..."}.
    instructions: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)
    language: Mapped[str] = mapped_column(String(16), server_default="en")
    voice: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), server_default=AgentStatus.DRAFT)

    organization: Mapped["Organization"] = relationship(back_populates="agents")
    workflows: Mapped[list["Workflow"]] = relationship(back_populates="agent", overlaps="organization,workflows")
    call_jobs: Mapped[list["JobRow"]] = relationship(back_populates="agent", overlaps="organization,contact,workflow,call_jobs")
