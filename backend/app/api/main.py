"""First local FastAPI slice; start with uvicorn's --factory option."""

import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated
from uuid import uuid4

from fastapi import (
    APIRouter,
    FastAPI,
    File,
    Form,
    Header,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
)
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy import Engine, create_engine, select

from ..db.api_queries import ApiQueries, ApiQueryError
from ..db.connection import ROOT, database_url
from ..db.correction_repository import (
    CorrectionError,
    CorrectionRepository,
    SubmitCorrection,
)
from ..db.document_repository import DocumentError, DocumentRepository, UploadCommand
from ..db.ingestion import DocumentIngestion, IngestionError
from ..db.run_repository import RepositoryError, RunRepository, SubmitRun
from ..parsing import MAX_FILE_BYTES
from .schemas import (
    AttemptAccepted,
    CaseOut,
    ConversationOut,
    CorrectionAccepted,
    CorrectionIn,
    CorrectionOut,
    DocumentAccepted,
    DocumentEvidenceOut,
    DocumentsOut,
    EventsOut,
    EvidenceOut,
    MessageAccepted,
    MessageIn,
    ResumeIn,
    RetryIn,
    RunFactsOut,
    RunOut,
)
from .upload import UploadError, UploadStorage


def _error(request: Request, status: int, code: str, details: object = None):
    return JSONResponse(status_code=status, content={"error": {
        "code": code, "message": code, "details": details or {},
        "request_id": request.state.request_id,
    }})


def _domain_status(code: str) -> int:
    if code in {"not_found", "run_not_found", "scope_mismatch"}:
        return 404
    if code in {"snapshot_not_ready", "revision_conflict", "snapshot_changed",
                "idempotency_conflict", "stale_attempt", "invalid_transition",
                "supplier_conflict", "conversation_busy"}:
        return 409
    if code in {"case_busy", "source_run_not_current", "facts_unavailable",
                "memory_scope", "snapshot_capacity",
                "correction_pending_recheck"}:
        return 409
    if code == "fact_not_found":
        return 404
    if code in {"unsupported_correction", "correction_unchanged"}:
        return 422
    if code == "file_limit":
        return 413
    if code in {"invalid_file", "invalid_filename", "unsupported_file",
                "invalid_supplier_binding", "invalid_role", "case_file_limit",
                "case_supplier_limit", "case_page_limit"}:
        return 422
    if code == "invalid_request":
        return 400
    return 500


