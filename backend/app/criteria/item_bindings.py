"""Separate, immutable item attribution; never changes original evidence identity."""

import json
import re
from dataclasses import asdict, dataclass
from typing import Annotated, Literal, Self

from openpyxl.utils.cell import coordinate_to_tuple
from pydantic import Field, model_validator

from ..evidence.models import Document, ScopedEvidence
from ..evidence.repository import EvidenceRepository, digest
from .models import ChecklistDefinition, Contract, EvidenceContext, Identifier, Label
from .page_exclusions import ValidatedExclusion
from .validation import mentions, source_span

MAX_ENTRIES = 140
MAX_REFS = 25_000


def identity_negated(text: str, value: str) -> bool:
    """Negate the product itself, not a following certificate/property."""
    label = re.escape(value)
    before = r"(?:未提供|不提供|不具备|没有|缺少|不采用|不使用|不含|不是|并非|非|无|不属于)"
    after = r"(?:未提供|不提供|缺失|没有|不存在)"
    return bool(re.search(rf"{before}\s*{label}|{label}\s*{after}(?=\s*(?:$|[，。；,;\n]))", text))


def explicit_skus(text: str, *, allow_bare: bool = False) -> frozenset[str]:
    """Labelled identifiers or a standalone ASCII SKU are exact constraints.

    Quantity prose or a pending model question is not an exact model request.
    More complex/non-ASCII model requirements remain subject to manual review.
    """
    if allow_bare and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}", text.strip()):
        return frozenset({text.strip().casefold()})
    return frozenset(
        value.casefold()
        for value in re.findall(
            r"(?:型号|款号|SKU)\s*[:：]?\s*([A-Za-z0-9][A-Za-z0-9._-]{0,79})",
            text,
            re.IGNORECASE,
        )
    )


def unsupported_sku_declaration(text: str) -> bool:
    """An unparsed explicit SKU is a guard, never an absent constraint."""
    values = re.findall(r"(?:型号|款号|SKU)\s*[:：]?\s*([^，。；,;\n]*)", text, re.IGNORECASE)
    for value in values:
        value = value.strip()
        if re.fullmatch(r"(?:待|等待|尚待|未明确|未确定|未指定|不限|任意).*", value):
            continue
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}", value):
            return True
    return False


def pure_identity_block(text: str, item_name: str, supplier_sku: str | None) -> bool:
    """Only an entire identity-only block may be exempt from fact coverage."""
    label = rf"(?:(?:品目|品名|商品名称|采购品目)\s*[:：]?\s*)?{re.escape(item_name)}"
    if re.fullmatch(label + r"\s*[。.]?", text.strip()):
        return True
    if not supplier_sku or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}", supplier_sku):
        return False
    sku = rf"(?:型号|款号|SKU)\s*[:：]?\s*{re.escape(supplier_sku)}"
    return bool(re.fullmatch(rf"(?:{label}\s*[,，;；]?\s*)?{sku}\s*[。.]?", text.strip(), re.IGNORECASE))


def unresolved_buyer_specification(text: str, item_name: str) -> bool:
    """Finite safe cases only; a free-text specification is not silently dropped.

    Complex model/specification constraints require manual review until represented
    by a supported explicit SKU or separate confirmed criteria.
    """
    if not text.strip() or text.strip() == item_name or explicit_skus(text, allow_bare=True):
        return False
    clauses = re.split(r"[，,；;。\n]", text)
    pending = r"(?:(?:具体)?型号)?(?:待|等待|尚待|未明确|未确定|未指定).*(?:确认|明确|确定|指定)"
    quantity = r"(?:采购)?(?:数量)?\s*[:：]?\s*\d+(?:\.\d+)?\s*(?:台|件|箱|个|部|套|只|双|批|公斤|kg)"
    return any(clause.strip() and not re.fullmatch(rf"(?:{pending}|{quantity})", clause.strip()) for clause in clauses)


class WholeSelector(Contract):
    kind: Literal["whole"]


class XlsxSelector(Contract):
    kind: Literal["xlsx"]
    sheet: str = Field(min_length=1, max_length=255)
    start_row: int = Field(ge=1, le=1_048_576)
    end_row: int = Field(ge=1, le=1_048_576)
    start_column: int = Field(ge=1, le=16_384)
    end_column: int = Field(ge=1, le=16_384)

    @model_validator(mode="after")
    def ordered(self) -> Self:
        if self.start_row > self.end_row or self.start_column > self.end_column:
            raise ValueError("item_binding_range")
        return self


