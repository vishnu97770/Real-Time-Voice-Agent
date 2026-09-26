from typing import TYPE_CHECKING

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, one_of
from app.models.enums import OrganizationStatus

if TYPE_CHECKING:
    from app.db import JobRow, UserRow
    from app.models.agent import Agent
    from app.models.contact import Contact
    from app.models.workflow import Workflow


class Organization(TimestampMixin, Base):
    """A business using the platform: the tenant that owns everything below it."""

    __tablename__ = "organizations"
    __table_args__ = (one_of("status", OrganizationStatus),)

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    industry: Mapped[str | None] = mapped_column(String(64))  # free text: no industry is special
    status: Mapped[str] = mapped_column(String(16), server_default=OrganizationStatus.ACTIVE)

    users: Mapped[list["UserRow"]] = relationship(back_populates="organization")
    agents: Mapped[list["Agent"]] = relationship(back_populates="organization")
    contacts: Mapped[list["Contact"]] = relationship(back_populates="organization")
    workflows: Mapped[list["Workflow"]] = relationship(back_populates="organization", overlaps="agent,workflows")
    call_jobs: Mapped[list["JobRow"]] = relationship(
        back_populates="organization", overlaps="agent,contact,workflow,call_jobs"
    )