def create_app(engine: Engine | None = None) -> FastAPI:
    owns_engine = engine is None
    if engine is None:
        engine = create_engine(database_url(), pool_pre_ping=True,
                               hide_parameters=True)
    queries, runs, documents = ApiQueries(engine), RunRepository(engine), DocumentRepository(engine)
    corrections = CorrectionRepository(engine)
    storage = UploadStorage(Path(os.getenv("DOCUCHECK_DATA_DIR", str(ROOT / "data"))))
    ingestion = DocumentIngestion(engine, storage.root)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        try:
            yield
        finally:
            if owns_engine:
                engine.dispose()

    app = FastAPI(title="DocuCheck API", version="0.1.0", lifespan=lifespan)

    @app.middleware("http")
    async def request_id(request: Request, call_next):
        request.state.request_id = uuid4().hex
        if (request.method == "POST" and request.url.path.startswith("/api/v1/cases/")
                and request.url.path.endswith("/documents")):
            length = request.headers.get("content-length", "")
            if length.isdecimal() and (len(length) > 20 or
                                       int(length) > MAX_FILE_BYTES + 64 * 1024):
                response = _error(request, 413, "file_limit")
                response.headers["X-Request-ID"] = request.state.request_id
                return response
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        return response

    @app.exception_handler(ApiQueryError)
    @app.exception_handler(RepositoryError)
    @app.exception_handler(DocumentError)
    @app.exception_handler(UploadError)
    @app.exception_handler(IngestionError)
    @app.exception_handler(CorrectionError)
    async def domain_error(request: Request, exc: ApiQueryError | RepositoryError | DocumentError | UploadError | IngestionError | CorrectionError):
        return _error(request, _domain_status(exc.code), exc.code)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        details = [{"location": list(item["loc"]), "type": item["type"]}
                   for item in exc.errors()]
        return _error(request, 422, "validation_error", details)

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        code = ({404: "not_found", 503: "service_unavailable"}
                .get(exc.status_code, "http_error"))
        return _error(request, exc.status_code, code)

    @app.exception_handler(Exception)
    async def internal_error(request: Request, exc: Exception):
        return _error(request, 500, "internal_error")

    @app.get("/health/live")
    def live():
        return {"status": "ok"}

    @app.get("/health/ready")
    def ready():
        try:
            with engine.connect() as connection:
                connection.scalar(select(1))
        except Exception as exc:
            raise HTTPException(status_code=503) from exc
        return {"status": "ok"}

    router = APIRouter(prefix="/api/v1")

    @router.post("/cases", status_code=201, response_model=CaseOut)
    def create_case():
        return queries.create_case()

    @router.post("/cases/{case_id}/documents", status_code=201,
                 response_model=DocumentAccepted)
    def upload_document(case_id: str,
                        file: Annotated[UploadFile, File()],
                        role: Annotated[str, Form()],
                        expected_revision: Annotated[int, Form(ge=0)],
                        supplier_handle: Annotated[str | None, Form()] = None,
                        supplier_name: Annotated[str | None, Form()] = None,
                        idempotency_key: str = Header(
                            alias="Idempotency-Key", min_length=1, max_length=255,
                        )):
        saved = storage.save(file)
        try:
            receipt = documents.upload(UploadCommand(
                case_id=case_id, expected_revision=expected_revision,
                role=role, supplier_handle=supplier_handle,
                supplier_name=supplier_name,
                idempotency_key=idempotency_key,
                document_id=saved.document_id,
                original_name=saved.original_name,
                storage_key=saved.storage_key,
                source_sha256=saved.source_sha256,
                media_type=saved.media_type, pdf_pages=saved.pdf_pages,
            ))
        except Exception:
            storage.discard(saved)
            raise
        if receipt["document_id"] != saved.document_id:
            storage.discard(saved)
        return receipt

    @router.get("/cases/{case_id}/documents", response_model=DocumentsOut)
    def list_documents(case_id: str):
        return documents.list_current(case_id)

    @router.post("/cases/{case_id}/corrections", status_code=201,
                 response_model=CorrectionAccepted)
    def submit_correction(case_id: str, body: CorrectionIn,
                          idempotency_key: str = Header(
                              alias="Idempotency-Key", min_length=1, max_length=255,
                          )):
        return corrections.submit(SubmitCorrection(
            case_id=case_id, idempotency_key=idempotency_key,
            **body.model_dump(),
        ))

    @router.get("/cases/{case_id}/corrections/{correction_id}",
                response_model=CorrectionOut)
    def get_correction(case_id: str, correction_id: str):
        return corrections.get_correction(case_id, correction_id)

    @router.get("/cases/{case_id}/snapshots/{snapshot_id}/documents/"
                "{document_id}/evidence", response_model=DocumentEvidenceOut)
    def list_document_evidence(case_id: str, snapshot_id: str,
                               document_id: str,
                               after_ordinal: int = Query(default=-1, ge=-1),
                               limit: int = Query(default=100, ge=1, le=100)):
        return ingestion.list_evidence(case_id, snapshot_id, document_id,
                                       after_ordinal, limit)

    @router.get("/cases/{case_id}/snapshots/{snapshot_id}/evidence/"
                "{evidence_id}", response_model=EvidenceOut)
    def read_evidence(case_id: str, snapshot_id: str, evidence_id: str):
        return ingestion.read_evidence(case_id, snapshot_id, evidence_id)

    @router.post("/cases/{case_id}/conversations", status_code=201,
                 response_model=ConversationOut)
    def create_conversation(case_id: str):
        return queries.create_conversation(case_id)

    @router.post("/conversations/{conversation_id}/messages", status_code=202,
                 response_model=MessageAccepted)
    def submit_message(conversation_id: str, body: MessageIn, response: Response,
                       idempotency_key: str = Header(
                           alias="Idempotency-Key", min_length=1, max_length=255,
                       )):
        case_id, snapshot_id = queries.snapshot_for_conversation(
            conversation_id, body.expected_revision,
        )
        receipt = runs.submit(SubmitRun(
            case_id=case_id, conversation_id=conversation_id,
            snapshot_id=snapshot_id, expected_revision=body.expected_revision,
            content=body.content, mode=body.mode,
            idempotency_key=idempotency_key,
        ))
        response.headers["Location"] = f"/api/v1/agent-runs/{receipt.run_id}"
        return {"message_id": receipt.message_id, "run_id": receipt.run_id,
                "attempt_id": receipt.attempt_id}

    @router.get("/agent-runs/{run_id}", response_model=RunOut)
    def get_run(run_id: str):
        return queries.get_run(run_id)

    @router.get("/agent-runs/{run_id}/facts", response_model=RunFactsOut)
    def get_run_facts(run_id: str):
        return corrections.get_run_facts(run_id)

    @router.get("/agent-runs/{run_id}/events", response_model=EventsOut)
    def get_events(run_id: str, after_seq: int = Query(default=0, ge=0)):
        return queries.events(run_id, after_seq)

    @router.post("/agent-runs/{run_id}/cancel", response_model=RunOut)
    def cancel_run(run_id: str):
        runs.cancel(run_id)
        return queries.get_run(run_id)

    @router.post("/agent-runs/{run_id}/retry", status_code=202,
                 response_model=AttemptAccepted)
    def retry_run(run_id: str, body: RetryIn, response: Response,
                  idempotency_key: str = Header(
                      alias="Idempotency-Key", min_length=1, max_length=255,
                  )):
        run = queries.get_run(run_id)
        receipt = runs.retry(run_id, run["snapshot_id"], body.expected_revision,
                             idempotency_key)
        response.headers["Location"] = f"/api/v1/agent-runs/{run_id}"
        return {"run_id": receipt.run_id, "attempt_id": receipt.attempt_id}

    @router.post("/agent-runs/{run_id}/resume", status_code=202,
                 response_model=AttemptAccepted)
    def resume_run(run_id: str, body: ResumeIn, response: Response,
                   idempotency_key: str = Header(
                       alias="Idempotency-Key", min_length=1, max_length=255,
                   )):
        run = queries.get_run(run_id)
        receipt = runs.resume(run_id, run["snapshot_id"], body.expected_revision,
                              idempotency_key, body.content)
        response.headers["Location"] = f"/api/v1/agent-runs/{run_id}"
        return {"run_id": receipt.run_id, "attempt_id": receipt.attempt_id}

    app.include_router(router)
    return app
