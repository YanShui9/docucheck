"""Deterministic, fail-closed checks for the eight procurement fields."""

import re
from collections.abc import Sequence
from dataclasses import asdict
from decimal import Decimal, InvalidOperation

from ..evidence.repository import EvidenceRepository, digest
from ..field_contract import FieldCheckResult, FieldFact, FieldKey

RULE_VERSION = "fields-v1"
_NUMBER = re.compile(r"\d+(?:\.\d+)?\Z")
_COMPARATORS = {"gte", "lte", "eq", "gt", "lt", "unknown"}
_ATTRIBUTE_KEYS = {
    "approximate", "currency", "pricing_unit", "tax_basis", "service_type",
    "item_type", "start_event", "day_type",
}


def normalize_field_value(
    field_key: FieldKey | str, raw_value: str, raw_unit: str,
) -> tuple[Decimal, str]:
    """Return a comparable decimal and unit without guessing an ambiguous basis."""
    field = FieldKey(field_key)
    if not isinstance(raw_value, str) or len(raw_value) > 20 or not _NUMBER.fullmatch(raw_value):
        raise ValueError("fact_value")
    try:
        value = Decimal(raw_value)
    except InvalidOperation as exc:
        raise ValueError("fact_value") from exc
    if not value.is_finite() or value <= 0:
        raise ValueError("fact_value")
    unit = raw_unit.strip()
    folded = unit.casefold()

    if field == FieldKey.MEMORY:
        if folded == "mib":
            return value / Decimal(1024), "GiB"
        if folded == "gib":
            return value, "GiB"
        if folded == "gb":
            return value, "GB"
    elif field == FieldKey.STORAGE:
        if folded == "tb":
            return value * 1000, "GB"
        if folded == "gb":
            return value, "GB"
        if folded == "gib":
            return value, "GiB"
    elif field == FieldKey.SCREEN_SIZE:
        if folded in {"inch", "in", "英寸"}:
            return value, "inch"
        if folded in {"cm", "厘米"}:
            return value / Decimal("2.54"), "inch"
    elif field == FieldKey.WEIGHT:
        if folded == "kg":
            return value, "kg"
        if folded == "g":
            return value / 1000, "kg"
    elif field == FieldKey.WARRANTY:
        if folded in {"month", "months", "月", "个月"}:
            return value, "month"
        if folded in {"year", "years", "年"}:
            return value * 12, "month"
    elif field == FieldKey.QUANTITY:
        if value != value.to_integral_value():
            raise ValueError("fact_value")
        if unit in {"台", "件", "个", "套"}:
            return value, unit
    elif field == FieldKey.UNIT_PRICE:
        parts = re.fullmatch(r"(元|人民币|CNY|USD|美元)[/／]([^/／\s]+)", unit, re.IGNORECASE)
        if parts:
            currency = {"元": "CNY", "人民币": "CNY", "美元": "USD"}.get(parts[1], parts[1].upper())
            return value, f"{currency}/{parts[2]}"
    elif field == FieldKey.DELIVERY_DAYS:
        if folded in {"natural_day", "day", "days", "自然日", "天"}:
            return value, "natural_day"
        if folded in {"working_day", "workday", "工作日"}:
            return value, "working_day"
        if folded in {"week", "weeks", "natural_week", "周", "自然周"}:
            return value * 7, "natural_day"
    raise ValueError("fact_unit")


def _attributes(fact: FieldFact) -> dict[str, str]:
    attrs = dict(fact.attributes)
    if (len(attrs) != len(fact.attributes) or not set(attrs) <= _ATTRIBUTE_KEYS
            or any(not isinstance(value, str) or not value for value in attrs.values())
            or attrs.get("approximate", "false") not in {"true", "false"}):
        raise ValueError("fact_attributes")
    return attrs


def _known(value: str | None) -> bool:
    return bool(value and value.strip() and value.strip().casefold() not in {
        "unknown", "unclear", "unspecified", "n/a", "待确认", "不明",
    })


def _validate_fact(
    repo: EvidenceRepository, supplier: str, field: FieldKey, fact: FieldFact,
) -> None:
    if fact.snapshot_id != repo.snapshot_id:
        raise ValueError("fact_snapshot")
    if fact.field_key != field:
        raise ValueError("fact_field")
    if fact.role not in {"requirement", "quote", "spec"}:
        raise ValueError("fact_role")
    if fact.supplier != (None if fact.role == "requirement" else supplier):
        raise ValueError("supplier_scope")
    if not fact.evidence_refs or fact.comparator not in _COMPARATORS:
        raise ValueError("fact_scope")
    expected_value, expected_unit = normalize_field_value(field, fact.raw_value, fact.raw_unit)
    if (not isinstance(fact.value, Decimal)
            or fact.value != expected_value or fact.unit != expected_unit):
        raise ValueError("fact_normalization")
    _attributes(fact)
    blocks = repo.read(list(fact.evidence_refs))
    if any(block.role != fact.role or block.supplier != fact.supplier
           or block.model != fact.model for block in blocks):
        raise ValueError("fact_scope")


