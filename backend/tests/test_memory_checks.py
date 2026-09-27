from dataclasses import replace
from importlib import import_module
from pathlib import Path

import pytest

from backend.app.evidence.repository import EvidenceRepository, load_case

PACK = Path(__file__).resolve().parents[2] / "examples/dev-pack-001"


def inputs():
    repo = load_case(PACK)
    validation = import_module("backend.app.extraction.validation")
    facts = []
    for block in repo.blocks:
        if "已装内存" in block.text and "GB" in block.text:
            candidate = {"raw_value": "32" if "32" in block.text else "16", "raw_unit": "GB",
                         "qualifier": "installed", "comparator": "gte" if block.role == "requirement" else "eq",
                         "evidence_refs": [block.evidence_id]}
            facts.append(validation.validate_memory(candidate, repo, "vendor-a"))
    return repo, facts


def test_memory_conflict_retains_three_sources():
    repo, facts = inputs()
    check = import_module("backend.app.rules.memory").evaluate_memory(repo, "vendor-a", facts, scan_complete=True)
    assert check.business_status == "资料冲突"
    assert len(check.evidence_refs) == 3


@pytest.mark.parametrize("value,status", [("8", "不满足"), ("16", "满足"), ("32", "满足")])
def test_consistent_values_use_decimal_rules(value, status):
    from decimal import Decimal
    repo, facts = inputs()
    # Rule unit boundary receives verified normalized facts; mutate only values
    # here to isolate the deterministic rule, not the extraction validator.
    facts = [replace(f, value=Decimal(value)) if f.role != "requirement" else f for f in facts]
    check = import_module("backend.app.rules.memory").evaluate_memory(repo, "vendor-a", facts, scan_complete=True)
    assert check.business_status == status


def test_incomplete_processing_never_becomes_missing_or_satisfied():
    repo, facts = inputs()
    rules = import_module("backend.app.rules.memory")
    assert rules.evaluate_memory(repo, "vendor-a", facts, scan_complete=False).business_status is None
    docs = list(repo.documents)
    docs[0] = replace(docs[0], parsed=replace(docs[0].parsed, status="partial"))
    partial = EvidenceRepository(repo.case_id, 2, docs)
    assert rules.evaluate_memory(partial, "vendor-a", [], scan_complete=True).business_status is None


def test_memory_result_identity_changes_with_processing_state():
    repo, facts = inputs()
    rules = import_module("backend.app.rules.memory")
    incomplete = rules.evaluate_memory(repo, "vendor-a", facts, scan_complete=False)
    complete = rules.evaluate_memory(repo, "vendor-a", facts, scan_complete=True)
    assert incomplete.processing_status == "partial"
    assert complete.processing_status == "complete"
    assert incomplete.check_result_id != complete.check_result_id


def test_memory_result_identity_changes_when_fact_content_changes():
    from decimal import Decimal

    repo, facts = inputs()
    rules = import_module("backend.app.rules.memory")
    offered = [fact for fact in facts if fact.role != "requirement"]
    requirement = [fact for fact in facts if fact.role == "requirement"]
    first = rules.evaluate_memory(repo, "vendor-a", [*requirement, offered[0]],
                                  scan_complete=True)
    changed = replace(offered[0], value=Decimal(17))
    second = rules.evaluate_memory(repo, "vendor-a", [*requirement, changed],
                                   scan_complete=True)
    assert first.business_status == second.business_status == "满足"
    assert first.check_result_id != second.check_result_id


def test_memory_mib_normalizes_to_gib_without_mixing_gb():
    validation = import_module("backend.app.extraction.validation")
    repo, _ = inputs()
    original = next(block for block in repo.blocks if block.role == "spec"
                    and "已装内存" in block.text)
    documents = []
    for document in repo.documents:
        if document.role == "spec":
            blocks = tuple(replace(block, text="已装内存 16384 MiB")
                           if block.text == original.text else block
                           for block in document.parsed.blocks)
            documents.append(replace(document, parsed=replace(document.parsed, blocks=blocks)))
        else:
            documents.append(document)
    changed_repo = EvidenceRepository(repo.case_id, repo.revision + 1, documents)
    source = next(block for block in changed_repo.blocks if block.role == "spec"
                  and "16384 MiB" in block.text)
    fact = validation.validate_memory(
        {"raw_value": "16384", "raw_unit": "MiB", "qualifier": "installed",
         "comparator": "eq", "evidence_refs": [source.evidence_id]},
        changed_repo, "vendor-a",
    )
    assert fact.value == 16
    assert fact.unit == "GiB"


