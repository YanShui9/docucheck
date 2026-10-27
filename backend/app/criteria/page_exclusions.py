"""Explicit other-SKU PDF scope decisions, never model-inferred exclusions."""

import json
import re
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING

from pydantic import Field

from ..evidence.repository import EvidenceRepository, digest
from .models import ChecklistDefinition, Contract, EvidenceContext
from .validation import mentions, source_span

if TYPE_CHECKING:
    from .item_bindings import ValidatedBindings

MAX_EXCLUSIONS = 30
SKU = r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}"
_COMMON = re.compile(
    r"共同条款|通用条款|统一(?:报价|单价|税)|"
    r"(?:本次|本项目|本采购|所有|全部|各型号|各产品|共同|通用).{0,16}(?:适用|有效|生效|报价|单价|税|条款|产品|品目|型号)|"
    r"(?:适用|用于|通用|覆盖).{0,16}(?:本次|本项目|本采购|全部|所有|各型号|各产品)|"
    r"(?:全部|所有|各)(?:设备|产品|型号).{0,16}(?:通用|适用|共同)|"
    r"\b(?:terms?|payments?|prices?|tax(?:es)?)\b.{0,40}\b(?:apply|applicable|common)\b.{0,20}\b(?:all|every)\b",
    re.IGNORECASE,
)


class PdfPageExclusion(Contract):
    document_id: str = Field(min_length=1, max_length=64)
    source_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    parser_version: str = Field(min_length=1, max_length=1000)
    page: int = Field(strict=True, ge=1, le=30)
    other_sku: str = Field(pattern=rf"^{SKU}$")
    sku_anchor: EvidenceContext
    reason: str = Field(min_length=1, max_length=1000, pattern=r"\S")


@dataclass(frozen=True)
class ValidatedExclusion:
    document_id: str
    supplier: str
    page: int
    evidence_refs: tuple[str, ...]
    canonical_json: str

    def payload(self) -> dict:
        return json.loads(self.canonical_json)


def validate_exclusions(
    exclusions: list[dict | PdfPageExclusion] | tuple,
    definition: ChecklistDefinition,
    repo: EvidenceRepository,
    bindings: "ValidatedBindings",
) -> tuple[ValidatedExclusion, ...]:
    from .item_bindings import (
        MAX_REFS,
        explicit_skus,
        identity_negated,
    )

    if not isinstance(exclusions, (list, tuple)) or len(exclusions) > MAX_EXCLUSIONS:
        raise ValueError("item_exclusion_capacity")
    if not exclusions:
        return ()
    targets = set()
    for item in definition.items:
        text = item.specification.strip()
        if not re.fullmatch(rf"(?:(?:型号|款号|SKU)\s*[:：]?\s*)?{SKU}", text, re.IGNORECASE):
            raise ValueError("item_exclusion_buyer_scope")
        targets.update(explicit_skus(text, allow_bare=True))
    if not targets:
        raise ValueError("item_exclusion_buyer_scope")
    reserved = {ref for entry in bindings.entries for ref in (
        *entry.evidence_refs, *entry.anchor_refs,
        *((entry.payload().get("sku_anchor") or {}).get("evidence_refs", [])))}
    documents = {doc.document_id: doc for doc in repo.documents}
    result, seen, total = [], set(), len(reserved)
    for raw in exclusions:
        entry = raw if isinstance(raw, PdfPageExclusion) else PdfPageExclusion.model_validate(raw)
        doc = documents.get(entry.document_id)
        if (doc is None or doc.role not in {"spec", "quote"} or not doc.supplier
                or doc.source_hash != entry.source_sha256 or doc.parsed.parser_version != entry.parser_version
                or doc.parsed.status != "complete" or doc.parsed.coverage_issues
                or not doc.parsed.total_pages or entry.page > doc.parsed.total_pages
                or set(doc.parsed.processed_pages) != set(range(doc.parsed.total_pages))
                or any(b.sheet is not None or b.page_idx is None for b in doc.parsed.blocks)):
            raise ValueError("item_exclusion_source")
        key = (entry.document_id, entry.page)
        if key in seen:
            raise ValueError("item_exclusion_overlap")
        seen.add(key)
        blocks = [b for b in repo.blocks if b.document_id == entry.document_id and b.page_idx == entry.page - 1]
        refs = {b.evidence_id for b in blocks}
        if not refs or refs.intersection(reserved):
            raise ValueError("item_exclusion_overlap")
        supplier_entries = [e for e in bindings.entries if e.supplier == doc.supplier]
        if not supplier_entries or any(e.identifier_class != "sku" or not e.supplier_sku for e in supplier_entries):
            raise ValueError("item_exclusion_buyer_scope")
        target_skus = targets | {e.supplier_sku.casefold() for e in supplier_entries}
        if entry.other_sku.casefold() in target_skus:
            raise ValueError("item_exclusion_target")
        anchors, _ = source_span(repo, entry.sku_anchor.evidence_refs, entry.sku_anchor.quote)
        if len(anchors) != 1 or anchors[0].evidence_id not in refs:
            raise ValueError("item_exclusion_anchor")
        # The entire anchor must be a finite identity heading, never a selected
        # substring which hides a business fact after the model declaration.
        heading = anchors[0].text.strip()
        label = r"(?:(?:品目|品名|商品名称|采购品目)\s*[:：]\s*[^；;。\n\d]{1,80}\s*[；;]\s*)?"
        if (not re.fullmatch(rf"{label}(?:型号|款号|SKU)\s*[:：]?\s*{re.escape(entry.other_sku)}\s*[。.]?",
                             heading, re.IGNORECASE)
                or re.search(r"数量|单价|交期|含税|不含税|共同|通用", heading)):
            raise ValueError("item_exclusion_anchor")
        page_text = "\n".join(b.text for b in blocks)
        # Binding rules allow pending model questions; an exclusion instead
        # requires every explicit identity declaration to name this single SKU.
        declarations = re.findall(r"(?:型号|款号|SKU)\s*[:：]?\s*([^，。；,;\n]*)", page_text, re.IGNORECASE)
        if (_COMMON.search(page_text)
                or any(value.strip().casefold() != entry.other_sku.casefold() for value in declarations)
                or explicit_skus(page_text) != {entry.other_sku.casefold()}
                or identity_negated(page_text.casefold(), entry.other_sku.casefold())
                or any(mentions(page_text, sku) for sku in target_skus)
                or any(mentions(page_text, item.name) for item in definition.items)):
            raise ValueError("item_exclusion_mixed_scope")
        total += len(refs)
        if total > MAX_REFS:
            raise ValueError("item_exclusion_capacity")
        payload = entry.model_dump(mode="json") | {
            "supplier": doc.supplier, "evidence_refs": sorted(refs),
            "source_fingerprint": digest([asdict(b) for b in sorted(blocks, key=lambda b: b.evidence_id)]),
            "anchor_fingerprint": digest([asdict(b) for b in anchors]),
        }
        result.append(ValidatedExclusion(entry.document_id, doc.supplier, entry.page,
                                         tuple(sorted(refs)), json.dumps(payload, ensure_ascii=False, sort_keys=True)))
    return tuple(sorted(result, key=lambda e: (e.document_id, e.page)))
