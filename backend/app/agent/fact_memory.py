"""Bounded, source-validated fact sets for durable Agent memory."""

import json
import re
from dataclasses import asdict
from decimal import Decimal

from ..evidence.repository import EvidenceRepository, digest
from ..extraction.fields import FieldCandidate, _validate_candidate
from ..extraction.memory import FactSet
from ..extraction.validation import Candidate, validate_memory
from ..field_contract import FieldFactSet, FieldKey

MAX_SETS = 24
MAX_FACTS_PER_SET = 256
MAX_SCANNED_PER_SET = 25_000
MAX_ENCODED_BYTES = 512 * 1024
_CAPACITY = re.compile(r"(?<![\d.])(\d+(?:\.\d+)?)\s*(MiB|GiB|GB)\b", re.IGNORECASE)


def _plain(value):
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_plain(item) for item in value]
    return value


def _payload(fact_set: FactSet | FieldFactSet) -> dict:
    kind = "field" if isinstance(fact_set, FieldFactSet) else "memory"
    value = asdict(fact_set)
    value["kind"] = kind
    if kind == "memory":
        value["field_key"] = FieldKey.MEMORY.value
        value["snapshot_id"] = None  # legacy FactSet has no snapshot field
        value["coverage_issues"] = []
    return _plain(value)


def encode_fact_sets(
    repo: EvidenceRepository, fact_sets: dict[str, FactSet | FieldFactSet],
) -> list[dict] | None:
    """Return all latest sets, or None when any limit prevents safe reuse."""
    latest = {}
    for fact_set in fact_sets.values():
        field = (fact_set.field_key if isinstance(fact_set, FieldFactSet)
                 else FieldKey.MEMORY)
        latest[(fact_set.supplier, field)] = fact_set
    if len(latest) > MAX_SETS:
        return None
    result = []
    for fact_set in latest.values():
        if (len(fact_set.facts) > MAX_FACTS_PER_SET
                or len(fact_set.scanned_ids) > MAX_SCANNED_PER_SET):
            return None
        if (isinstance(fact_set, FieldFactSet)
                and fact_set.snapshot_id != repo.snapshot_id):
            return None
        result.append(_payload(fact_set))
    if len(json.dumps(result, ensure_ascii=False,
                      separators=(",", ":")).encode("utf-8")) > MAX_ENCODED_BYTES:
        return None
    return result


def _strings(value, *, limit: int) -> tuple[str, ...]:
    if (not isinstance(value, list) or len(value) > limit
            or any(not isinstance(item, str) or not 0 < len(item) <= 128
                   for item in value)
            or len(value) != len(set(value))):
        raise ValueError("memory_scope")
    return tuple(value)


def _field_fact(repo: EvidenceRepository, supplier: str,
                field: FieldKey, value: dict):
    if not isinstance(value, dict) or not isinstance(value.get("raw_value"), str):
        raise TypeError("memory_scope")
    candidate = FieldCandidate(
        field_key=field, raw_value=value["raw_value"],
        raw_unit=value["raw_unit"],
        evidence_refs=list(_strings(value["evidence_refs"], limit=2)),
    )
    validated = _validate_candidate(candidate, repo, supplier)
    if _plain(asdict(validated)) != value:
        raise ValueError("memory_scope")
    return validated


def _memory_fact(repo: EvidenceRepository, supplier: str, value: dict):
    if not isinstance(value, dict):
        raise TypeError("memory_scope")
    refs = _strings(value["evidence_refs"], limit=2)
    text = " ".join(block.text for block in repo.read(list(refs)))
    for raw, unit in _CAPACITY.findall(text):
        try:
            canonical_unit = {"gb": "GB", "gib": "GiB", "mib": "MiB"}[unit.casefold()]
            candidate = Candidate(raw_value=raw, raw_unit=canonical_unit,
                                  qualifier=value["qualifier"],
                                  comparator=value["comparator"],
                                  evidence_refs=list(refs))
            validated = validate_memory(candidate, repo, supplier)
        except (ValueError, TypeError):
            continue
        if _plain(asdict(validated)) == value:
            return validated
    raise ValueError("memory_scope")


def decode_fact_sets(repo: EvidenceRepository, data: object) -> dict[str, FactSet | FieldFactSet]:
    """Reject modified or foreign facts before restoring any tool state."""
    if not isinstance(data, list) or len(data) > MAX_SETS:
        raise ValueError("memory_scope")
    if len(json.dumps(data, ensure_ascii=False,
                      separators=(",", ":")).encode("utf-8")) > MAX_ENCODED_BYTES:
        raise ValueError("memory_scope")
    restored = {}
    seen_pairs = set()
    try:
        for item in data:
            if not isinstance(item, dict) or item.get("kind") not in {"field", "memory"}:
                raise ValueError("memory_scope")
            kind, supplier = item["kind"], item["supplier"]
            repo.assert_supplier(supplier)
            field = FieldKey(item["field_key"])
            if kind == "memory" and field != FieldKey.MEMORY:
                raise ValueError("memory_scope")
            pair = (supplier, field)
            if pair in seen_pairs:
                raise ValueError("memory_scope")
            seen_pairs.add(pair)
            scanned = _strings(item["scanned_ids"], limit=MAX_SCANNED_PER_SET)
            allowed = {block.evidence_id for block in repo.supplier_blocks(supplier)}
            if not set(scanned) <= allowed or type(item["complete"]) is not bool:
                raise ValueError("memory_scope")
            if (item["complete"] and (set(scanned) != allowed
                                      or not repo.coverage(supplier)["complete"])):
                raise ValueError("memory_scope")
            facts_json = item["facts"]
            if not isinstance(facts_json, list) or len(facts_json) > MAX_FACTS_PER_SET:
                raise ValueError("memory_scope")
            if kind == "field":
                if item["snapshot_id"] != repo.snapshot_id:
                    raise ValueError("memory_scope")
                issues = _strings(item["coverage_issues"], limit=MAX_SCANNED_PER_SET)
                if item["complete"] and issues:
                    raise ValueError("memory_scope")
                facts = tuple(_field_fact(repo, supplier, field, fact)
                              for fact in facts_json)
                identity = digest([
                    repo.snapshot_id, supplier, field.value, scanned, item["complete"],
                    [asdict(fact) | {"value": str(fact.value)} for fact in facts],
                ])
                fact_set = FieldFactSet(
                    fact_set_id=identity, snapshot_id=repo.snapshot_id,
                    supplier=supplier, field_key=field, facts=facts,
                    scanned_ids=scanned, complete=item["complete"],
                    coverage_issues=issues,
                )
            else:
                if item["snapshot_id"] is not None or item["coverage_issues"] != []:
                    raise ValueError("memory_scope")
                facts = tuple(_memory_fact(repo, supplier, fact)
                              for fact in facts_json)
                identity = digest([
                    repo.snapshot_id, supplier, scanned, item["complete"],
                    [asdict(fact) | {"value": str(fact.value)} for fact in facts],
                ])
                fact_set = FactSet(identity, supplier, facts, scanned, item["complete"])
            if item["fact_set_id"] != identity or _payload(fact_set) != item:
                raise ValueError("memory_scope")
            restored[identity] = fact_set
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("memory_scope") from exc
    return restored
