"""Frozen dynamic development-evaluation contracts, without decision-rule imports."""

from __future__ import annotations

import re
import unicodedata
from typing import Annotated, Literal

from openpyxl.utils.cell import range_boundaries
from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.app.criteria.models import ChecklistDefinition

Identifier = Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")]
Filename = Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,100}\.(?:pdf|xlsx)$")]
Digest = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
Text = Annotated[str, Field(min_length=1, max_length=4000, pattern=r"\S")]
Role = Literal["requirement", "quote", "spec"]
BusinessStatus = Literal["满足", "不满足", "资料冲突", "证据不足", "待人工确认"]
ProcessingStatus = Literal["complete", "partial", "failed", "pending", "not_started"]
PrimaryKey = tuple[str, str, str, str]
SchemaVersion = Annotated[int, Field(ge=1, le=1)]
CELL = r"[A-Z]{1,3}[1-9][0-9]{0,6}"


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class Scope(Contract):
    page_start: int | None = Field(default=None, ge=1, le=4096)
    page_end: int | None = Field(default=None, ge=1, le=4096)
    sheet: str | None = Field(default=None, min_length=1, max_length=31)
    cell_range: str | None = Field(default=None, pattern=rf"^{CELL}(?::{CELL})?$")

    @model_validator(mode="after")
    def valid_selector(self):
        pdf = self.page_start is not None and self.page_end is not None
        xlsx = self.sheet is not None and self.cell_range is not None
        if pdf == xlsx or (pdf and (self.sheet is not None or self.cell_range is not None)) or (
            xlsx and (self.page_start is not None or self.page_end is not None)
        ):
            raise ValueError("invalid_scope")
        if pdf and self.page_start > self.page_end:
            raise ValueError("invalid_page_range")
        if xlsx:
            left, top, right, bottom = range_boundaries(self.cell_range)
            if left > right or top > bottom or right > 16384 or bottom > 1048576:
                raise ValueError("invalid_cell_range")
        return self


class Anchor(Contract):
    document: Filename
    role: Role
    supplier_handle: Identifier | None
    item_id: Identifier
    text: Text
    page: int | None = Field(default=None, ge=1, le=4096)
    sheet: str | None = Field(default=None, min_length=1, max_length=31)
    cell: str | None = Field(default=None, pattern=rf"^{CELL}$")
    bbox: list[float] | None = Field(default=None, min_length=4, max_length=4)

    @model_validator(mode="after")
    def valid_location(self):
        if self.document.endswith(".pdf"):
            if self.page is None or self.sheet is not None or self.cell is not None:
                raise ValueError("invalid_pdf_location")
            if self.bbox and (any(v < 0 or not float("-inf") < v < float("inf") for v in self.bbox)
                              or self.bbox[0] >= self.bbox[2] or self.bbox[1] >= self.bbox[3]):
                raise ValueError("invalid_bbox")
        elif self.sheet is None or self.cell is None or self.page is not None or self.bbox is not None:
            raise ValueError("invalid_xlsx_location")
        if (self.role == "requirement") != (self.supplier_handle is None):
            raise ValueError("anchor_role_supplier")
        return self


def _claim_key(anchor: Anchor) -> tuple:
    """A bbox alias cannot turn the same original text into a second claim."""
    return (anchor.document, anchor.role, anchor.supplier_handle, anchor.item_id,
            anchor.page, anchor.sheet, anchor.cell, "".join(unicodedata.normalize("NFKC", anchor.text).split()))


def contains(scope: Scope, anchor: Anchor) -> bool:
    if scope.page_start is not None:
        return anchor.page is not None and scope.page_start <= anchor.page <= scope.page_end
    if anchor.sheet != scope.sheet or anchor.cell is None:
        return False
    left, top, right, bottom = range_boundaries(scope.cell_range)
    col, row, _, _ = range_boundaries(anchor.cell)
    return left <= col <= right and top <= row <= bottom


