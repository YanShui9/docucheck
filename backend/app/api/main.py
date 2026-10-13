"""First local FastAPI slice; start with uvicorn's --factory option."""

import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Literal
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
from starlette.exceptions import HTTPException as StarletteHTTPException

from ..db.api_queries import ApiQueries, ApiQueryError
from ..db.case_deletion import CaseDeletion, CaseDeletionError
from ..db.checklist_repository import ChecklistError
from ..db.connection import ROOT, database_url
from ..db.correction_recheck import CorrectionRecheck
from ..db.correction_repository import (
    CorrectionError,
    CorrectionRepository,
    SubmitCorrection,
)
from ..db.document_repository import DocumentError, DocumentRepository, UploadCommand
from ..db.index_repository import IndexError, IndexRepository
from ..db.ingestion import DocumentIngestion, IngestionError
from ..db.item_binding_repository import ItemBindingError
from ..db.memory_repository import MemoryError, MemoryRepository
from ..db.models import cases, conversations
from ..db.run_repository import RepositoryError, RunRepository, SubmitRun
from ..db.upload_intents import UploadIntents
from ..db.workspace import WorkspaceAccessError, WorkspaceRepository
from ..vector_store.target import configured_target
from .auth import AuthenticationError, Principal, authenticator_from_env
from .body_limit import MAX_UPLOAD_BODY_BYTES, UploadBodyLimitMiddleware
from .checklists import checklist_router
from .dynamic_schemas import DynamicCorrectionIn
from .index_schemas import IndexAccepted, IndexesOut, IndexIn
from .item_bindings import item_binding_router
from .memory_schemas import MemoryPreferenceIn, MemoryProfileOut
from .route_auth import AuthorizedRoute
from .run_export import export_payload, render_json, render_xlsx
from .schemas import (
    AttemptAccepted,
    CaseCreateIn,
    CaseDeletionOut,
    CaseOut,
    CaseRunsOut,
    ConversationOut,
    ConversationsOut,
    CorrectionAccepted,
    CorrectionIn,
    CorrectionOut,
    DocumentAccepted,
    DocumentEvidenceOut,
    DocumentsOut,
    EventsOut,
    EvidenceOut,
    MemberIn,
    MessageAccepted,
    MessageIn,
    MessagesOut,
    ProjectIn,
    ResumeIn,
    RetryIn,
    RunChecksOut,
    RunFactsOut,
    RunOut,
    SheetWindowOut,
    SnapshotsOut,
)
from .upload import UploadError, UploadStorage


def _error(request: Request, status: int, code: str, details: object = None):
    return JSONResponse(status_code=status, content={"error": {
        "code": code, "message": code, "details": details or {},
        "request_id": request.state.request_id,
    }})


def _domain_status(code: str) -> int:
    if code in {"item_binding_checklist_changed", "item_binding_revision_conflict", "item_binding_source_changed"}:
        return 409
    if code in {"item_binding_invalid", "item_binding_confirmation_required", "item_binding_source", "item_binding_range"}:
        return 422
    if code == "item_binding_capacity":
        return 413
    if code == "assistant_preview_limit":
        return 409
    if code in {"checklist_revision_conflict", "checklist_snapshot_changed", "checklist_source_changed", "checklist_execution_pending", "checklist_draft_missing", "checklist_changed", "batch_source_changed", "dynamic_source_changed", "assistant_source_changed", "assistant_scope"}:
        return 409
    if code in {"invalid_checklist", "invalid_checklist_selection", "checklist_confirmation_required", "checklist_needs_resolution", "checklist_source_scope", "checklist_rebase_unresolved"}:
        return 422
    if code == "checklist_capacity":
        return 413
    if code in {"not_found", "run_not_found", "scope_mismatch"}:
        return 404
    if code in {"snapshot_not_ready", "revision_conflict", "snapshot_changed",
                "index_snapshot_changed", "stale_index_claim",
                "idempotency_conflict", "stale_attempt", "invalid_transition",
                "supplier_conflict", "conversation_busy", "dynamic_scope", "upload_expired"}:
        return 409
    if code in {"case_busy", "source_run_not_current", "facts_unavailable",
                "memory_scope", "run_changed", "snapshot_capacity",
                "checks_unavailable", "xlsx_cell_limit",
                "correction_pending_recheck", "correction_source_changed",
                "correction_recheck_capacity", "case_deleting",
                "source_missing", "source_hash_mismatch", "source_limit",
                "source_type_mismatch"}:
        return 409
    if code == "fact_not_found":
        return 404
    if code == "case_deleted":
        return 410
    if code in {"unsupported_correction", "correction_unchanged"}:
        return 422
    if code == "file_limit":
        return 413
    if code in {"dynamic_capacity", "assistant_capacity"}:
        return 413
    if code in {"invalid_file", "invalid_filename", "unsupported_file",
                "invalid_cell",
                "invalid_supplier_binding", "invalid_role", "case_file_limit",
                "case_supplier_limit", "case_page_limit"}:
        return 422
    if code == "invalid_request":
        return 400
    if code == "index_target_configuration":
        return 503
    if code in {"memory_revision_conflict", "memory_snapshot_changed", "memory_source_changed", "memory_context_changed"}:
        return 409
    if code in {"invalid_memory_preference", "invalid_memory_expiration", "memory_confirmation_required"}:
        return 422
    return 500