class PdfSelector(Contract):
    kind: Literal["pdf"]
    start_page: int = Field(ge=1, le=30)
    end_page: int = Field(ge=1, le=30)

    @model_validator(mode="after")
    def ordered(self) -> Self:
        if self.start_page > self.end_page:
            raise ValueError("item_binding_range")
        return self


Selector = Annotated[
    WholeSelector | XlsxSelector | PdfSelector, Field(discriminator="kind")
]


class ItemBindingEntry(Contract):
    document_id: str = Field(min_length=1, max_length=64)
    source_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    parser_version: str = Field(min_length=1, max_length=1000)
    item_id: Identifier
    selector: Selector
    anchor: EvidenceContext
    identifier_class: Literal["category", "sku", "unknown"]
    supplier_sku: Label | None = None
    sku_anchor: EvidenceContext | None = None
    single_item_confirmed: bool = False

    @model_validator(mode="after")
    def explicit(self) -> Self:
        if (self.selector.kind == "whole") != self.single_item_confirmed:
            raise ValueError("item_binding_confirmation_required")
        if (self.supplier_sku is None) != (self.sku_anchor is None):
            raise ValueError("item_binding_sku")
        return self


@dataclass(frozen=True)
class ValidatedEntry:
    document_id: str
    item_id: str
    supplier: str
    source_sha256: str
    parser_version: str
    evidence_refs: tuple[str, ...]
    anchor_refs: tuple[str, ...]
    identifier_class: str
    supplier_sku: str | None
    canonical_json: str

    def payload(self) -> dict:
        return json.loads(self.canonical_json)


@dataclass(frozen=True)
class ValidatedBindings:
    entries: tuple[ValidatedEntry, ...]
    exclusions: tuple[ValidatedExclusion, ...] = ()

    def payload(self) -> list[dict]:
        return [entry.payload() for entry in self.entries]

    def allowed_refs(self, item_id: str, supplier: str | None = None) -> frozenset[str]:
        return frozenset(
            ref
            for entry in self.entries
            if entry.item_id == item_id
            and (supplier is None or entry.supplier == supplier)
            for ref in entry.evidence_refs
        )

    def for_refs(
        self,
        item_id: str,
        refs: list[str] | tuple[str, ...],
        supplier: str | None = None,
    ) -> ValidatedEntry:
        if not refs or len(set(refs)) != len(refs):
            raise ValueError("item_binding_scope")
        found = [
            entry
            for entry in self.entries
            if entry.item_id == item_id
            and (supplier is None or entry.supplier == supplier)
            and set(refs) <= set(entry.evidence_refs)
        ]
        if not found:
            raise ValueError("item_binding_scope")
        # Every fact must fit one independently confirmed range.
        return found[0]

    def assert_fact_scope(
        self, item_id: str, refs: list[str], supplier: str
    ) -> ValidatedEntry:
        return self.for_refs(item_id, refs, supplier)


@dataclass(frozen=True)
class ReviewBindings:
    """Validated server context, reused across extraction batches and facts."""

    version_id: str | None
    digest: str
    case_id: str
    snapshot_id: str
    checklist_digest: str
    bindings: ValidatedBindings
    schema_version: int = 1

    def identity(self) -> dict:
        return {"item_binding_version_id": self.version_id, "item_binding_digest": self.digest}


def review_bindings(
    value: dict | ReviewBindings,
    definition: ChecklistDefinition | dict,
    repo: EvidenceRepository,
) -> ReviewBindings:
    definition = (definition if isinstance(definition, ChecklistDefinition)
                  else ChecklistDefinition.model_validate(definition))
    if isinstance(value, ReviewBindings):
        if (value.case_id != repo.case_id or value.snapshot_id != repo.snapshot_id
                or value.checklist_digest != digest(definition.model_dump(mode="json"))):
            raise ValueError("item_binding_source_changed")
        return value
    bound = validated_from_frozen(value, definition, repo)
    return ReviewBindings(value["version_id"], value["digest"], repo.case_id, repo.snapshot_id,
                         value["payload"]["checklist_digest"], bound, value["payload"]["schema_version"])


def assert_binding_rule(value: dict | ReviewBindings | None, rule_version: str) -> None:
    schema = value.schema_version if isinstance(value, ReviewBindings) else (value or {}).get("payload", {}).get("schema_version", 1)
    if schema == 2 and rule_version != "dynamic-rules-v16":
        raise ValueError("item_exclusion_rule_version")


