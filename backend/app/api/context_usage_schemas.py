"""No prompt content is part of the public context-usage contract."""

from typing import Literal

from pydantic import BaseModel


class InputEstimate(BaseModel):
    input_bytes: int
    limit_bytes: int
    model_window_tokens: int
    output_reserved_tokens: int | None
    safety_reserved_tokens: int | None


class RequestUsage(BaseModel):
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None


class RequestMemory(BaseModel):
    history_turns: int | None
    summary_notes: int | None
    task_handoff: bool | None


class ContextUsageRecord(BaseModel):
    seq: int
    created_at: str
    phase: Literal["routing", "edit", "evidence", "decide", "extract", "summary"]
    status: Literal["started", "completed", "failed", "rejected", "other"]
    provider_called: bool | None
    estimate: InputEstimate
    usage: RequestUsage | None
    memory: RequestMemory | None
    summary_status: Literal["completed", "failed", "not_recorded"]


class ContextUsageOut(BaseModel):
    run_id: str
    case_id: str
    conversation_id: str
    snapshot_id: str
    revision: int
    attempt_id: str
    run_status: str
    record: ContextUsageRecord | None