@pytest.mark.parametrize(("text", "comparator"), [
    ("已装内存大于 16 GB", "gt"),
    ("已装内存小于 16 GB", "lt"),
])
def test_memory_strict_comparator_is_preserved(text, comparator):
    validation = import_module("backend.app.extraction.validation")
    repo, _ = inputs()
    documents = []
    for document in repo.documents:
        if document.role == "requirement":
            blocks = tuple(replace(block, text=text) if "已装内存" in block.text
                           else block for block in document.parsed.blocks)
            documents.append(replace(document, parsed=replace(document.parsed, blocks=blocks)))
        else:
            documents.append(document)
    changed_repo = EvidenceRepository(repo.case_id, repo.revision + 1, documents)
    source = next(block for block in changed_repo.blocks if block.role == "requirement"
                  and text in block.text)
    fact = validation.validate_memory(
        {"raw_value": "16", "raw_unit": "GB", "qualifier": "installed",
         "comparator": comparator, "evidence_refs": [source.evidence_id]},
        changed_repo, "vendor-a",
    )
    assert fact.comparator == comparator


def test_missing_and_ambiguous_are_distinct():
    repo, facts = inputs()
    rules = import_module("backend.app.rules.memory")
    requirement = [f for f in facts if f.role == "requirement"]
    assert rules.evaluate_memory(repo, "vendor-a", requirement, scan_complete=True).business_status == "证据不足"
    uncertain = [replace(f, qualifier="unknown") if f.role != "requirement" else f for f in facts]
    assert rules.evaluate_memory(repo, "vendor-a", uncertain, scan_complete=True).business_status == "待人工确认"


def test_fabricated_numeric_fact_is_rejected():
    repo, _ = inputs()
    block = next(b for b in repo.blocks if b.cell == "B6")
    with pytest.raises(ValueError, match="fact_value"):
        import_module("backend.app.extraction.validation").validate_memory(
            {"raw_value": "64", "raw_unit": "GB", "qualifier": "installed", "comparator": "eq",
             "evidence_refs": [block.evidence_id]}, repo, "vendor-a")


@pytest.mark.parametrize("text", ["内存见附件；已装存储 512 GB", "内存说明：已装存储 512 GB"])
def test_storage_value_near_memory_label_cannot_be_memory(text):
    from backend.app.evidence.models import Document
    from backend.app.parsing import EvidenceBlock, ParseResult
    validation = import_module("backend.app.extraction.validation")
    parsed = ParseResult("spec", "test", "complete", (EvidenceBlock("e", "spec", text),))
    repo = EvidenceRepository("test", 1, [Document("spec", "hash", "spec", "a", "A", parsed)])
    with pytest.raises(ValueError, match="fact_field"):
        validation.validate_memory({"raw_value": "512", "raw_unit": "GB", "qualifier": "installed",
                                    "comparator": "eq", "evidence_refs": [repo.blocks[0].evidence_id]}, repo, "a")


def test_strict_comparison_is_not_silently_treated_as_equality():
    from backend.app.evidence.models import Document
    from backend.app.parsing import EvidenceBlock, ParseResult
    validation = import_module("backend.app.extraction.validation")
    parsed = ParseResult("req", "test", "complete", (EvidenceBlock("e", "req", "已装内存大于 16 GB"),))
    offered = ParseResult("spec", "test", "complete", ())
    repo = EvidenceRepository("test", 1, [Document("req", "h1", "requirement", None, None, parsed),
                                         Document("spec", "h2", "spec", "a", "A", offered)])
    with pytest.raises(ValueError, match="fact_comparator"):
        validation.validate_memory({"raw_value": "16", "raw_unit": "GB", "qualifier": "installed",
                                    "comparator": "eq", "evidence_refs": [repo.blocks[0].evidence_id]}, repo, "a")


def _scripted_extractor(repo, *, omit_role=None):
    import json

    from langchain_core.messages import AIMessage

    by_id = {block.evidence_id: block for block in repo.blocks}

    def model_call(operation, messages, schema):
        assert operation == "extract"
        facts = []
        for item in json.loads(messages[-1].content)["evidence"]:
            block = by_id[item["evidence_id"]]
            if "已装内存" not in block.text or "GB" not in block.text:
                continue
            if block.role == omit_role:
                continue
            facts.append({
                "raw_value": "32" if "32" in block.text else "16",
                "raw_unit": "GB",
                "qualifier": "installed",
                "comparator": "gte" if block.role == "requirement" else "eq",
                "evidence_refs": [block.evidence_id],
            })
        return AIMessage(
            content="",
            tool_calls=[{"name": "MemoryCandidates", "args": {"facts": facts}, "id": "batch"}],
        )

    return model_call