def binding_identity(value: dict | ReviewBindings) -> dict:
    if isinstance(value, ReviewBindings):
        return value.identity()
    if (not isinstance(value, dict) or value.get("digest") != digest(value.get("payload"))
            or not isinstance(value.get("payload"), dict)):
        raise ValueError("item_binding_source_changed")
    return {"item_binding_version_id": value["version_id"], "item_binding_digest": value["digest"]}


def _expand(
    selector: WholeSelector | XlsxSelector | PdfSelector,
    document: Document,
    blocks: list[ScopedEvidence],
) -> list[ScopedEvidence]:
    if selector.kind == "whole":
        return blocks
    if selector.kind == "pdf":
        pages = document.parsed.total_pages
        if not pages or selector.end_page > pages or any(b.sheet for b in blocks):
            raise ValueError("item_binding_range")
        return [
            b
            for b in blocks
            if b.page_idx is not None
            and selector.start_page <= b.page_idx + 1 <= selector.end_page
        ]
    sheet = [b for b in blocks if b.sheet == selector.sheet and b.cell]
    if not sheet or any(b.page_idx is not None for b in blocks):
        raise ValueError("item_binding_range")
    positions = [(coordinate_to_tuple(b.cell), b) for b in sheet]
    if selector.end_row > max(p[0] for p, _ in positions) or selector.end_column > max(
        p[1] for p, _ in positions
    ):
        raise ValueError("item_binding_range")
    return [
        b
        for (row, col), b in positions
        if selector.start_row <= row <= selector.end_row
        and selector.start_column <= col <= selector.end_column
    ]


def validate_bindings(
    entries: list[dict | ItemBindingEntry] | tuple[dict | ItemBindingEntry, ...],
    definition: ChecklistDefinition | dict,
    repo: EvidenceRepository,
) -> ValidatedBindings:
    definition = (
        definition
        if isinstance(definition, ChecklistDefinition)
        else ChecklistDefinition.model_validate(definition)
    )
    if not isinstance(entries, (list, tuple)) or len(entries) > MAX_ENTRIES:
        raise ValueError("item_binding_capacity")
    items = {item.item_id: item for item in definition.items}
    documents = {doc.document_id: doc for doc in repo.documents}
    result, owners, total = [], {}, 0
    for raw in entries:
        entry = (
            raw
            if isinstance(raw, ItemBindingEntry)
            else ItemBindingEntry.model_validate(raw)
        )
        item, doc = items.get(entry.item_id), documents.get(entry.document_id)
        if (
            item is None
            or doc is None
            or doc.role not in {"quote", "spec"}
            or not doc.supplier
            or doc.source_hash != entry.source_sha256
            or doc.parsed.parser_version != entry.parser_version
            or doc.parsed.status not in {"complete", "partial"}
        ):
            raise ValueError("item_binding_source")
        anchors, text = source_span(
            repo, entry.anchor.evidence_refs, entry.anchor.quote
        )
        if any(b.document_id != doc.document_id for b in anchors):
            raise ValueError("item_binding_anchor")
        sku_text, sku_blocks = "", []
        if entry.sku_anchor is not None:
            sku_blocks, sku_text = source_span(
                repo, entry.sku_anchor.evidence_refs, entry.sku_anchor.quote
            )
            if (
                any(b.document_id != doc.document_id for b in sku_blocks)
                or not mentions(entry.sku_anchor.quote, entry.supplier_sku)
                or identity_negated(sku_text, entry.supplier_sku)
            ):
                raise ValueError("item_binding_sku")
        anchor_sku = (
            entry.supplier_sku
            if entry.supplier_sku and mentions(item.specification, entry.supplier_sku)
            else None
        )
        if (
            not mentions(entry.anchor.quote, item.name)
            and not (anchor_sku and mentions(entry.anchor.quote, anchor_sku))
        ) or identity_negated(text, item.name):
            raise ValueError("item_binding_anchor")
        if (
            entry.identifier_class == "category"
            and doc.model
            and (doc.model != item.name or not mentions(text, doc.model))
        ):
            raise ValueError("item_binding_identifier")
        if entry.identifier_class == "sku" and (
            not entry.supplier_sku
            or (doc.model is not None and entry.supplier_sku != doc.model)
            or not mentions(sku_text, entry.supplier_sku)
        ):
            raise ValueError("item_binding_sku")
        blocks = [b for b in repo.blocks if b.document_id == doc.document_id]
        selected = _expand(entry.selector, doc, blocks)
        if not selected:
            raise ValueError("item_binding_range")
        required_skus = explicit_skus(item.specification, allow_bare=True)
        if (
            required_skus
            and entry.supplier_sku
            and entry.supplier_sku.casefold() not in required_skus
        ):
            raise ValueError("item_binding_conflict")
        owner = (item.item_id, entry.identifier_class, entry.supplier_sku)
        for block in selected:
            if identity_negated(block.text, item.name) or (
                entry.supplier_sku and identity_negated(block.text, entry.supplier_sku)
            ):
                raise ValueError("item_binding_conflict")
            if any(
                mentions(block.text, other.name) and not identity_negated(block.text, other.name)
                for other in definition.items
                if other.item_id != item.item_id
            ):
                raise ValueError("item_binding_conflict")
            # Retain explicit source SKU conflicts; a user click cannot erase them.
            models = explicit_skus(block.text)
            if any(
                entry.supplier_sku
                and model.casefold() != entry.supplier_sku.casefold()
                or required_skus
                and model.casefold() not in required_skus
                for model in models
            ):
                raise ValueError("item_binding_conflict")
            if block.evidence_id in owners and owners[block.evidence_id] != owner:
                raise ValueError("item_binding_overlap")
            owners[block.evidence_id] = owner
        total += len(selected)
        if total > MAX_REFS:
            raise ValueError("item_binding_capacity")
        payload = entry.model_dump(mode="json")
        payload.update(
            supplier=doc.supplier,
            evidence_refs=sorted(b.evidence_id for b in selected),
            source_fingerprint=digest(
                [asdict(b) for b in sorted(selected, key=lambda b: b.evidence_id)]
            ),
            anchor_fingerprint=digest([asdict(b) for b in (*anchors, *sku_blocks)]),
        )
        result.append(
            ValidatedEntry(
                doc.document_id,
                item.item_id,
                doc.supplier,
                doc.source_hash,
                doc.parsed.parser_version,
                tuple(payload["evidence_refs"]),
                tuple(entry.anchor.evidence_refs),
                entry.identifier_class,
                entry.supplier_sku,
                json.dumps(payload, ensure_ascii=False, sort_keys=True),
            )
        )
    result.sort(key=lambda entry: entry.canonical_json)
    if len({entry.canonical_json for entry in result}) != len(result):
        raise ValueError("item_binding_duplicate")
    return ValidatedBindings(tuple(result))


