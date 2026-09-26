from typing import Annotated, ClassVar

from pydantic import BaseModel, ConfigDict, Field, model_validator

# One short entry of a list such as an agent's target users or tasks.
Label = Annotated[str, Field(min_length=1, max_length=60)]


class Schema(BaseModel):
    """Input schemas reject unknown fields, so a typo (or an attempt to set organization_id,
    which the server decides) is an error rather than silently ignored."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class UpdateSchema(Schema):
    """Every field optional: only the fields sent are changed (`model_dump(exclude_unset=True)`)."""

    # Fields whose column is NOT NULL: they may be omitted but not set to null.
    required_fields: ClassVar[frozenset[str]] = frozenset()

    @model_validator(mode="after")
    def reject_null_for_required(self) -> "UpdateSchema":
        for name in self.model_fields_set & self.required_fields:
            if getattr(self, name) is None:
                raise ValueError(f"{name} cannot be null")

        return self


class ResponseSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True)
