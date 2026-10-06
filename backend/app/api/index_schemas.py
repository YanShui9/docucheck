from pydantic import BaseModel, ConfigDict, Field


class IndexIn(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    expected_revision: int = Field(ge=1)
    retry_failed: bool = False


class DocumentIndexOut(BaseModel):
    document_id: str
    parse_status: str
    status: str
    attempts: int
    error_code: str | None
    expected_count: int
    ingested_count: int


class IndexesOut(BaseModel):
    case_id: str
    snapshot_id: str
    revision: int
    documents: list[DocumentIndexOut]


class IndexAccepted(BaseModel):
    scheduled: int
    worker_configured: bool
