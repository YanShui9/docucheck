"""Finite, explicitly confirmed personal preferences; no arbitrary factual memory."""

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    field_validator,
)


class MemoryPreferenceIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: str = Field(min_length=1, max_length=32)
    expected_revision: int = Field(ge=0, strict=True)
    confirmed: StrictBool
    expires_at: AwareDatetime | None = None

    @field_validator("confirmed")
    @classmethod
    def explicit_confirmation(cls, value):
        if value is not True:
            raise ValueError("memory_confirmation_required")
        return value


class MemoryRecordOut(BaseModel):
    key: str
    value: str
    expires_at: str | None


class MemoryProfileOut(BaseModel):
    revision: int
    records: list[MemoryRecordOut]
