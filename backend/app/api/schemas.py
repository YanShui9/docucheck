"""Public request and response shapes for the first M3 API slice."""

from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_serializer,
    model_validator,
)


class DynamicCompatibleOut(BaseModel):
    @model_serializer(mode="wrap")
    def omit_absent_dynamic_fields(self, handler):
        output = handler(self)
        for key in (
            "criterion_id",
            "item_id",
            "item_name",
            "criterion_name",
            "checklist_version_id",
            "workflow",
            "payload_digest",
        ):
            if output.get(key) is None:
                output.pop(key, None)
        return output


class MessageIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str = Field(min_length=1, max_length=2_000)
    mode: Literal["standard", "thorough"] = "standard"
    expected_revision: int = Field(ge=0)
    operation: Literal["review", "propose_checklist", "assistant"] = "review"

    @model_validator(mode="after")
    def revision_scope(self):
        if self.expected_revision == 0 and self.operation != "assistant":
            raise ValueError("revision_zero_requires_assistant")
        return self

    @field_validator("content")
    @classmethod
    def non_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("message_empty")
        return value


class RetryIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(ge=0)


class ResumeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str = Field(min_length=1, max_length=2000)
    expected_revision: int = Field(ge=0)

    @field_validator("content")
    @classmethod
    def non_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("message_empty")
        return value


class CaseOut(BaseModel):
    id: str
    project_id: str | None = None
    revision: int
    created_at: str


class CaseCreateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: str = Field(min_length=32, max_length=32)


class ProjectIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)


class MemberIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subject: str = Field(min_length=1, max_length=255)
    role: Literal["owner", "editor", "viewer"]


class CaseDeletionOut(BaseModel):
    case_id: str
    revision: int
    status: Literal["active", "deleting", "deleted"]
    requested_at: str | None
    completed_at: str | None = None
    error_code: str | None = None


class ConversationOut(BaseModel):
    id: str
    case_id: str
    created_at: str


class ConversationsOut(BaseModel):
    case_id: str
    conversations: list[ConversationOut]
    next_offset: int | None


class StoredMessageOut(BaseModel):
    id: str
    role: Literal["user", "assistant"]
    content: str
    run_id: str | None
    created_at: str


class MessagesOut(BaseModel):
    conversation_id: str
    total: int
    messages: list[StoredMessageOut]
    next_offset: int | None


class MessageAccepted(BaseModel):
    message_id: str
    run_id: str
    attempt_id: str


class AttemptAccepted(BaseModel):
    run_id: str
    attempt_id: str


class RunOut(DynamicCompatibleOut):
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
    outcome_unknown: bool
    model_id: str | None = None
    usage: dict[str, int] | None = None
    is_current: bool
    checklist_version_id: str | None = None
    workflow: str | None = None
    correction_id: str | None = None
    outgoing_correction_id: str | None = None
    correction_review_status: Literal["complete", "needs_review"] | None = None
    created_at: str
    updated_at: str


class RunSummaryOut(BaseModel):
    id: str
    conversation_id: str
    snapshot_id: str
    revision: int
    mode: Literal["standard", "thorough"]
    status: str
    question: str
    answer_text: str | None
    error_code: str | None
    created_at: str
    updated_at: str


class CaseRunsOut(BaseModel):
    case_id: str
    runs: list[RunSummaryOut]
    next_offset: int | None


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
    supplier_name: str | None = None
    model_name: str | None = None
    parse_status: str
    source_sha256: str


class DocumentsOut(BaseModel):
    case_id: str
    revision: int
    snapshot_id: str | None
    documents: list[DocumentOut]


class SnapshotOut(BaseModel):
    id: str
    revision: int
    created_at: str
    document_count: int


class SnapshotsOut(BaseModel):
    case_id: str
    snapshots: list[SnapshotOut]
    next_before_revision: int | None


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


class SheetCellOut(BaseModel):
    address: str
    text: str
    highlight: bool


class SheetRowOut(BaseModel):
    number: int
    cells: list[SheetCellOut]


class SheetWindowOut(BaseModel):
    sheet: str
    cell: str
    rows: list[SheetRowOut]


class CorrectionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_run_id: str = Field(min_length=1, max_length=32)
    fact_set_id: str = Field(min_length=1, max_length=64)
    fact_id: str = Field(min_length=1, max_length=64)
    raw_value: str = Field(pattern=r"^\d+(\.\d+)?$", max_length=20)
    raw_unit: str = Field(min_length=1, max_length=30)
    reason: str = Field(min_length=1, max_length=2000)
    expected_revision: int = Field(ge=1)

    @field_validator("reason")
    @classmethod
    def reason_non_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("reason_empty")
        return value


class CorrectionAccepted(BaseModel):
    correction_id: str
    case_id: str
    source_run_id: str
    from_snapshot_id: str
    snapshot_id: str
    revision: int
    review_status: Literal["pending_recheck"]


class CorrectionOut(CorrectionAccepted):
    review_status: Literal["pending_recheck", "complete", "needs_review"]
    fact_set_id: str
    fact_id: str
    field_key: str
    supplier: str
    old_fact: dict
    corrected_candidate: dict
    source_evidence_ids: list[str]
    target_evidence_ids: list[str]
    reason: str
    created_at: str
    corrected_fact: dict | None = None
    check_result: dict | None = None


class RunFactsOut(DynamicCompatibleOut):
    run_id: str
    case_id: str
    snapshot_id: str
    revision: int
    is_current: bool
    available: bool
    fact_sets: list[dict]
    checklist_version_id: str | None = None
    payload_digest: str | None = None


class CheckOut(DynamicCompatibleOut):
    check_result_id: str
    snapshot_id: str
    supplier: str
    field_key: str
    processing_status: str
    business_status: str | None
    reason_code: str
    evidence_refs: list[str]
    reason_evidence_refs: list[str]
    fact_ids: list[str]
    rule_version: str
    is_formal: bool
    criterion_id: str | None = None
    item_id: str | None = None
    item_name: str | None = None
    criterion_name: str | None = None
    checklist_version_id: str | None = None


class RunChecksOut(DynamicCompatibleOut):
    run_id: str
    case_id: str
    snapshot_id: str
    revision: int
    run_status: str
    is_current: bool
    checks: list[CheckOut]
    checklist_version_id: str | None = None
