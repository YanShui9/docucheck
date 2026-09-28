"""Full extraction scans source blocks and validates model-proposed spans."""

import json
from dataclasses import replace
from pathlib import Path

from langchain_core.messages import AIMessage

from backend.app.evidence.repository import EvidenceRepository, load_case
from backend.app.extraction.fields import extract_fields
from backend.app.field_contract import FieldKey
from backend.app.rules.fields import evaluate_field

PACK = Path(__file__).resolve().parents[2] / "examples" / "dev-pack-001"


class StorageCandidates:
    def __init__(self, *, omit_spec: bool = False) -> None:
        self.omit_spec = omit_spec
        self.seen: set[str] = set()
        self.calls = 0

    def __call__(self, operation, messages, schema):
        assert operation == "extract"
        self.calls += 1
        facts = []
        for block in json.loads(messages[-1].content)["evidence"]:
            self.seen.add(block["evidence_id"])
            if "标称存储至少 512 GB" in block["text"]:
                facts.append({"field_key": "storage", "raw_value": "512",
                              "raw_unit": "GB", "evidence_refs": [block["evidence_id"]]})
            elif "标称 SSD 1 TB" in block["text"] and not self.omit_spec:
                facts.append({"field_key": "storage", "raw_value": "1",
                              "raw_unit": "TB", "evidence_refs": [block["evidence_id"]]})
        return AIMessage(content="", tool_calls=[{"id": f"extract-{self.calls}",
                                                  "name": "FieldCandidates",
                                                  "args": {"facts": facts}}])


def test_storage_full_extraction_reads_every_applicable_block() -> None:
    repo = load_case(PACK)
    model = StorageCandidates()

    result = extract_fields(repo, "vendor-a", [FieldKey.STORAGE], model)[FieldKey.STORAGE]

    assert model.seen == {block.evidence_id for block in repo.supplier_blocks("vendor-a")}
    assert result.complete
    assert len(result.facts) == 2
    check = evaluate_field(repo, "vendor-a", FieldKey.STORAGE, result.facts,
                           scan_complete=result.complete)
    assert check.processing_status == "complete"
    assert check.business_status == "满足"


def test_model_empty_or_missing_candidate_cannot_prove_field_absent() -> None:
    repo = load_case(PACK)
    model = StorageCandidates(omit_spec=True)

    result = extract_fields(repo, "vendor-a", [FieldKey.STORAGE], model)[FieldKey.STORAGE]

    assert len(result.facts) == 1
    assert not result.complete
    assert any("unverified_source" in issue for issue in result.coverage_issues)
    check = evaluate_field(repo, "vendor-a", FieldKey.STORAGE, result.facts,
                           scan_complete=result.complete)
    assert check.business_status is None


def test_quantity_value_in_adjacent_xlsx_cell_uses_label_and_value_sources() -> None:
    repo = load_case(PACK)

    def model_call(operation, messages, schema):
        batch = json.loads(messages[-1].content)["evidence"]
        facts = []
        for block in batch:
            if "采购数量 10 台" in block["text"]:
                facts.append({"field_key": "quantity", "raw_value": "10", "raw_unit": "台",
                              "evidence_refs": [block["evidence_id"]]})
        by_cell = {item["cell"]: item for item in batch if item["sheet"] == "报价"}
        if "A7" in by_cell and "B7" in by_cell:
            facts.append({"field_key": "quantity", "raw_value": "10", "raw_unit": "台",
                          "evidence_refs": [by_cell["A7"]["evidence_id"],
                                            by_cell["B7"]["evidence_id"]]})
        return AIMessage(content="", tool_calls=[{"id": "quantities", "name": "FieldCandidates",
                                                  "args": {"facts": facts}}])

    result = extract_fields(repo, "vendor-a", [FieldKey.QUANTITY], model_call)[FieldKey.QUANTITY]

    assert result.complete
    assert len(result.facts) == 2
    assert evaluate_field(repo, "vendor-a", FieldKey.QUANTITY, result.facts,
                          scan_complete=True).business_status == "满足"


