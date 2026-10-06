"""Chinese procurement checklist contracts exposed under existing project authorization."""

from fastapi import APIRouter, Header, Request
from pydantic import Field, StrictBool, field_validator
from sqlalchemy import Engine

from ..criteria.models import ChecklistDefinition, Contract
from ..db.checklist_repository import ChecklistRepository
from .auth import Principal
from .route_auth import AuthorizedRoute


class DraftIn(ChecklistDefinition):
    expected_revision: int = Field(ge=0)
    expected_checklist_revision: int = Field(ge=0)
    reason: str = Field(min_length=1, max_length=1000, pattern=r"\S")


class ConfirmIn(Contract):
    expected_revision: int = Field(ge=0)
    expected_checklist_revision: int = Field(ge=1)
    criterion_ids: list[str] = Field(min_length=1, max_length=100)
    confirmed: StrictBool
    reason: str = Field(min_length=1, max_length=1000, pattern=r"\S")

    @field_validator("confirmed")
    @classmethod
    def require_confirmation(cls, value):
        if value is not True:
            raise ValueError("checklist_confirmation_required")
        return value


def checklist_router(engine: Engine):
    router = APIRouter(prefix="/api/v1", route_class=AuthorizedRoute)
    service = ChecklistRepository(engine)

    def actor(request):
        return (
            request.state.principal
            if request.app.state.authenticator.enabled
            else Principal("local", "local")
        ).key

    @router.get("/cases/{case_id}/snapshots/{snapshot_id}/checklist")
    def get_checklist(case_id: str, snapshot_id: str):
        return service.get(case_id, snapshot_id)

    @router.put("/cases/{case_id}/snapshots/{snapshot_id}/checklist")
    def save_checklist(
        case_id: str,
        snapshot_id: str,
        body: DraftIn,
        request: Request,
        idempotency_key: str = Header(
            alias="Idempotency-Key", min_length=1, max_length=255
        ),
    ):
        definition = body.model_dump(mode="json", include={"items", "criteria"})
        return service.put(
            case_id,
            snapshot_id,
            definition,
            actor=actor(request),
            expected_revision=body.expected_revision,
            expected_checklist_revision=body.expected_checklist_revision,
            idempotency_key=idempotency_key,
            reason=body.reason,
        )

    @router.post("/cases/{case_id}/snapshots/{snapshot_id}/checklist/confirm")
    def confirm_checklist(
        case_id: str,
        snapshot_id: str,
        body: ConfirmIn,
        request: Request,
        idempotency_key: str = Header(
            alias="Idempotency-Key", min_length=1, max_length=255
        ),
    ):
        return service.confirm(
            case_id,
            snapshot_id,
            body.criterion_ids,
            actor=actor(request),
            expected_revision=body.expected_revision,
            expected_checklist_revision=body.expected_checklist_revision,
            confirmed=body.confirmed,
            idempotency_key=idempotency_key,
            reason=body.reason,
        )

    @router.get("/cases/{case_id}/checklist-versions/{version_id}")
    def get_checklist_version(case_id: str, version_id: str):
        return service.version(case_id, version_id)

    return router