class Document(Contract):
    filename: Filename
    role: Role
    supplier_handle: Identifier | None
    sha256: Digest
    bytes: int = Field(gt=0, le=10 * 1024 * 1024)
    pages: int = Field(ge=0, le=30)

    @model_validator(mode="after")
    def valid_metadata(self):
        if (self.role == "requirement") != (self.supplier_handle is None):
            raise ValueError("document_role_supplier")
        if self.filename.endswith(".pdf") != (self.pages > 0):
            raise ValueError("invalid_document_pages")
        return self


class Binding(Contract):
    document: Filename
    item_id: Identifier
    sku: str | None = Field(default=None, min_length=1, max_length=120)
    scope: Scope
    anchor: Anchor
    sku_anchor: Anchor | None = None

    @model_validator(mode="after")
    def valid_anchors(self):
        for anchor in [self.anchor, *([self.sku_anchor] if self.sku_anchor else [])]:
            if anchor.document != self.document or anchor.item_id != self.item_id or not contains(self.scope, anchor):
                raise ValueError("binding_anchor_scope")
        if self.sku and (self.sku_anchor is None or not re.search(
            rf"(?<![A-Za-z0-9_-]){re.escape(self.sku)}(?![A-Za-z0-9_-])", self.sku_anchor.text
        )):
            raise ValueError("missing_sku_anchor")
        return self


class Manifest(Contract):
    schema_version: SchemaVersion
    protocol_id: Literal["dynamic-dev-v1"]
    pack_id: Identifier
    data_class: Literal["ai_authored_seen_development"]
    category: Literal["phone", "fruit", "clothing", "mixed"]
    source_family_ids: list[Identifier] = Field(min_length=1, max_length=20)
    supplier_handles: list[Identifier] = Field(min_length=1, max_length=20)
    documents: list[Document] = Field(min_length=2, max_length=100)
    checklist_definition: ChecklistDefinition
    buyer_anchors: dict[Identifier, Anchor] = Field(min_length=1, max_length=100)
    bindings: list[Binding] = Field(min_length=2, max_length=400)
    authors: list[Text] = Field(min_length=1, max_length=20)
    reviewers: list[Text] = Field(min_length=1, max_length=20)

    def primary_keys(self) -> list[PrimaryKey]:
        return [(self.pack_id, supplier, criterion.item_id, criterion.criterion_id)
                for supplier in self.supplier_handles for criterion in self.checklist_definition.criteria
                if criterion.enabled]

    @model_validator(mode="after")
    def valid_scope(self):
        docs = {doc.filename: doc for doc in self.documents}
        items = {item.item_id for item in self.checklist_definition.items}
        if len(docs) != len(self.documents) or len(set(self.supplier_handles)) != len(self.supplier_handles):
            raise ValueError("duplicate_manifest_identity")
        if not any(doc.role == "requirement" for doc in self.documents) or not self.primary_keys():
            raise ValueError("missing_requirement_or_enabled_criteria")
        if sum(doc.pages for doc in self.documents) > 30:
            raise ValueError("case_page_limit")
        if any(doc.supplier_handle is not None and doc.supplier_handle not in self.supplier_handles
               for doc in self.documents):
            raise ValueError("unknown_supplier")
        criteria = {criterion.criterion_id: criterion for criterion in self.checklist_definition.criteria if criterion.enabled}
        if set(self.buyer_anchors) != set(criteria):
            raise ValueError("buyer_anchor_criteria_mismatch")
        for criterion_id, anchor in self.buyer_anchors.items():
            doc = docs.get(anchor.document)
            if (doc is None or doc.role != "requirement" or anchor.role != "requirement"
                or anchor.supplier_handle is not None or anchor.item_id != criteria[criterion_id].item_id):
                raise ValueError("buyer_anchor_identity")
            if anchor.page is not None and anchor.page > doc.pages:
                raise ValueError("buyer_anchor_outside_document")
        binding_keys = []
        for binding in self.bindings:
            doc = docs.get(binding.document)
            if doc is None or binding.item_id not in items:
                raise ValueError("unknown_binding_identity")
            if ((doc.pages > 0) != (binding.scope.page_start is not None)
                or (doc.pages and binding.scope.page_end > doc.pages)):
                raise ValueError("binding_location_outside_document")
            for anchor in [binding.anchor, *([binding.sku_anchor] if binding.sku_anchor else [])]:
                if anchor.role != doc.role or anchor.supplier_handle != doc.supplier_handle:
                    raise ValueError("binding_source_identity")
            binding_keys.append((binding.document, binding.item_id, binding.scope.model_dump_json()))
        if len(set(binding_keys)) != len(binding_keys):
            raise ValueError("duplicate_binding")
        if {name for name, doc in docs.items() if doc.role != "requirement"} - {binding.document for binding in self.bindings}:
            raise ValueError("unbound_document")
        for supplier, item in {(key[1], key[2]) for key in self.primary_keys()}:
            if not any(docs[b.document].supplier_handle == supplier and b.item_id == item for b in self.bindings):
                raise ValueError("supplier_item_without_scope")
        return self