def validated_from_frozen(
    envelope: dict,
    definition: ChecklistDefinition | dict | None,
    repo: EvidenceRepository,
) -> ValidatedBindings:
    payload = envelope["payload"]
    definition_payload = (
        (
            definition
            if isinstance(definition, ChecklistDefinition)
            else ChecklistDefinition.model_validate(definition)
        ).model_dump(mode="json")
        if definition is not None
        else None
    )
    if (
        digest(payload) != envelope["digest"]
        or payload["case_id"] != repo.case_id
        or payload["snapshot_id"] != repo.snapshot_id
        or payload.get("schema_version") not in {1, 2}
        or payload.get("checklist_digest") != digest(definition_payload)
    ):
        raise ValueError("item_binding_source_changed")
    if (payload["schema_version"] == 1 and "exclusions" in payload
            or payload["schema_version"] == 2 and (not payload.get("exclusions") or "continuation" in payload)):
        raise ValueError("item_binding_source_changed")
    if not payload["entries"] and definition_payload is None and payload["schema_version"] == 1:
        return ValidatedBindings(())
    fields = set(ItemBindingEntry.model_fields)
    actual = validate_bindings(
        [
            {key: value for key, value in entry.items() if key in fields}
            for entry in payload["entries"]
        ],
        definition,
        repo,
    )
    if actual.payload() != payload["entries"]:
        raise ValueError("item_binding_source_changed")
    if payload["schema_version"] == 2:
        from .page_exclusions import PdfPageExclusion, validate_exclusions

        fields = set(PdfPageExclusion.model_fields)
        exclusions = validate_exclusions([{k: v for k, v in e.items() if k in fields}
                                          for e in payload["exclusions"]],
                                         ChecklistDefinition.model_validate(definition_payload), repo, actual)
        if [e.payload() for e in exclusions] != payload["exclusions"]:
            raise ValueError("item_binding_source_changed")
        actual = ValidatedBindings(actual.entries, exclusions)
    return actual