def test_second_value_in_same_source_block_cannot_be_silently_omitted() -> None:
    original = load_case(PACK)
    documents = []
    for document in original.documents:
        if document.role != "spec":
            documents.append(document)
            continue
        blocks = tuple(replace(block, text="标称 SSD 1 TB；标称 SSD 2 TB")
                       if block.text == "标称 SSD 1 TB" else block
                       for block in document.parsed.blocks)
        documents.append(replace(document, parsed=replace(document.parsed, blocks=blocks)))
    repo = EvidenceRepository(original.case_id, original.revision + 1, documents)

    result = extract_fields(repo, "vendor-a", [FieldKey.STORAGE],
                            StorageCandidates())[FieldKey.STORAGE]

    assert not result.complete
    assert any("unverified_value" in issue for issue in result.coverage_issues)
    assert evaluate_field(repo, "vendor-a", FieldKey.STORAGE, result.facts,
                          scan_complete=result.complete).business_status is None


def test_value_from_other_field_in_same_source_is_rejected() -> None:
    original = load_case(PACK)
    documents = []
    for document in original.documents:
        if document.role != "spec":
            documents.append(document)
            continue
        blocks = tuple(replace(block, text="标称 SSD 1 TB；已装内存 16 GB")
                       if block.text == "标称 SSD 1 TB" else block
                       for block in document.parsed.blocks)
        documents.append(replace(document, parsed=replace(document.parsed, blocks=blocks)))
    repo = EvidenceRepository(original.case_id, original.revision + 1, documents)

    def wrong_candidate(operation, messages, schema):
        facts = []
        for block in json.loads(messages[-1].content)["evidence"]:
            if block["text"] == "标称 SSD 1 TB；已装内存 16 GB":
                facts.append({"field_key": "storage", "raw_value": "16", "raw_unit": "GB",
                              "evidence_refs": [block["evidence_id"]]})
        return AIMessage(content="", tool_calls=[{"id": "bad", "name": "FieldCandidates",
                                                  "args": {"facts": facts}}])

    result = extract_fields(repo, "vendor-a", [FieldKey.STORAGE], wrong_candidate)[FieldKey.STORAGE]
    assert not result.complete
    assert not result.facts
    assert any("candidate_invalid_uncovered" in issue for issue in result.coverage_issues)


def test_extra_other_field_candidate_does_not_poison_complete_storage() -> None:
    repo = load_case(PACK)

    class ExtraMemory(StorageCandidates):
        def __call__(self, operation, messages, schema):
            reply = super().__call__(operation, messages, schema)
            batch = json.loads(messages[-1].content)["evidence"]
            memory = next((block for block in batch if "已装内存 16 GB" in block["text"]), None)
            if memory:
                reply.tool_calls[0]["args"]["facts"].append({
                    "field_key": "memory", "raw_value": "16", "raw_unit": "GB",
                    "evidence_refs": [memory["evidence_id"]],
                })
            return reply

    result = extract_fields(repo, "vendor-a", [FieldKey.STORAGE],
                            ExtraMemory())[FieldKey.STORAGE]

    assert result.complete
    assert len(result.facts) == 2


def test_repeated_same_number_with_distinct_qualifiers_needs_more_coverage() -> None:
    original = load_case(PACK)
    documents = []
    for document in original.documents:
        if document.role != "spec":
            documents.append(document)
            continue
        blocks = tuple(replace(block, text="标称 SSD 1 TB；可用存储 1 TB")
                       if block.text == "标称 SSD 1 TB" else block
                       for block in document.parsed.blocks)
        documents.append(replace(document, parsed=replace(document.parsed, blocks=blocks)))
    repo = EvidenceRepository(original.case_id, original.revision + 1, documents)

    result = extract_fields(repo, "vendor-a", [FieldKey.STORAGE],
                            StorageCandidates())[FieldKey.STORAGE]
    assert not result.complete
    assert any("unverified_value" in issue for issue in result.coverage_issues)
    assert evaluate_field(repo, "vendor-a", FieldKey.STORAGE, result.facts,
                          scan_complete=result.complete).business_status is None
