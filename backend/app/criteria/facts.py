"""Verified dynamic facts and finite Decimal rules, independent of fixed fields."""

import re
from decimal import Decimal, InvalidOperation
from typing import Annotated, Literal

from pydantic import Field, model_validator

from ..evidence.repository import EvidenceRepository, digest
from .models import (
    ChecklistDefinition,
    Contract,
    Criterion,
    Identifier,
    Label,
    ProcurementItem,
)
from .validation import (
    COMPLEX,
    NUMBER_PATTERN,
    compact,
    duration_calendar,
    duration_measurements,
    duration_start_events,
    duration_unit_calendar,
    enum_choices,
    item_supported,
    mentions,
    metric_clause,
    money_basis,
    negated,
    number_pairs,
    rule_supported,
    source_comparator,
    source_span,
)

RULE_VERSION = "dynamic-rules-v2"
SUPPORTED_RULE_VERSIONS = {"dynamic-rules-v1", RULE_VERSION}
_VALUE_NUMBER = re.compile(r"^-?\d{1,12}(?:\.\d{1,6})?$")
_UNCERTAIN = re.compile(r"最大|最高|最多支持|支持扩展|参考|宣传|另一型号|其他型号|"
                        r"不属于本次|次日|翌日|next day", re.IGNORECASE)


class FactCandidate(Contract):
    criterion_id: Identifier
    item_id: Identifier
    supplier: Label
    value: Annotated[str, Field(min_length=1, max_length=500, pattern=r"\S")] | bool
    unit: str = Field(max_length=120)
    evidence_refs: list[str] = Field(min_length=1, max_length=4)
    quote: str = Field(min_length=1, max_length=2000, pattern=r"\S")
    calendar: Literal["natural_days", "working_days", "months"] | None = None
    start_event: Label | None = None
    currency: Literal["CNY", "USD", "EUR"] | None = None
    per_unit: Label | None = None
    tax_basis: Literal["included", "excluded", "unspecified"] | None = None

    @model_validator(mode="after")
    def refs_unique(self):
        if (len(set(self.evidence_refs)) != len(self.evidence_refs)
                or any(not ref.strip() or len(ref) > 64 for ref in self.evidence_refs)):
            raise ValueError("invalid_fact_refs")
        if self.unit != self.unit.strip():
            raise ValueError("invalid_fact_unit")
        return self


def _definition(definition):
    return (definition if isinstance(definition, ChecklistDefinition)
            else ChecklistDefinition.model_validate(definition))


def _criterion(definition, criterion_id, item_id):
    criterion = next((c for c in definition.criteria if c.criterion_id == criterion_id), None)
    if criterion is None:
        raise ValueError("unknown_criterion")
    item = next((i for i in definition.items if i.item_id == item_id), None)
    if item is None or criterion.item_id != item_id:
        raise ValueError("unknown_item")
    return criterion, item


def _fact_clause(text: str, criterion: Criterion, item: ProcurementItem, *, model: str | None,
                 multi_item: bool) -> str:
    clause = metric_clause(text, criterion.name)
    if clause is None:
        raise ValueError("fact_criterion")
    if multi_item and not item_supported(clause, item, model=model):
        raise ValueError("fact_item")
    return clause


def _presence_value(text: str, description: str) -> bool:
    if not mentions(text, description):
        raise ValueError("fact_value")
    return not negated(text, description)


