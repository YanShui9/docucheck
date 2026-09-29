"""Source-bound fact sets remain usable across Agent processes."""

import json
from dataclasses import asdict, replace
from pathlib import Path

import pytest

from backend.app.agent.fact_memory import decode_fact_sets, encode_fact_sets
from backend.app.evidence.repository import digest, load_case
from backend.app.extraction.fields import FieldCandidate, _validate_candidate
from backend.app.extraction.memory import FactSet
from backend.app.extraction.validation import Candidate, validate_memory
from backend.app.field_contract import FieldFactSet, FieldKey

PACK = Path(__file__).resolve().parents[2] / "examples" / "dev-pack-001"


def _field_set(repo):
    repo = load_case(PACK)
    block = next(block for block in repo.blocks
                 if block.supplier == "vendor-a" and "1 TB" in block.text)
    fact = _validate_candidate(FieldCandidate(
        field_key=FieldKey.STORAGE, raw_value="1", raw_unit="TB",
        evidence_refs=[block.evidence_id],
    ), repo, "vendor-a")
    identity = digest([repo.snapshot_id, "vendor-a", FieldKey.STORAGE.value,
                       (block.evidence_id,), False,
                       [asdict(fact) | {"value": str(fact.value)}]])
    fact_set = FieldFactSet(
        fact_set_id=identity, snapshot_id=repo.snapshot_id,
        supplier="vendor-a", field_key=FieldKey.STORAGE,
        facts=(fact,), scanned_ids=(block.evidence_id,),
        complete=False, coverage_issues=("example",),
    )
    return fact_set


def test_field_fact_set_round_trip():
    repo = load_case(PACK)
    fact_set = _field_set(repo)
    encoded = encode_fact_sets(repo, {fact_set.fact_set_id: fact_set})
    assert encoded is not None
    decoded = decode_fact_sets(repo, json.loads(json.dumps(encoded)))
    assert list(decoded.values()) == [fact_set]


def test_legacy_memory_fact_set_round_trip():
    repo = load_case(PACK)
    block = next(block for block in repo.blocks
                 if block.supplier == "vendor-a" and "32 GB" in block.text
                 and "内存" in block.text)
    fact = validate_memory(Candidate(
        raw_value="32", raw_unit="GB", qualifier="installed",
        comparator="eq", evidence_refs=[block.evidence_id],
    ), repo, "vendor-a")
    scanned = (block.evidence_id,)
    identity = digest([repo.snapshot_id, "vendor-a", scanned, False,
                       [asdict(fact) | {"value": str(fact.value)}]])
    fact_set = FactSet(identity, "vendor-a", (fact,), scanned, False)
    encoded = encode_fact_sets(repo, {identity: fact_set})
    assert decode_fact_sets(repo, encoded)[identity] == fact_set


@pytest.mark.parametrize("change", [
    lambda data: data[0]["facts"][0].__setitem__("raw_value", "2"),
    lambda data: data[0].__setitem__("supplier", "vendor-b"),
    lambda data: data[0]["facts"][0].__setitem__("evidence_refs", ["foreign"]),
    lambda data: data[0].__setitem__("snapshot_id", "foreign"),
])
def test_changed_fact_or_scope_is_rejected(change):
    repo = load_case(PACK)
    fact_set = _field_set(repo)
    encoded = encode_fact_sets(repo, {fact_set.fact_set_id: fact_set})
    change(encoded)
    with pytest.raises(ValueError, match="memory_scope"):
        decode_fact_sets(repo, encoded)


def test_oversized_fact_collection_cannot_be_partially_saved():
    repo = load_case(PACK)
    fact_set = _field_set(repo)
    oversized = replace(fact_set, facts=fact_set.facts * 257)
    assert encode_fact_sets(repo, {oversized.fact_set_id: oversized}) is None


def test_complete_flag_requires_entire_supplier_scan():
    repo = load_case(PACK)
    fact_set = _field_set(repo)
    encoded = encode_fact_sets(repo, {fact_set.fact_set_id: fact_set})
    encoded[0]["complete"] = True
    encoded[0]["coverage_issues"] = []
    encoded[0]["fact_set_id"] = digest([
        repo.snapshot_id, "vendor-a", "storage", fact_set.scanned_ids,
        True, [asdict(fact_set.facts[0]) | {"value": str(fact_set.facts[0].value)}],
    ])
    with pytest.raises(ValueError, match="memory_scope"):
        decode_fact_sets(repo, encoded)


@pytest.mark.parametrize("change", [
    lambda data: data[0].pop("scanned_ids"),
    lambda data: data[0].__setitem__("facts", "not-a-list"),
    lambda data: data[0].__setitem__("kind", "unknown"),
    lambda data: data[0].__setitem__("scanned_ids", ["same", "same"]),
])
def test_malformed_fact_set_is_rejected(change):
    repo = load_case(PACK)
    fact_set = _field_set(repo)
    encoded = encode_fact_sets(repo, {fact_set.fact_set_id: fact_set})
    change(encoded)
    with pytest.raises(ValueError, match="memory_scope"):
        decode_fact_sets(repo, encoded)


def test_encoded_byte_limit_discards_all_fact_sets():
    repo = load_case(PACK)
    fact_set = _field_set(repo)
    oversized = replace(fact_set, scanned_ids=tuple(
        f"{index:064x}" for index in range(10_000)))
    assert encode_fact_sets(repo, {oversized.fact_set_id: oversized}) is None