def _basis(fact: FieldFact) -> tuple[str, ...] | None:
    attrs = _attributes(fact)
    if attrs.get("approximate") == "true":
        return None
    field = fact.field_key
    if field == FieldKey.MEMORY:
        return ("installed",) if fact.qualifier == "installed" else None
    if field == FieldKey.STORAGE:
        return (fact.qualifier,) if fact.qualifier in {"nominal", "usable"} else None
    if field == FieldKey.SCREEN_SIZE:
        return ("diagonal",) if fact.qualifier == "diagonal" else None
    if field == FieldKey.WEIGHT:
        return ("device",) if fact.qualifier == "device" else None
    if field == FieldKey.WARRANTY:
        service = attrs.get("service_type")
        if fact.qualifier == "standard" and _known(service):
            return ("standard", service)
        return None
    if field == FieldKey.QUANTITY:
        item = attrs.get("item_type")
        if fact.qualifier == "purchase" and _known(item) and fact.unit != "套":
            return ("purchase", item)
        return None
    if field == FieldKey.UNIT_PRICE:
        currency = attrs.get("currency")
        pricing_unit = attrs.get("pricing_unit")
        tax_basis = attrs.get("tax_basis")
        if (fact.qualifier in {"tax_included", "tax_excluded"}
                and tax_basis == fact.qualifier.removeprefix("tax_")
                and currency in {"CNY", "USD"} and _known(pricing_unit)
                and fact.unit == f"{currency}/{pricing_unit}"):
            return (tax_basis, currency, pricing_unit)
        return None
    if field == FieldKey.DELIVERY_DAYS:
        event = attrs.get("start_event")
        day_type = attrs.get("day_type")
        if (fact.qualifier == "delivery" and _known(event)
                and (day_type is None or day_type == fact.unit)):
            return ("delivery", event, fact.unit)
    return None


def _passes(comparator: str, actual: Decimal, required: Decimal) -> bool:
    return {
        "gte": actual >= required,
        "lte": actual <= required,
        "eq": actual == required,
        "gt": actual > required,
        "lt": actual < required,
    }[comparator]


def evaluate_field(
    repo: EvidenceRepository,
    supplier: str,
    field_key: FieldKey | str,
    facts: Sequence[FieldFact],
    *,
    scan_complete: bool,
    failed: bool = False,
) -> FieldCheckResult:
    """Evaluate one snapshot/supplier/field only after source-bound extraction.

    An incomplete scan can retain provisional facts but never a business status.
    """
    field = FieldKey(field_key)
    if failed and scan_complete:
        raise ValueError("invalid_processing_state")
    coverage = repo.coverage(supplier)
    for fact in facts:
        _validate_fact(repo, supplier, field, fact)
    refs = tuple(dict.fromkeys(ref for fact in facts for ref in fact.evidence_refs))
    requirements = [fact for fact in facts if fact.role == "requirement"]
    offered = [fact for fact in facts if fact.role != "requirement"]
    processing = ("failed" if failed else "complete" if scan_complete and coverage["complete"]
                  else "partial")
    status = None
    reason = "extraction_failed" if failed else "coverage_incomplete"

    if processing == "complete":
        if not requirements:
            status, reason = "证据不足", "requirement_missing"
        elif (any(_basis(fact) is None or fact.comparator == "unknown" for fact in requirements)
              or len({(fact.value, fact.unit, fact.comparator, _basis(fact))
                      for fact in requirements}) != 1):
            status, reason = "待人工确认", "requirement_unclear"
        elif not offered:
            status, reason = "证据不足", "supplier_fact_missing"
        elif (any(_basis(fact) is None or fact.comparator != "eq" or not fact.model
                  for fact in offered)
              or len({fact.model for fact in offered}) != 1
              or any(fact.model is not None and fact.model != offered[0].model
                     for fact in requirements)
              or len({_basis(fact) for fact in offered}) != 1
              or _basis(requirements[0]) != _basis(offered[0])
              or any(fact.unit != requirements[0].unit for fact in offered)):
            status, reason = "待人工确认", "semantics_unclear"
        elif len({fact.value for fact in offered}) != 1:
            status, reason = "资料冲突", "conflicting_supplier_values"
        else:
            passes = _passes(requirements[0].comparator,
                             offered[0].value, requirements[0].value)
            status, reason = (("满足", "threshold_met") if passes
                              else ("不满足", "threshold_failed"))

    reason_refs = (tuple(dict.fromkeys(ref for fact in offered for ref in fact.evidence_refs))
                   if reason == "conflicting_supplier_values" else refs)
    fact_payloads = [asdict(fact) | {"value": str(fact.value)} for fact in facts]
    identity = digest([
        repo.snapshot_id, supplier, field.value, RULE_VERSION,
        sorted(fact_payloads, key=lambda fact: (fact["fact_id"], fact["raw_value"])),
        processing, status, reason,
        sorted(refs),
    ])
    return FieldCheckResult(
        check_result_id=identity,
        snapshot_id=repo.snapshot_id,
        supplier=supplier,
        field_key=field,
        processing_status=processing,
        business_status=status,
        reason_code=reason,
        evidence_refs=refs,
        reason_evidence_refs=reason_refs,
        fact_ids=tuple(fact.fact_id for fact in facts),
        rule_version=RULE_VERSION,
    )
