"""Public request and response shapes for the first M3 API slice."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class MessageIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str = Field(min_length=1, max_length=10_000)
    mode: Literal["standard", "thorough"] = "standard"
    expected_revision: int = Field(ge=1)

    @field_validator("content")
    @classmethod
    def non_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("message_empty")
        return value


class RetryIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(ge=1)


class CaseOut(BaseModel):
    id: str
    revision: int
    created_at: str


class ConversationOut(BaseModel):
    id: str
    case_id: str
    created_at: str


class MessageAccepted(BaseModel):
    message_id: str
    run_id: str
    attempt_id: str


class AttemptAccepted(BaseModel):
    run_id: str
    attempt_id: str


class RunOut(BaseModel):
    id: str
    case_id: str
    conversation_id: str
    message_id: str
    snapshot_id: str
    revision: int
    mode: Literal["standard", "thorough"]
    status: str
    active_attempt_id: str
    answer_text: str | None
    error_code: str | None
    is_current: bool
    created_at: str
    updated_at: str


class EventOut(BaseModel):
    seq: int
    attempt_id: str
    kind: str
    status: str
    summary: dict
    created_at: str


class EventsOut(BaseModel):
    run_id: str
    events: list[EventOut]
    next_seq: int


class DocumentAccepted(BaseModel):
    document_id: str
    case_id: str
    snapshot_id: str
    revision: int
    parse_status: str
    source_sha256: str


class DocumentOut(BaseModel):
    id: str
    original_name: str
    role: str
    supplier_handle: str | None
    parse_status: str
    source_sha256: str


class DocumentsOut(BaseModel):
    case_id: str
    revision: int
    snapshot_id: str | None
    documents: list[DocumentOut]


class EvidenceOut(BaseModel):
    id: str
    document_id: str
    ordinal: int
    source_text: str
    page_idx: int | None
    bbox: list[float] | None
    page_width_pt: float | None
    page_height_pt: float | None
    sheet: str | None
    cell: str | None
    parser_version: str | None


class DocumentEvidenceOut(BaseModel):
    case_id: str
    snapshot_id: str
    document_id: str
    parse_status: str
    parser_version: str | None
    coverage_issues: list[str]
    evidence: list[EvidenceOut]
    next_ordinal: int