def create_app(engine: Engine | None = None, authenticator=None) -> FastAPI:
    owns_engine = engine is None
    if engine is None:
        engine = create_engine(database_url(), pool_pre_ping=True,
                               hide_parameters=True)
    queries, runs, documents = ApiQueries(engine), RunRepository(engine), DocumentRepository(engine)
    corrections = CorrectionRepository(engine)
    rechecks = CorrectionRecheck(engine)
    deletion = CaseDeletion(engine)
    indexes = IndexRepository(engine)
    storage = UploadStorage(Path(os.getenv("DOCUCHECK_DATA_DIR", str(ROOT / "data"))))
    uploads = UploadIntents(engine, storage.root)
    ingestion = DocumentIngestion(engine, storage.root)
    workspace = WorkspaceRepository(engine)
    authenticator = authenticator or authenticator_from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        try:
            yield
        finally:
            if owns_engine:
                engine.dispose()

    app = FastAPI(title="DocuCheck API", version="0.1.0", lifespan=lifespan)
    app.state.upload_intents = uploads
    app.state.workspace = workspace
    app.state.authenticator = authenticator
    app.add_middleware(UploadBodyLimitMiddleware)

    @app.middleware("http")
    async def request_id(request: Request, call_next):
        request.state.request_id = uuid4().hex
        if (not authenticator.enabled and
                request.url.path.startswith("/api/v1/") and
                (request.client is None or request.client.host not in {
                    "127.0.0.1", "::1", "testclient",
                })):
            response = _error(request, 403, "local_mode_loopback_only")
            response.headers["X-Request-ID"] = request.state.request_id
            return response
        if (request.method == "POST" and request.url.path.startswith("/api/v1/cases/")
                and request.url.path.endswith("/documents")):
            length = request.headers.get("content-length", "")
            if length.isdecimal() and (len(length) > 20 or
                                       int(length) > MAX_UPLOAD_BODY_BYTES):
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
    @app.exception_handler(CaseDeletionError)
    @app.exception_handler(IndexError)
    @app.exception_handler(MemoryError)
    @app.exception_handler(ChecklistError)
    @app.exception_handler(ItemBindingError)
    async def domain_error(request: Request, exc: ApiQueryError | RepositoryError | DocumentError | UploadError | IngestionError | CorrectionError | CaseDeletionError | IndexError):
        return _error(request, _domain_status(exc.code), exc.code)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        details = [{"location": list(item["loc"]), "type": item["type"]}
                   for item in exc.errors()]
        return _error(request, 422, "validation_error", details)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException):
        code = ({404: "not_found", 413: "file_limit", 503: "service_unavailable"}
                .get(exc.status_code, "http_error"))
        return _error(request, exc.status_code, code)

    @app.exception_handler(Exception)
    async def internal_error(request: Request, exc: Exception):
        return _error(request, 500, "internal_error")

    @app.exception_handler(AuthenticationError)
    async def authentication_error(request: Request, exc: AuthenticationError):
        response = _error(request, 401, exc.code)
        response.headers["WWW-Authenticate"] = "Bearer"
        return response

    @app.exception_handler(WorkspaceAccessError)
    async def workspace_error(request: Request, exc: WorkspaceAccessError):
        status = (422 if exc.code in {"invalid_project_name", "invalid_role"}
                  else 409 if exc.code == "last_project_owner" else 404)
        return _error(request, status, exc.code)

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

    router = APIRouter(prefix="/api/v1", route_class=AuthorizedRoute)

    @router.get("/me")
    def me(request: Request):
        principal = (request.state.principal if authenticator.enabled
                     else Principal("local", "local"))
        return {"subject": principal.subject, "name": principal.name}

    def memory_actor(request: Request) -> str:
        return (request.state.principal if authenticator.enabled else Principal("local", "local")).key

    @router.get("/me/memories", response_model=MemoryProfileOut)
    def personal_memories(request: Request):
        return MemoryRepository(engine).profile(memory_actor(request))

    @router.put("/me/memories/{key}", response_model=MemoryProfileOut)
    def save_personal_memory(key: str, body: MemoryPreferenceIn, request: Request):
        return MemoryRepository(engine).put(memory_actor(request), key, body.value,
            body.expected_revision, confirmed=body.confirmed, expires_at=body.expires_at)

    @router.delete("/me/memories/{key}", response_model=MemoryProfileOut)
    def delete_personal_memory(key: str, request: Request,
                               expected_revision: Annotated[int, Query(ge=0)]):
        return MemoryRepository(engine).delete(memory_actor(request), key, expected_revision)

    @router.get("/conversations/{conversation_id}/memory")
    def conversation_memory(conversation_id: str):
        with engine.connect() as connection:
            revision = connection.scalar(select(cases.c.revision).join(conversations,
                conversations.c.case_id == cases.c.id).where(conversations.c.id == conversation_id))
        if revision is None:
            raise ApiQueryError("not_found")
        case_id, snapshot_id = queries.snapshot_for_conversation(conversation_id, revision)
        memory = MemoryRepository(engine).conversation(case_id, conversation_id, snapshot_id)
        task = memory["task"]
        return {"case_id": case_id, "conversation_id": conversation_id, "snapshot_id": snapshot_id,
                "task": {k: v for k, v in task.items() if k != "memory"} if task else None,
                "summary": memory["summary"]}

    @router.post("/projects", status_code=201)
    def create_project(body: ProjectIn, request: Request):
        if not authenticator.enabled:
            raise AuthenticationError("oidc_required")
        return workspace.create_project(request.state.principal, body.name)

    @router.get("/projects")
    def list_projects(request: Request):
        if not authenticator.enabled:
            raise AuthenticationError("oidc_required")
        return {"projects": workspace.list_projects(request.state.principal)}

    @router.post("/projects/{project_id}/members", status_code=201)
    def add_project_member(project_id: str, body: MemberIn,
                           request: Request):
        actor = request.state.principal
        return workspace.add_member(project_id, actor,
                                    Principal(actor.issuer, body.subject),
                                    body.role)

    @router.get("/projects/{project_id}/cases")
    def list_project_cases(project_id: str, request: Request):
        return {"project_id": project_id,
                "cases": workspace.list_cases(project_id,
                                               request.state.principal)}

    @router.post("/cases", status_code=201, response_model=CaseOut)
    def create_case(request: Request, body: CaseCreateIn | None = None):
        if authenticator.enabled:
            if body is None:
                raise HTTPException(status_code=422)
            return workspace.create_case(body.project_id,
                                         request.state.principal)
        return queries.create_case()

    @router.get("/cases/{case_id}")
    def get_case(case_id: str, request: Request):
        if authenticator.enabled:
            return workspace.get_case(case_id, request.state.principal)
        with engine.connect() as connection:
            from ..db.models import cases
            row = connection.execute(select(cases).where(
                cases.c.id == case_id,
            )).mappings().first()
        if row is None:
            raise HTTPException(status_code=404)
        return {"id": case_id, "project_id": row["project_id"],
                "revision": row["revision"],
                "created_at": row["created_at"].isoformat(),
                "updated_at": row["updated_at"].isoformat()}

    @router.delete("/cases/{case_id}", status_code=202,
                   response_model=CaseDeletionOut)
    def request_case_deletion(case_id: str,
                              response: Response,
                              expected_revision: int = Query(ge=0)):
        receipt = deletion.request(case_id, expected_revision)
        if receipt["status"] == "deleted":
            response.status_code = 200
        return receipt

    @router.get("/cases/{case_id}/deletion", response_model=CaseDeletionOut)
    def get_case_deletion(case_id: str):
        return deletion.get(case_id)

    @router.post("/cases/{case_id}/documents", status_code=201,
                 response_model=DocumentAccepted)
    def upload_document(case_id: str,
                        file: Annotated[UploadFile, File()],
                        role: Annotated[str, Form()],
                        expected_revision: Annotated[int, Form(ge=0)],
                        supplier_handle: Annotated[str | None, Form()] = None,
                        supplier_name: Annotated[str | None, Form()] = None,
                        model_name: Annotated[str | None, Form()] = None,
                        idempotency_key: str = Header(
                            alias="Idempotency-Key", min_length=1, max_length=255,
                        )):
        prepared = storage.prepare(file)
        intent = uploads.create(prepared.document_id, prepared.storage_key)
        with uploads.transaction(intent) as connection:
            saved = None
            try:
                saved = storage.save(file, prepared=prepared)
                uploads.require_owner(connection, intent)
                receipt = documents.upload(UploadCommand(
                    case_id=case_id, expected_revision=expected_revision,
                    role=role, supplier_handle=supplier_handle,
                    supplier_name=supplier_name, model_name=model_name,
                    idempotency_key=idempotency_key,
                    document_id=saved.document_id, original_name=saved.original_name,
                    storage_key=saved.storage_key, source_sha256=saved.source_sha256,
                    media_type=saved.media_type, pdf_pages=saved.pdf_pages,
                ), transaction=connection)
                if receipt["document_id"] != saved.document_id:
                    storage.discard(saved)
                uploads.complete(connection, intent)
            except Exception:
                if saved is not None:
                    storage.discard(saved)
                raise
        # Commit errors can have an unknown outcome. Never delete the file
        # outside its transaction; the durable intent/ref decides recovery.
        return receipt

    @router.get("/cases/{case_id}/documents", response_model=DocumentsOut)
    def list_documents(case_id: str):
        return documents.list_current(case_id)

    @router.get("/cases/{case_id}/snapshots", response_model=SnapshotsOut)
    def list_snapshots(case_id: str,
                       before_revision: int | None = Query(default=None, ge=1),
                       limit: int = Query(default=50, ge=1, le=50)):
        return documents.list_snapshots(case_id, before_revision=before_revision,
                                        limit=limit)

    @router.get("/cases/{case_id}/snapshots/{snapshot_id}/documents",
                response_model=DocumentsOut)
    def list_snapshot_documents(case_id: str, snapshot_id: str):
        return documents.list_snapshot(case_id, snapshot_id)

    @router.get("/cases/{case_id}/snapshots/{snapshot_id}/indexes", response_model=IndexesOut)
    def get_indexes(case_id: str, snapshot_id: str):
        return indexes.status(case_id, snapshot_id)

    @router.post("/cases/{case_id}/snapshots/{snapshot_id}/indexes", status_code=202,
                 response_model=IndexAccepted)
    def schedule_indexes(case_id: str, snapshot_id: str, body: IndexIn):
        try:
            target = configured_target()
        except ValueError:
            raise IndexError("index_target_configuration") from None
        count = indexes.schedule(case_id, snapshot_id, body.expected_revision,
                                 target=target, retry_failed=body.retry_failed)
        return {"scheduled": count, "worker_configured": target is not None}

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

    @router.post("/cases/{case_id}/corrections/{correction_id}/recheck",
                 response_model=CorrectionOut)
    def recheck_correction(case_id: str, correction_id: str):
        return rechecks.recheck(case_id, correction_id)

    @router.get("/cases/{case_id}/snapshots/{snapshot_id}/documents/"
                "{document_id}/evidence", response_model=DocumentEvidenceOut)
    def list_document_evidence(case_id: str, snapshot_id: str,
                               document_id: str,
                               after_ordinal: int = Query(default=-1, ge=-1),
                               limit: int = Query(default=100, ge=1, le=100)):
        return ingestion.list_evidence(case_id, snapshot_id, document_id,
                                       after_ordinal, limit)

    @router.get("/cases/{case_id}/snapshots/{snapshot_id}/documents/"
                "{document_id}/original")
    def read_original(case_id: str, snapshot_id: str, document_id: str):
        content, media_type = ingestion.original(case_id, snapshot_id, document_id)
        return Response(content=content, media_type=media_type, headers={
            "Cache-Control": "no-store",
            "Content-Disposition": "attachment",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "sandbox",
        })

    @router.get("/cases/{case_id}/snapshots/{snapshot_id}/documents/"
                "{document_id}/original/sheet-window", response_model=SheetWindowOut)
    def read_sheet_window(case_id: str, snapshot_id: str, document_id: str,
                          sheet: str = Query(min_length=1, max_length=31),
                          cell: str = Query(min_length=2, max_length=6)):
        return ingestion.sheet_window(case_id, snapshot_id, document_id,
                                      sheet, cell)

    @router.get("/cases/{case_id}/snapshots/{snapshot_id}/evidence/"
                "{evidence_id}", response_model=EvidenceOut)
    def read_evidence(case_id: str, snapshot_id: str, evidence_id: str):
        return ingestion.read_evidence(case_id, snapshot_id, evidence_id)

    @router.post("/cases/{case_id}/conversations", status_code=201,
                 response_model=ConversationOut)
    def create_conversation(case_id: str):
        return queries.create_conversation(case_id)

    @router.get("/cases/{case_id}/runs", response_model=CaseRunsOut)
    def list_case_runs(case_id: str,
                       snapshot_id: str | None = Query(default=None, max_length=64),
                       offset: int = Query(default=0, ge=0),
                       limit: int = Query(default=50, ge=1, le=100)):
        return queries.list_case_runs(case_id, snapshot_id, offset, limit)

    @router.get("/cases/{case_id}/conversations", response_model=ConversationsOut)
    def list_conversations(case_id: str,
                           offset: int = Query(default=0, ge=0),
                           limit: int = Query(default=50, ge=1, le=100)):
        return queries.list_conversations(case_id, offset, limit)

    @router.get("/conversations/{conversation_id}/messages",
                response_model=MessagesOut)
    def list_messages(conversation_id: str,
                      offset: int = Query(default=0, ge=0),
                      limit: int = Query(default=50, ge=1, le=100)):
        return queries.list_messages(conversation_id, offset, limit)

    @router.post("/conversations/{conversation_id}/messages", status_code=202,
                 response_model=MessageAccepted)
    def submit_message(conversation_id: str, body: MessageIn, response: Response, request: Request,
                       idempotency_key: str = Header(
                           alias="Idempotency-Key", min_length=1, max_length=255,
                       )):
        case_id, snapshot_id = queries.snapshot_for_conversation(
            conversation_id, body.expected_revision,
            allow_empty=body.operation == "assistant",
        )
        receipt = runs.submit(SubmitRun(
            case_id=case_id, conversation_id=conversation_id,
            snapshot_id=snapshot_id, expected_revision=body.expected_revision,
            content=body.content, mode=body.mode,
            idempotency_key=idempotency_key,
            requester_key=request.state.principal.key if authenticator.enabled else None,
            workflow=("assistant" if body.operation == "assistant" else
                      "proposal" if body.operation == "propose_checklist" else "auto"),
        ))
        response.headers["Location"] = f"/api/v1/agent-runs/{receipt.run_id}"
        return {"message_id": receipt.message_id, "run_id": receipt.run_id,
                "attempt_id": receipt.attempt_id}

    @router.get("/agent-runs/{run_id}", response_model=RunOut)
    def get_run(run_id: str):
        return queries.get_run(run_id)

    @router.get("/agent-runs/{run_id}/facts", response_model=RunFactsOut)
    def get_run_facts(run_id: str):
        from ..db.dynamic_repository import DynamicRepository
        dynamic = DynamicRepository(engine)
        if dynamic.exists(run_id):
            return dynamic.facts(run_id)
        from ..db.assistant_repository import AssistantRepository
        assistant = AssistantRepository(engine)
        if assistant.exists(run_id):
            return assistant.facts(run_id)
        return corrections.get_run_facts(run_id)

    @router.get("/agent-runs/{run_id}/proposal")
    def get_checklist_proposal(run_id: str):
        from ..db.assistant_repository import AssistantRepository
        from ..db.dynamic_repository import DynamicRepository
        assistant = AssistantRepository(engine)
        if assistant.exists(run_id):
            result = assistant.result(run_id)["result"]
            if not result or result["kind"] != "proposal":
                return assistant.proposal(run_id)
        return DynamicRepository(engine).proposal(run_id)

    @router.get("/agent-runs/{run_id}/assistant-result")
    def get_assistant_result(run_id: str):
        from ..db.assistant_repository import AssistantRepository
        return AssistantRepository(engine).result(run_id)

    @router.get("/agent-runs/{run_id}/progress")
    def get_dynamic_progress(run_id: str):
        from ..db.dynamic_repository import DynamicRepository
        return DynamicRepository(engine).progress(run_id)

    @router.post("/agent-runs/{run_id}/fact-corrections", status_code=201)
    def correct_dynamic_fact(run_id: str, body: DynamicCorrectionIn, request: Request,
        idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=255)):
        from ..db.dynamic_repository import DynamicRepository
        actor = request.state.principal if authenticator.enabled else Principal("local", "local")
        return DynamicRepository(engine).correction(run_id, body.model_dump(mode="json"), actor=actor.key, key=idempotency_key)

    @router.get("/agent-runs/{run_id}/fact-corrections")
    def list_dynamic_corrections(run_id: str):
        from ..db.dynamic_repository import DynamicRepository
        return DynamicRepository(engine).corrections(run_id)

    @router.get("/agent-runs/{run_id}/checks", response_model=RunChecksOut)
    def get_run_checks(run_id: str):
        return queries.get_run_checks(run_id)

    @router.get("/agent-runs/{run_id}/export")
    def export_run(run_id: str,
                   format: Literal["json", "xlsx"] = Query(default="json")):
        payload = export_payload(engine, queries.get_run_checks(run_id))
        content = render_json(payload) if format == "json" else render_xlsx(payload)
        media_type = ("application/json; charset=utf-8" if format == "json" else
                      "application/vnd.openxmlformats-officedocument."
                      "spreadsheetml.sheet")
        return Response(content=content, media_type=media_type, headers={
            "Content-Disposition": (
                f'attachment; filename="docucheck-{run_id}.{format}"'),
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, no-store",
        })

    @router.get("/agent-runs/{run_id}/events", response_model=EventsOut)
    def get_events(run_id: str, after_seq: int = Query(default=0, ge=0)):
        return queries.events(run_id, after_seq)

    @router.post("/agent-runs/{run_id}/cancel", response_model=RunOut)
    def cancel_run(run_id: str):
        runs.cancel(run_id)
        return queries.get_run(run_id)

    @router.post("/agent-runs/{run_id}/retry", status_code=202,
                 response_model=AttemptAccepted)
    def retry_run(run_id: str, body: RetryIn, response: Response, request: Request,
                  idempotency_key: str = Header(
                      alias="Idempotency-Key", min_length=1, max_length=255,
                  )):
        run = queries.get_run(run_id)
        receipt = runs.retry(run_id, run["snapshot_id"], body.expected_revision,
                             idempotency_key, requester_key=request.state.principal.key if authenticator.enabled else None)
        response.headers["Location"] = f"/api/v1/agent-runs/{run_id}"
        return {"run_id": receipt.run_id, "attempt_id": receipt.attempt_id}

    @router.post("/agent-runs/{run_id}/resume", status_code=202,
                 response_model=AttemptAccepted)
    def resume_run(run_id: str, body: ResumeIn, response: Response, request: Request,
                   idempotency_key: str = Header(
                       alias="Idempotency-Key", min_length=1, max_length=255,
                   )):
        run = queries.get_run(run_id)
        receipt = runs.resume(run_id, run["snapshot_id"], body.expected_revision,
                              idempotency_key, body.content,
                              requester_key=request.state.principal.key if authenticator.enabled else None)
        response.headers["Location"] = f"/api/v1/agent-runs/{run_id}"
        return {"run_id": receipt.run_id, "attempt_id": receipt.attempt_id}

    app.include_router(router)
    app.include_router(checklist_router(engine))
    app.include_router(item_binding_router(engine))
    return app
