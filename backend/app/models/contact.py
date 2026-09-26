from typing import TYPE_CHECKING, Any

from sqlalchemy import ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, JsonType, TimestampMixin, one_of
from app.models.enums import ConsentStatus, ContactStatus

if TYPE_CHECKING:
    from app.db import JobRow
    from app.models.organization import Organization


class Contact(TimestampMixin, Base):
    """A person an organization may call. Anything domain-specific (an appointment, a policy
    number) goes in `metadata`, so the table stays the same for every industry."""

    __tablename__ = "contacts"
    __table_args__ = (
        Index("ix_contacts_organization_id_phone", "organization_id", "phone"),
        # Lets call jobs reference (contact, organization) together, so a job can never
        # point at another organization's contact.
        UniqueConstraint("id", "organization_id", name="uq_contacts_id_organization_id"),
        one_of("status", ContactStatus),
        one_of("consent_status", ConsentStatus),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"))
    name: Mapped[str] = mapped_column(String(120))
    phone: Mapped[str | None] = mapped_column(String(24))
    email: Mapped[str | None] = mapped_column(String(254))
    # `metadata` is reserved on declarative classes, hence the attribute name.
    metadata_: Mapped[dict[str, Any]] = mapped_column("metadata", JsonType, default=dict)
    consent_status: Mapped[str] = mapped_column(String(16), server_default=ConsentStatus.UNKNOWN)
    preferred_language: Mapped[str | None] = mapped_column(String(16))
    # e.g. {"start": "10:00", "end": "18:00", "timezone": "Asia/Kolkata"}
    preferred_contact_time: Mapped[dict[str, Any] | None] = mapped_column(JsonType)
    status: Mapped[str] = mapped_column(String(16), server_default=ContactStatus.ACTIVE)

    organization: Mapped["Organization"] = relationship(back_populates="contacts")
    call_jobs: Mapped[list["JobRow"]] = relationship(back_populates="contact", overlaps="organization,agent,workflow,call_jobs")