def test_complete_model_extraction_yields_conflict_from_all_three_sources():
    from backend.app.extraction.memory import extract_memory
    from backend.app.rules.memory import evaluate_memory

    repo = load_case(PACK)
    fact_set = extract_memory(repo, "vendor-a", _scripted_extractor(repo))
    assert fact_set.complete
    assert len(fact_set.facts) == 3
    check = evaluate_memory(repo, "vendor-a", list(fact_set.facts), scan_complete=fact_set.complete)
    assert check.business_status == "资料冲突"
    assert len(check.evidence_refs) == 3


def test_omitted_memory_source_keeps_processing_incomplete():
    from backend.app.extraction.memory import extract_memory
    from backend.app.rules.memory import evaluate_memory

    repo = load_case(PACK)
    fact_set = extract_memory(repo, "vendor-a", _scripted_extractor(repo, omit_role="spec"))
    assert set(fact_set.scanned_ids) == {block.evidence_id for block in repo.blocks}
    assert not fact_set.complete
    check = evaluate_memory(repo, "vendor-a", list(fact_set.facts), scan_complete=fact_set.complete)
    assert check.processing_status == "partial"
    assert check.business_status is None


def _split_quote_repo(value_text="16 GB"):
    repo = load_case(PACK)
    documents = list(repo.documents)
    for index, document in enumerate(documents):
        if document.role != "quote":
            continue
        blocks = tuple(replace(block, text=value_text) if block.cell == "B6" else block
                       for block in document.parsed.blocks)
        documents[index] = replace(document, parsed=replace(document.parsed, blocks=blocks))
    return EvidenceRepository(repo.case_id, repo.revision + 1, documents)


def _split_quote_extractor(repo, *, include_pair):
    import json

    from langchain_core.messages import AIMessage

    by_id = {block.evidence_id: block for block in repo.blocks}
    label = next(block for block in repo.blocks if block.role == "quote" and block.cell == "A6")
    value = next(block for block in repo.blocks if block.role == "quote" and block.cell == "B6")

    def model_call(operation, messages, schema):
        assert operation == "extract"
        items = json.loads(messages[-1].content)["evidence"]
        facts = []
        for item in items:
            block = by_id[item["evidence_id"]]
            if "已装内存" not in block.text or "GB" not in block.text:
                continue
            facts.append({"raw_value": "32" if "32" in block.text else "16",
                          "raw_unit": "GB", "qualifier": "installed",
                          "comparator": "gte" if block.role == "requirement" else "eq",
                          "evidence_refs": [block.evidence_id]})
        if include_pair and {label.evidence_id, value.evidence_id} <= {
            item["evidence_id"] for item in items
        }:
            assert {item["cell"] for item in items if item["evidence_id"] in {
                label.evidence_id, value.evidence_id
            }} == {"A6", "B6"}
            facts.append({"raw_value": "16", "raw_unit": "GB", "qualifier": "installed",
                          "comparator": "eq", "evidence_refs": [label.evidence_id, value.evidence_id]})
        return AIMessage(content="", tool_calls=[
            {"name": "MemoryCandidates", "args": {"facts": facts}, "id": "batch"}
        ])

    return model_call


def test_split_xlsx_memory_value_requires_both_original_cells():
    from backend.app.extraction.validation import validate_memory

    repo = _split_quote_repo()
    label = next(block for block in repo.blocks if block.role == "quote" and block.cell == "A6")
    value = next(block for block in repo.blocks if block.role == "quote" and block.cell == "B6")
    fact = validate_memory({"raw_value": "16", "raw_unit": "GB", "qualifier": "installed",
                            "comparator": "eq", "evidence_refs": [label.evidence_id, value.evidence_id]},
                           repo, "vendor-a")
    assert fact.evidence_refs == (label.evidence_id, value.evidence_id)
    assert fact.role == "quote" and str(fact.value) == "16"


def test_split_xlsx_memory_quote_conflicts_with_specification():
    from backend.app.extraction.memory import extract_memory
    from backend.app.rules.memory import evaluate_memory

    repo = _split_quote_repo()
    fact_set = extract_memory(repo, "vendor-a", _split_quote_extractor(repo, include_pair=True))
    assert fact_set.complete
    assert len(fact_set.facts) == 3
    check = evaluate_memory(repo, "vendor-a", list(fact_set.facts), scan_complete=fact_set.complete)
    assert check.business_status == "资料冲突"
    assert {block.cell for block in repo.read(list(check.evidence_refs)) if block.role == "quote"} == {
        "A6", "B6"
    }


