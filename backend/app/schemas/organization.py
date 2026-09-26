from datetime import datetime

from pydantic import Field

from app.models.enums import OrganizationStatus
from app.schemas.base import ResponseSchema, Schema


class OrganizationCreate(Schema):
    name: str = Field(min_length=1, max_length=120)
    industry: str | None = Field(default=None, max_length=64)


class OrganizationResponse(ResponseSchema):
    id: int
    name: str
    industry: str | None
    status: OrganizationStatus
    created_at: datetime
    updated_at: datetime