def validate_fact(candidate: dict | FactCandidate, definition: ChecklistDefinition | dict,
                  repo: EvidenceRepository, supplier: str, *, rule_version=RULE_VERSION) -> dict:
    if rule_version not in SUPPORTED_RULE_VERSIONS:
        raise ValueError("rule_version")
    legacy_duration = rule_version == "dynamic-rules-v1"
    candidate = (candidate if isinstance(candidate, FactCandidate)
                 else FactCandidate.model_validate(candidate))
    definition = _definition(definition)
    criterion, item = _criterion(definition, candidate.criterion_id, candidate.item_id)
    repo.assert_supplier(supplier)
    if candidate.supplier != supplier:
        raise ValueError("supplier_scope")
    blocks, text = source_span(repo, candidate.evidence_refs, candidate.quote)
    if any(block.role not in {"quote", "spec"} or block.supplier != supplier for block in blocks):
        raise ValueError("supplier_scope")
    model = blocks[0].model
    clause = _fact_clause(text, criterion, item, model=model, multi_item=len(definition.items) > 1)
    rule = criterion.rule
    ambiguous = bool(COMPLEX.search(text) or _UNCERTAIN.search(text))
    comparator = source_comparator(clause)
    observed = {"calendar": None, "start_event": None, "currency": None,
                "per_unit": None, "tax_basis": None}
    if rule.type in {"numeric", "money", "duration"}:
        if not isinstance(candidate.value, str) or not _VALUE_NUMBER.fullmatch(candidate.value):
            raise ValueError("fact_value")
        if rule.type in {"money", "duration"} and Decimal(candidate.value) < 0:
            raise ValueError("fact_value")
        legacy_pairs = legacy_duration or rule.type != "duration"
        values = number_pairs(clause, candidate.unit, legacy_duration=legacy_pairs)
        if Decimal(candidate.value) not in values:
            raise ValueError("fact_value")
        ambiguous = ambiguous or len(values) != 1 or len(number_pairs(text, candidate.unit, legacy_duration=legacy_pairs)) != 1
        if rule.type == "money":
            observed["currency"], observed["per_unit"], observed["tax_basis"] = money_basis(clause, candidate.unit)
        if rule.type == "duration":
            observed["calendar"] = duration_calendar(clause, candidate.unit, legacy_duration=legacy_duration)
            ambiguous = (ambiguous or observed["calendar"] is None
                         or len(duration_measurements(text, legacy_duration=legacy_duration)) != 1
                         or len(re.findall(NUMBER_PATTERN, clause)) != 1)
            possible = duration_start_events(clause, hints=(candidate.start_event, rule.start_event), legacy_duration=legacy_duration)
            if len(possible) == 1:
                observed["start_event"] = possible[0]
            elif possible:
                ambiguous = True
    elif rule.type == "presence":
        if not isinstance(candidate.value, bool) or candidate.value != _presence_value(clause, rule.description):
            raise ValueError("fact_value")
        if candidate.unit:
            raise ValueError("fact_unit")
    else:
        if (not isinstance(candidate.value, str) or not mentions(clause, candidate.value)
                or (rule.type == "enum" and negated(clause, candidate.value))):
            raise ValueError("fact_value")
        if rule.type == "enum" and candidate.unit:
            raise ValueError("fact_unit")
        if rule.type == "enum":
            ambiguous = ambiguous or len([value for value in rule.values if mentions(text, value)]) > 1
            ambiguous = ambiguous or enum_choices(clause, criterion.name) != [candidate.value]
    for name, observed_value in observed.items():
        proposed = getattr(candidate, name)
        if proposed is not None and proposed != observed_value:
            raise ValueError(f"fact_{name}")
    applicable = (item_supported(clause, item, model=model)
                  and not bool(re.search(r"另一型号|其他型号|不属于本次", clause)))
    if item.specification and model and not mentions(item.specification, model):
        applicable = False
    ambiguous = ambiguous or not applicable
    canonical = candidate.model_dump(mode="json") | observed
    canonical.update(evidence_refs=[block.evidence_id for block in blocks], quote=compact(candidate.quote),
                     case_id=repo.case_id, snapshot_id=repo.snapshot_id, model=model,
                     comparator=comparator, ambiguous=ambiguous, applicable=applicable)
    return canonical | {"fact_id": digest([repo.snapshot_id, canonical])}


def revalidate_fact(fact: dict, definition: ChecklistDefinition | dict,
                    repo: EvidenceRepository, supplier: str, *, rule_version=RULE_VERSION) -> dict:
    candidate = {key: fact[key] for key in FactCandidate.model_fields if key in fact}
    verified = validate_fact(candidate, definition, repo, supplier, rule_version=rule_version)
    if verified != fact:
        raise ValueError("fact_integrity")
    return verified


# Only units with an explicit physical relationship share a dimension.
_UNITS = {
    "mg": ("mass", "0.001"), "g": ("mass", "1"), "克": ("mass", "1"),
    "kg": ("mass", "1000"), "千克": ("mass", "1000"), "公斤": ("mass", "1000"),
    "斤": ("mass", "500"),
    "mm": ("length", "1"), "毫米": ("length", "1"), "cm": ("length", "10"),
    "厘米": ("length", "10"), "m": ("length", "1000"), "米": ("length", "1000"),
    "inch": ("length", "25.4"), "in": ("length", "25.4"), "英寸": ("length", "25.4"),
    "ml": ("volume", "1"), "毫升": ("volume", "1"), "l": ("volume", "1000"), "升": ("volume", "1000"),
    "mb": ("decimal_capacity", "1"), "gb": ("decimal_capacity", "1000"),
    "tb": ("decimal_capacity", "1000000"),
    "mib": ("binary_capacity", "1"), "gib": ("binary_capacity", "1024"),
    "tib": ("binary_capacity", "1048576"),
}


def _numeric(value, unit):
    if not isinstance(value, str) or not _VALUE_NUMBER.fullmatch(value):
        raise ValueError("fact_value")
    if unit.casefold() in {"mb", "gb", "tb", "mib", "gib", "tib"} and unit not in {
            "MB", "GB", "TB", "MiB", "GiB", "TiB"}:
        return Decimal(value), f"exact:{unit}"
    dimension, multiplier = _UNITS.get(unit.casefold(), (f"exact:{unit}", "1"))
    return Decimal(value) * Decimal(multiplier), dimension


def _passes(rule, actual):
    if rule.operator == "between":
        return Decimal(rule.lower) <= actual <= Decimal(rule.upper)
    required = Decimal(rule.value)
    return {"eq": actual == required, "gte": actual >= required, "lte": actual <= required,
            "gt": actual > required, "lt": actual < required}[rule.operator]


