"""Explicit Chinese procurement attribution confirmation, under project ACLs."""

from typing import Literal

from fastapi import APIRouter, Header, Request
from pydantic import Field, StrictBool, field_validator
from sqlalchemy import Engine

from ..criteria.item_bindings import ItemBindingEntry
from ..criteria.models import Contract
from ..db.item_binding_repository import ItemBindingRepository
from .auth import Principal
from .route_auth import AuthorizedRoute


class ConfirmBindingsIn(Contract):
    entries: list[ItemBindingEntry] = Field(max_length=140)
    checklist_version_id: str = Field(min_length=1, max_length=32)
    expected_revision: int = Field(ge=0)
    expected_checklist_revision: int = Field(ge=1)
    expected_binding_revision: int = Field(ge=0)
    confirmed: StrictBool
    reason: str = Field(min_length=1, max_length=1000, pattern=r"\S")

    @field_validator("confirmed")
    @classmethod
    def explicit_confirmation(cls, value: bool) -> bool:
        if value is not True:
            raise ValueError("item_binding_confirmation_required")
        return value


class ExpandedItemBindingOut(ItemBindingEntry):
    supplier: str
    evidence_refs: list[str]
    source_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    anchor_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")


class ItemBindingContinuationOut(Contract):
    version_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    digest: str = Field(pattern=r"^[a-f0-9]{64}$")


class ItemBindingPayloadOut(Contract):
    schema_version: Literal[1]
    case_id: str
    snapshot_id: str
    checklist_version_id: str | None
    checklist_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    entries: list[ExpandedItemBindingOut]
    continuation: ItemBindingContinuationOut | None = None


class ItemBindingVersionOut(Contract):
    id: str
    version: int = Field(ge=1)
    snapshot_id: str
    checklist_version_id: str
    digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    payload: ItemBindingPayloadOut
    confirmed_by: str
    reason: str
    confirmed_at: str


class ItemBindingViewOut(Contract):
    case_id: str
    snapshot_id: str
    binding_revision: int = Field(ge=0)
    current_version: ItemBindingVersionOut | None
    historical_version: ItemBindingVersionOut | None
    state: Literal["confirmed", "stale", "empty"]


def item_binding_router(engine: Engine) -> APIRouter:
    router = APIRouter(prefix="/api/v1", route_class=AuthorizedRoute)
    service = ItemBindingRepository(engine)

    @router.get(
        "/cases/{case_id}/snapshots/{snapshot_id}/item-bindings",
        response_model=ItemBindingViewOut,
        response_model_exclude_unset=True,
    )
    def get_bindings(case_id: str, snapshot_id: str) -> dict:
        return service.get(case_id, snapshot_id)

    @router.put(
        "/cases/{case_id}/snapshots/{snapshot_id}/item-bindings",
        response_model=ItemBindingViewOut,
        response_model_exclude_unset=True,
    )
    def confirm_bindings(
        case_id: str,
        snapshot_id: str,
        body: ConfirmBindingsIn,
        request: Request,
        idempotency_key: str = Header(
            alias="Idempotency-Key", min_length=1, max_length=255
        ),
    ) -> dict:
        principal = (
            request.state.principal
            if request.app.state.authenticator.enabled
            else Principal("local", "local")
        )
        data = body.model_dump(mode="json")
        return service.put(
            case_id,
            snapshot_id,
            actor=principal.key,
            idempotency_key=idempotency_key,
            **data,
        )

    @router.get(
        "/cases/{case_id}/item-binding-versions/{version_id}",
        response_model=ItemBindingVersionOut,
        response_model_exclude_unset=True,
    )
    def get_version(case_id: str, version_id: str) -> dict:
        return service.version(case_id, version_id)

    return router