def test_omitted_split_xlsx_memory_quote_does_not_claim_complete_coverage():
    from backend.app.extraction.memory import extract_memory
    from backend.app.rules.memory import evaluate_memory

    repo = _split_quote_repo()
    fact_set = extract_memory(repo, "vendor-a", _split_quote_extractor(repo, include_pair=False))
    assert not fact_set.complete
    check = evaluate_memory(repo, "vendor-a", list(fact_set.facts), scan_complete=fact_set.complete)
    assert check.processing_status == "partial"
    assert check.business_status is None


def test_xlsx_memory_label_cannot_borrow_storage_capacity():
    from backend.app.extraction.validation import validate_memory

    repo = _split_quote_repo("已装存储 512 GB")
    label = next(block for block in repo.blocks if block.role == "quote" and block.cell == "A6")
    storage = next(block for block in repo.blocks if block.role == "quote" and block.cell == "B6")
    with pytest.raises(ValueError, match="fact_field"):
        validate_memory({"raw_value": "512", "raw_unit": "GB", "qualifier": "installed",
                         "comparator": "eq", "evidence_refs": [label.evidence_id, storage.evidence_id]},
                        repo, "vendor-a")


def _split_quote_with_extra_cells(extra_cells):
    repo = _split_quote_repo()
    documents = list(repo.documents)
    for index, document in enumerate(documents):
        if document.role != "quote":
            continue
        template = next(block for block in document.parsed.blocks if block.cell == "B6")
        extras = tuple(replace(template, evidence_id=f"extra-{cell}", cell=cell, text=text)
                       for cell, text in extra_cells.items())
        documents[index] = replace(document, parsed=replace(
            document.parsed, blocks=(*document.parsed.blocks, *extras)))
    return EvidenceRepository(repo.case_id, repo.revision + 1, documents)


@pytest.mark.parametrize("extra_cells", [
    {"B5": "最大支持"},
    {"C6": "8 GB"},
])
def test_xlsx_memory_pair_rejects_conflicting_table_context(extra_cells):
    from backend.app.extraction.validation import validate_memory

    repo = _split_quote_with_extra_cells(extra_cells)
    label = next(block for block in repo.blocks if block.role == "quote" and block.cell == "A6")
    value = next(block for block in repo.blocks if block.role == "quote" and block.cell == "B6")
    with pytest.raises(ValueError, match="fact_field"):
        validate_memory({"raw_value": "16", "raw_unit": "GB", "qualifier": "installed",
                         "comparator": "eq", "evidence_refs": [label.evidence_id, value.evidence_id]},
                        repo, "vendor-a")


@pytest.mark.parametrize("label_text", ["已装内存卡", "已装虚拟内存"])
def test_xlsx_other_memory_label_is_not_installed_ram(label_text):
    from backend.app.extraction.validation import validate_memory

    repo = _split_quote_repo()
    documents = list(repo.documents)
    for index, document in enumerate(documents):
        if document.role == "quote":
            blocks = tuple(replace(block, text=label_text) if block.cell == "A6" else block
                           for block in document.parsed.blocks)
            documents[index] = replace(document, parsed=replace(document.parsed, blocks=blocks))
    repo = EvidenceRepository(repo.case_id, repo.revision + 1, documents)
    label = next(block for block in repo.blocks if block.role == "quote" and block.cell == "A6")
    value = next(block for block in repo.blocks if block.role == "quote" and block.cell == "B6")
    with pytest.raises(ValueError, match="fact_field"):
        validate_memory({"raw_value": "16", "raw_unit": "GB", "qualifier": "installed",
                         "comparator": "eq", "evidence_refs": [label.evidence_id, value.evidence_id]},
                        repo, "vendor-a")


@pytest.mark.parametrize("cell_text", ["已装内存卡 16 GB", "已装虚拟内存 16 GB"])
def test_xlsx_other_memory_cell_is_not_installed_ram(cell_text):
    from backend.app.extraction.validation import validate_memory

    repo = _split_quote_repo(cell_text)
    card = next(block for block in repo.blocks if block.role == "quote" and block.cell == "B6")
    with pytest.raises(ValueError, match="fact_field"):
        validate_memory({"raw_value": "16", "raw_unit": "GB", "qualifier": "installed",
                         "comparator": "eq", "evidence_refs": [card.evidence_id]},
                        repo, "vendor-a")