class EvidenceGroup(Contract):
    group_id: Identifier
    purpose: Literal["buyer_baseline", "supplier_claim", "context"]
    alternatives: list[Anchor] = Field(min_length=1, max_length=20)


class CoverageScope(Contract):
    document: Filename
    item_id: Identifier
    scope: Scope


class GoldCell(Contract):
    pack_id: Identifier
    supplier_handle: Identifier
    item_id: Identifier
    criterion_id: Identifier
    expected_processing: Literal["complete"]
    expected_business: BusinessStatus
    expected_formal: bool
    expected_needs_review: bool
    reason: Text
    evidence_groups: list[EvidenceGroup] = Field(min_length=1, max_length=30)
    required_coverage: list[CoverageScope] = Field(min_length=1, max_length=100)
    rule_basis: Text
    buyer_baseline: Text
    source_version: Text

    def key(self) -> PrimaryKey:
        return self.pack_id, self.supplier_handle, self.item_id, self.criterion_id

    @model_validator(mode="after")
    def valid_expectations(self):
        if len({g.group_id for g in self.evidence_groups}) != len(self.evidence_groups):
            raise ValueError("duplicate_evidence_group")
        claims = [g for g in self.evidence_groups if g.purpose == "supplier_claim"]
        if self.expected_business in {"满足", "不满足", "资料冲突"} and (
            not claims or not any(g.purpose == "buyer_baseline" for g in self.evidence_groups)
        ):
            raise ValueError("missing_buyer_or_supplier_group")
        if self.expected_business == "资料冲突" and len(claims) < 2:
            raise ValueError("conflict_requires_multiple_claims")
        if self.expected_business == "资料冲突":
            identities = [{_claim_key(anchor) for anchor in group.alternatives} for group in claims]
            if any(left_anchor[:-1] == right_anchor[:-1] and (
                left_anchor[-1] in right_anchor[-1] or right_anchor[-1] in left_anchor[-1]
            ) for i, left in enumerate(identities) for right in identities[i + 1:]
                for left_anchor in left for right_anchor in right):
                raise ValueError("conflict_requires_distinct_claims")
        if self.expected_needs_review and self.expected_formal:
            raise ValueError("review_cannot_be_formal")
        for group in self.evidence_groups:
            for anchor in group.alternatives:
                if anchor.item_id != self.item_id:
                    raise ValueError("gold_anchor_item")
                if group.purpose == "buyer_baseline" and anchor.role != "requirement":
                    raise ValueError("buyer_group_role")
                if group.purpose == "supplier_claim" and (
                    anchor.role == "requirement" or anchor.supplier_handle != self.supplier_handle
                ):
                    raise ValueError("supplier_group_role")
        return self


class Gold(Contract):
    schema_version: SchemaVersion
    protocol_id: Literal["dynamic-dev-v1"]
    pack_id: Identifier
    data_class: Literal["ai_authored_seen_development"]
    author: Text
    reviewer: Text
    review_state: Literal["ai_reviewed_development"]
    cells: list[GoldCell] = Field(min_length=1, max_length=2000)

    @model_validator(mode="after")
    def unique_cells(self):
        if len({cell.key() for cell in self.cells}) != len(self.cells):
            raise ValueError("duplicate_gold_key")
        return self


