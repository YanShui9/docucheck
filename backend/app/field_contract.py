"""Snapshot-scoped facts and decisions shared by all procurement fields.

FieldFact is admitted only after source text validation by the extraction layer.
The rule layer checks its scope and normalization again before comparison.
"""

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Literal


class FieldKey(StrEnum):
    MEMORY = "memory"
    STORAGE = "storage"
    SCREEN_SIZE = "screen_size"
    WEIGHT = "weight"
    WARRANTY = "warranty"
    QUANTITY = "quantity"
    UNIT_PRICE = "unit_price"
    DELIVERY_DAYS = "delivery_days"


Comparator = Literal["gte", "lte", "eq", "gt", "lt", "unknown"]
ProcessingStatus = Literal["complete", "partial", "failed"]
BusinessStatus = Literal["满足", "不满足", "资料冲突", "证据不足", "待人工确认"]


@dataclass(frozen=True, kw_only=True)
class FieldFact:
    fact_id: str
    snapshot_id: str
    field_key: FieldKey
    raw_value: str
    raw_unit: str
    value: Decimal
    unit: str
    qualifier: str
    comparator: Comparator
    role: Literal["requirement", "quote", "spec"]
    supplier: str | None
    model: str | None
    evidence_refs: tuple[str, ...]
    attributes: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, kw_only=True)
class FieldFactSet:
    fact_set_id: str
    snapshot_id: str
    supplier: str
    field_key: FieldKey
    facts: tuple[FieldFact, ...]
    scanned_ids: tuple[str, ...]
    complete: bool
    coverage_issues: tuple[str, ...] = ()


@dataclass(frozen=True, kw_only=True)
class FieldCheckResult:
    check_result_id: str
    snapshot_id: str
    supplier: str
    field_key: FieldKey
    processing_status: ProcessingStatus
    business_status: BusinessStatus | None
    reason_code: str
    evidence_refs: tuple[str, ...]
    reason_evidence_refs: tuple[str, ...]
    fact_ids: tuple[str, ...]
    rule_version: str
