"""First local FastAPI slice; start with uvicorn's --factory option."""

from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import APIRouter, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy import Engine, create_engine, select

from ..db.api_queries import ApiQueries, ApiQueryError
from ..db.connection import database_url
from ..db.run_repository import RepositoryError, RunRepository, SubmitRun
from .schemas import (
    AttemptAccepted,
    CaseOut,
    ConversationOut,
    EventsOut,
    MessageAccepted,
    MessageIn,
    RetryIn,
    RunOut,
)


def _error(request: Request, status: int, code: str, details: object = None):
    return JSONResponse(status_code=status, content={"error": {
        "code": code, "message": code, "details": details or {},
        "request_id": request.state.request_id,
    }})


def _domain_status(code: str) -> int:
    if code in {"not_found", "run_not_found", "scope_mismatch"}:
        return 404
    if code in {"snapshot_not_ready", "revision_conflict", "snapshot_changed",
                "idempotency_conflict", "stale_attempt", "invalid_transition"}:
        return 409
    if code == "invalid_request":
        return 400
    return 500


def create_app(engine: Engine | None = None) -> FastAPI:
    owns_engine = engine is None
    if engine is None:
        engine = create_engine(database_url(), pool_pre_ping=True,
                               hide_parameters=True)
    queries, runs = ApiQueries(engine), RunRepository(engine)

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
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        return response

    @app.exception_handler(ApiQueryError)
    @app.exception_handler(RepositoryError)
    async def domain_error(request: Request, exc: ApiQueryError | RepositoryError):
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

    app.include_router(router)
    return app