def validate_pair(manifest: Manifest, gold: Gold) -> None:
    """Check label completeness and provenance; never compute a business answer."""
    if (manifest.pack_id, manifest.protocol_id, manifest.data_class) != (gold.pack_id, gold.protocol_id, gold.data_class):
        raise ValueError("manifest_gold_identity")
    if set(manifest.primary_keys()) != {cell.key() for cell in gold.cells}:
        raise ValueError("gold_primary_keys_mismatch")
    docs = {doc.filename: doc for doc in manifest.documents}
    for cell in gold.cells:
        applicable = {(b.document, b.item_id, b.scope.model_dump_json()) for b in manifest.bindings
                      if b.item_id == cell.item_id and docs[b.document].supplier_handle == cell.supplier_handle}
        scopes = [(scope.document, scope.item_id, scope.scope.model_dump_json()) for scope in cell.required_coverage]
        if set(scopes) != applicable or len(set(scopes)) != len(scopes):
            raise ValueError("gold_coverage_scope_mismatch")
        for group in cell.evidence_groups:
            for anchor in group.alternatives:
                doc = docs.get(anchor.document)
                if doc is None or doc.role != anchor.role or doc.supplier_handle != anchor.supplier_handle:
                    raise ValueError("gold_document_identity")
                if doc.supplier_handle not in {None, cell.supplier_handle}:
                    raise ValueError("gold_cross_supplier")
                if anchor.role == "requirement":
                    if anchor.page is not None and anchor.page > doc.pages:
                        raise ValueError("gold_anchor_outside_document")
                elif not any(b.document == anchor.document and b.item_id == cell.item_id
                             and contains(b.scope, anchor) for b in manifest.bindings):
                    raise ValueError("gold_anchor_outside_binding")


class ObservedCell(Contract):
    pack_id: Identifier
    supplier_handle: Identifier
    item_id: Identifier
    criterion_id: Identifier
    surface: Literal["full", "status_only"] = "full"
    processing_status: ProcessingStatus = "complete"
    business_status: BusinessStatus | None
    is_formal: bool | None = None
    needs_review: bool | None = None
    evidence_refs: list[str] = Field(default_factory=list, max_length=1000)

    def key(self) -> PrimaryKey:
        return self.pack_id, self.supplier_handle, self.item_id, self.criterion_id


class ObservedSource(Anchor):
    evidence_id: str = Field(min_length=1, max_length=100)
    sha256: Digest


class CoverageRecord(CoverageScope):
    processing_status: ProcessingStatus
    source: str | None = Field(default=None, max_length=1000)


class ObservedChecklist(Contract):
    version_id: Identifier
    case_id: Identifier
    snapshot_id: Identifier
    definition: ChecklistDefinition


class Observation(Contract):
    pack_id: Identifier
    case_id: Identifier | None = None
    snapshot_id: Identifier | None = None
    run_status: Literal["completed", "partial", "failed", "pending", "not_started", "waiting_user",
                        "interrupted", "cancelled", "outcome_unknown", "running", "queued"]
    checklist_version_id: Identifier | None = None
    confirmed_checklist: ObservedChecklist | None = None
    api_cells: list[ObservedCell] = Field(default_factory=list, max_length=10000)
    visible_cells: list[ObservedCell] = Field(default_factory=list, max_length=10000)
    export_cells: list[ObservedCell] = Field(default_factory=list, max_length=10000)
    xlsx_export_cells: list[ObservedCell] = Field(default_factory=list, max_length=10000)
    sources: list[ObservedSource] = Field(default_factory=list, max_length=20000)
    coverage: list[CoverageRecord] = Field(default_factory=list, max_length=2000)
    coverage_unknown: bool = False
    surface_errors: list[str] = Field(default_factory=list, max_length=1000)
    error_code: str | None = Field(default=None, max_length=200)