def _evaluate(criterion, item, offered, *, rule_version):
    legacy_duration = rule_version == "dynamic-rules-v1"
    rule = criterion.rule
    if criterion.requires_manual_review or not rule_supported(criterion, item, legacy_duration=legacy_duration):
        return "待人工确认", "requirement_manual"
    if not offered:
        return "证据不足", "supplier_fact_missing"
    if (any(not fact.get("applicable", False) for fact in offered)
            or len({fact.get("model") for fact in offered}) > 1):
        return "待人工确认", "item_model_unclear"
    try:
        if rule.type == "numeric":
            normalized = [_numeric(fact["value"], fact["unit"]) for fact in offered]
            _, dimension = _numeric(rule.value or rule.lower, rule.unit)
            if any(unit != dimension for _, unit in normalized):
                return "待人工确认", "unit_basis_mismatch"
            scale, _ = _numeric("1", rule.unit)
            actual = [value / scale for value, _ in normalized]
        elif rule.type == "money":
            if (rule.tax_basis == "unspecified" or any(
                    (fact.get("currency"), fact.get("per_unit"), fact.get("tax_basis"))
                    != (rule.currency, rule.per_unit, rule.tax_basis) for fact in offered)):
                return "待人工确认", "money_basis_mismatch"
            actual = [Decimal(fact["value"]) for fact in offered]
        elif rule.type == "duration":
            if any(duration_unit_calendar(fact.get("unit", ""), legacy_duration=legacy_duration) != rule.calendar
                   or (fact.get("calendar"), fact.get("start_event")) != (rule.calendar, rule.start_event)
                   for fact in offered):
                return "待人工确认", "duration_basis_mismatch"
            actual = [Decimal(fact["value"]) for fact in offered]
        else:
            actual = [fact["value"] for fact in offered]
        if len(set(actual)) > 1:
            return "资料冲突", "conflicting_supplier_values"
        if any(fact.get("ambiguous", True) for fact in offered):
            return "待人工确认", "semantics_unclear"
        comparators = {fact.get("comparator") for fact in offered}
        if rule.type == "duration" and comparators <= {"eq", "lt", "lte"}:
            if len(comparators) != 1:
                return "资料冲突", "conflicting_supplier_values"
            if comparators != {"eq"} and rule.operator not in {"lt", "lte"}:
                return "待人工确认", "semantics_unclear"
        elif comparators != {"eq"}:
            return "待人工确认", "semantics_unclear"
        if rule.type == "enum":
            passes = actual[0] in rule.values
        elif rule.type == "presence":
            passes = actual[0] == rule.must_be_present
        elif rule.type == "duration" and comparators == {"lt"} and rule.operator == "lt":
            passes = actual[0] <= Decimal(rule.value)
        else:
            passes = _passes(rule, actual[0])
    except (KeyError, TypeError, ValueError, InvalidOperation):
        return "待人工确认", "semantics_unclear"
    return ("满足", "threshold_met") if passes else ("不满足", "threshold_failed")


def evaluate_checklist(definition: ChecklistDefinition | dict, facts: list[dict],
                       coverage: dict[str, bool], *, snapshot_id: str, rule_version=RULE_VERSION) -> list[dict]:
    """Enumerate every enabled criterion and supplier after complete extraction."""
    definition = _definition(definition)
    if rule_version not in SUPPORTED_RULE_VERSIONS:
        raise ValueError("rule_version")
    if any(not isinstance(supplier, str) or type(complete) is not bool
           for supplier, complete in coverage.items()):
        raise ValueError("coverage_state")
    for fact in facts:
        if fact.get("snapshot_id") != snapshot_id:
            raise ValueError("fact_snapshot")
        _criterion(definition, fact.get("criterion_id"), fact.get("item_id"))
        if fact.get("supplier") not in coverage:
            raise ValueError("supplier_scope")
    items = {item.item_id: item for item in definition.items}
    results = []
    for supplier, complete in coverage.items():
        for criterion in definition.criteria:
            if not criterion.enabled:
                continue
            offered = [fact for fact in facts if fact["supplier"] == supplier
                       and fact["criterion_id"] == criterion.criterion_id]
            offered = sorted({fact["fact_id"]: fact for fact in offered}.values(), key=lambda fact: fact["fact_id"])
            refs = list(dict.fromkeys(ref for fact in offered for ref in fact["evidence_refs"]))
            status, reason = (_evaluate(criterion, items[criterion.item_id], offered, rule_version=rule_version)
                              if complete else (None, "coverage_incomplete"))
            processing = "complete" if complete else "partial"
            result = {"snapshot_id": snapshot_id, "supplier": supplier,
                      "field_key": criterion.criterion_id, "criterion_id": criterion.criterion_id,
                      "item_id": criterion.item_id, "item_name": items[criterion.item_id].name,
                      "criterion_name": criterion.name,
                      "processing_status": processing, "business_status": status,
                      "reason_code": reason, "evidence_refs": refs, "reason_evidence_refs": refs,
                      "fact_ids": [fact["fact_id"] for fact in offered],
                      "rule_version": rule_version, "is_formal": complete}
            identity = digest([definition.model_dump(mode="json"), offered, result])
            results.append({"check_result_id": identity, **result})
    return results
