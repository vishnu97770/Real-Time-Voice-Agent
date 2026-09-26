from datetime import datetime
from typing import Any

from pydantic import AliasChoices, Field

from app.models.enums import ConsentStatus, ContactStatus
from app.schemas.base import ResponseSchema, Schema, UpdateSchema


class ContactCreate(Schema):
    """`metadata` is where anything domain-specific goes (an appointment, a policy number). On the
    ORM model the attribute is `metadata_`, because `metadata` is reserved by SQLAlchemy."""

    name: str = Field(min_length=1, max_length=120)
    phone: str | None = Field(default=None, max_length=24)
    email: str | None = Field(default=None, max_length=254)
    metadata: dict[str, Any] = Field(default_factory=dict)
    consent_status: ConsentStatus = ConsentStatus.UNKNOWN
    preferred_language: str | None = Field(default=None, max_length=16)
    preferred_contact_time: dict[str, Any] | None = None
    status: ContactStatus = ContactStatus.ACTIVE


class ContactUpdate(UpdateSchema):
    required_fields = frozenset({"name", "metadata", "consent_status", "status"})

    name: str | None = Field(default=None, min_length=1, max_length=120)
    phone: str | None = Field(default=None, max_length=24)
    email: str | None = Field(default=None, max_length=254)
    metadata: dict[str, Any] | None = None
    consent_status: ConsentStatus | None = None
    preferred_language: str | None = Field(default=None, max_length=16)
    preferred_contact_time: dict[str, Any] | None = None
    status: ContactStatus | None = None


class ContactResponse(ResponseSchema):
    id: int
    organization_id: int
    name: str
    phone: str | None
    email: str | None
    # Read from the ORM's `metadata_`, written out as `metadata`.
    metadata: dict[str, Any] = Field(
        validation_alias=AliasChoices("metadata_", "metadata"), serialization_alias="metadata"
    )
    consent_status: ConsentStatus
    preferred_language: str | None
    preferred_contact_time: dict[str, Any] | None
    status: ContactStatus
    created_at: datetime
    updated_at: datetime
