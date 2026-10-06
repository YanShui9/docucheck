"""Uncertain facts and explicit model exclusions remain source-bound."""
import json
from dataclasses import asdict
from decimal import Decimal

import pytest
from langchain_core.messages import AIMessage

from backend.app.agent.fact_memory import decode_fact_sets, encode_fact_sets
from backend.app.evidence.models import Document
from backend.app.evidence.repository import EvidenceRepository, digest
from backend.app.extraction.fields import (
    FieldCandidate,
    _validate_candidate,
    extract_fields,
)
from backend.app.extraction.validation import Candidate, validate_memory
from backend.app.field_contract import FieldKey
from backend.app.parsing import EvidenceBlock, ParseResult
from backend.app.rules.fields import evaluate_field


def repo_for(requirement, source, *, extra=None, model="MODEL-A"):
    documents = []
    for role, texts in (("requirement", [requirement]), ("quote", ["本型号报价附件"]),
                        ("spec", [source, *([extra] if extra else [])])):
        parsed = ParseResult(role, "test", "complete", tuple(
            EvidenceBlock(f"{role}-{i}", role, text, page_idx=0) for i, text in enumerate(texts)
        ), total_pages=1, processed_pages=(0,))
        documents.append(Document(role, f"hash-{role}", role,
                                  None if role == "requirement" else "vendor-a",
                                  None if role == "requirement" else model, parsed))
    return EvidenceRepository("coverage", 1, documents)


def model_for(field, *, omit_uncertain=False, include_foreign=False):
    def call(operation, messages, schema):
        facts = []
        for block in json.loads(messages[-1].content)["evidence"]:
            text = block["text"]
            if field == "memory":
                if "另一型号" in text and not include_foreign:
                    continue
                import re
                match = re.search(r"已装内存(?:至少)?\s*(\d+)\s*GB", text)
                if match:
                    facts.append({"raw_value": match[1], "raw_unit": "GB", "qualifier": "installed",
                                  "comparator": "gte" if "至少" in text else "eq",
                                  "evidence_refs": [block["evidence_id"]]})
            else:
                from backend.app.extraction.fields import (
                    VALUE_PATTERNS,
                    _relevant_clauses,
                )
                if omit_uncertain and block.get("role") != "requirement":
                    continue
                for clause in _relevant_clauses(FieldKey(field), text):
                    for match in VALUE_PATTERNS[FieldKey(field)].finditer(clause):
                        facts.append({"field_key": field, "raw_value": match["value"],
                                      "raw_unit": match["unit"], "evidence_refs": [block["evidence_id"]]})
        return AIMessage(content="", tool_calls=[{"id": "extract", "name": schema["function"]["name"],
                                                  "args": {"facts": facts}}])
    return call


@pytest.mark.parametrize("source", [
    "可选 SSD 1 TB，未声明实装容量",
    "标称 SSD 可选 1 TB",
    "标称存储 1 TB，未声明实装容量",
    "选配标称硬盘 1 TB",
])
@pytest.mark.parametrize("omit", [False, True])
def test_optional_storage_is_accounted_but_never_installed(source, omit):
    repo = repo_for("标称存储至少 512 GB", source)
    sets = extract_fields(repo, "vendor-a", ["storage"], model_for("storage", omit_uncertain=omit))
    result = sets[FieldKey.STORAGE]
    assert result.complete and len(result.facts) == 2
    offered = next(f for f in result.facts if f.role == "spec")
    assert offered.qualifier == "unknown" and offered.value == Decimal(1000)
    assert evaluate_field(repo, "vendor-a", "storage", result.facts,
                          scan_complete=True).business_status == "待人工确认"
    encoded = encode_fact_sets(repo, {result.fact_set_id: result})
    assert decode_fact_sets(repo, encoded)[result.fact_set_id] == result


@pytest.mark.parametrize("currency,normalized", [("元", "CNY"), ("人民币", "CNY"), ("USD", "USD")])
@pytest.mark.parametrize("omit", [False, True])
def test_bundle_total_is_retained_without_inventing_a_unit_price(currency, normalized, omit):
    repo = repo_for("含税单价不超过 8000 元/台",
                    f"含税套装总价 60000 {currency}，含附件，单价未声明")
    result = extract_fields(repo, "vendor-a", ["unit_price"],
                            model_for("unit_price", omit_uncertain=omit))[FieldKey.UNIT_PRICE]
    assert result.complete and len(result.facts) == 2
    offered = next(f for f in result.facts if f.role == "spec")
    assert offered.raw_value == "60000" and offered.raw_unit == currency
    assert offered.unit == normalized and dict(offered.attributes)["pricing_unit"] == "unknown"
    check = evaluate_field(repo, "vendor-a", "unit_price", result.facts, scan_complete=True)
    assert check.business_status == "待人工确认"
    assert offered.evidence_refs[0] in check.evidence_refs
    assert decode_fact_sets(repo, encode_fact_sets(repo, {result.fact_set_id: result}))[result.fact_set_id] == result
    from backend.app.rules.fields import (
        _evaluate_field_v1,
        _evaluate_field_v2,
        _evaluate_field_v3,
    )
    for old in (_evaluate_field_v1, _evaluate_field_v2, _evaluate_field_v3):
        with pytest.raises(ValueError, match="fact_unit"):
            old(repo, "vendor-a", "unit_price", result.facts, scan_complete=True)


def test_bundle_total_cannot_be_divided_or_given_a_fabricated_per_device_unit():
    repo = repo_for("含税单价不超过 8000 元/台", "含税套装总价 60000 元，含附件，单价未声明")
    ref = next(b.evidence_id for b in repo.blocks if b.role == "spec")
    for value, unit in (("6000", "元/台"), ("60000", "元/台")):
        with pytest.raises(ValueError, match="candidate_value"):
            _validate_candidate(FieldCandidate(field_key="unit_price", raw_value=value,
                                               raw_unit=unit, evidence_refs=[ref]), repo, "vendor-a")


def test_bare_currency_candidate_cannot_shorten_a_real_per_device_unit():
    repo = repo_for("含税单价不超过 8000 元/台", "含税单价 6000 元/台")
    ref = next(b.evidence_id for b in repo.blocks if b.role == "spec")
    with pytest.raises(ValueError, match="candidate_value"):
        _validate_candidate(FieldCandidate(field_key="unit_price", raw_value="6000",
                                           raw_unit="元", evidence_refs=[ref]), repo, "vendor-a")


@pytest.mark.parametrize("include_foreign", [False, True])
def test_explicit_other_model_source_is_scanned_and_excluded(include_foreign):
    note = "宣传参考:另一型号 ALT-99 已装内存 64 GB，不属于本次报价。"
    repo = repo_for("已装内存至少 16 GB", "已装内存 8 GB", extra=note)
    result = extract_fields(repo, "vendor-a", ["memory"],
                            model_for("memory", include_foreign=include_foreign))[FieldKey.MEMORY]
    assert result.complete and len(result.facts) == 2
    assert set(result.scanned_ids) == {b.evidence_id for b in repo.blocks}
    assert evaluate_field(repo, "vendor-a", "memory", result.facts,
                          scan_complete=True).business_status == "不满足"
    assert decode_fact_sets(repo, encode_fact_sets(repo, {result.fact_set_id: result}))[result.fact_set_id] == result
    ref = next(b.evidence_id for b in repo.blocks if "ALT-99" in b.text)
    with pytest.raises(ValueError, match="fact_not_applicable"):
        validate_memory(Candidate(raw_value="64", raw_unit="GB", qualifier="installed",
                                  comparator="eq", evidence_refs=[ref]), repo, "vendor-a")


@pytest.mark.parametrize("note,model", [
    ("另一型号 ALT-99 已装内存 64 GB", "MODEL-A"),
    ("型号 ALT-99 已装内存 64 GB，不属于本次报价", "MODEL-A"),
    ("另一型号 MODEL-A 已装内存 64 GB，不属于本次报价", "MODEL-A"),
    ("另一型号 ALT-99 已装内存 64 GB，不属于本次报价", None),
    ("另一型号 ALT-99 已装内存 64 GB，不属于本次报价；本次型号 MODEL-A 已装内存 8 GB", "MODEL-A"),
    ("另一型号 ALT-99 已装内存 64 GB，不属于本次报价。已装内存 32 GB", "MODEL-A"),
])
def test_ambiguous_scope_is_not_excluded(note, model):
    repo = repo_for("已装内存至少 16 GB", "已装内存 8 GB", extra=note, model=model)
    def omit_notes(operation, messages, schema):
        payload = json.loads(messages[-1].content)
        payload["evidence"] = [b for b in payload["evidence"] if b["text"] in {"已装内存至少 16 GB", "已装内存 8 GB"}]
        messages[-1].content = json.dumps(payload)
        return model_for("memory")(operation, messages, schema)
    result = extract_fields(repo, "vendor-a", ["memory"], omit_notes)[FieldKey.MEMORY]
    assert not result.complete


def test_restored_complete_flag_cannot_hide_an_unextracted_capacity():
    repo = repo_for("标称存储至少 512 GB", "标称 SSD 1 TB")
    result = extract_fields(repo, "vendor-a", ["storage"],
                            model_for("storage", omit_uncertain=True))[FieldKey.STORAGE]
    assert not result.complete
    data = encode_fact_sets(repo, {result.fact_set_id: result})
    data[0]["complete"] = True
    data[0]["coverage_issues"] = []
    data[0]["fact_set_id"] = digest([repo.snapshot_id, "vendor-a", "storage", result.scanned_ids,
                                     True, [asdict(f) | {"value": str(f.value)} for f in result.facts]])
    with pytest.raises(ValueError, match="memory_scope"):
        decode_fact_sets(repo, data)


@pytest.mark.parametrize("source,expected", [
    ("已装内存 8 GB", "不满足"),
    ("最大可扩展内存 64 GB，未声明已装容量", "待人工确认"),
])
@pytest.mark.parametrize("qualifier,comparator", [("installed", "lte"), ("unknown", "unknown")])
def test_extraction_derives_memory_semantics_from_source_instead_of_model_hints(source, expected, qualifier, comparator):
    import re

    repo = repo_for("已装内存至少 16 GB", source)

    def hints(operation, messages, schema):
        facts = []
        for b in json.loads(messages[-1].content)["evidence"]:
            match = re.search(r"(\d+)\s*GB", b["text"])
            if "内存" in b["text"] and match:
                facts.append({"raw_value": match[1], "raw_unit": "GB", "qualifier": qualifier,
                              "comparator": comparator, "evidence_refs": [b["evidence_id"]]})
        return AIMessage(content="", tool_calls=[{"id": "hints", "name": "MemoryCandidates", "args": {"facts": facts}}])

    fs = extract_fields(repo, "vendor-a", ["memory"], hints)[FieldKey.MEMORY]
    assert fs.complete
    offered = next(f for f in fs.facts if f.role == "spec")
    assert offered.qualifier == ("maximum" if "最大" in source else "installed")
    assert offered.comparator == "eq"
    assert evaluate_field(repo, "vendor-a", "memory", fs.facts, scan_complete=True).business_status == expected
    assert decode_fact_sets(repo, encode_fact_sets(repo, {fs.fact_set_id: fs}))[fs.fact_set_id] == fs


@pytest.mark.parametrize("source", ["约已装内存 32 GB", "可选已装内存 32 GB", "已装内存 16 GB 或 32 GB"])
def test_canonical_extraction_keeps_uncertain_memory_unknown_and_restorable(source):
    repo = repo_for("已装内存至少 16 GB", source)
    fs = extract_fields(repo, "vendor-a", ["memory"], model_for("memory"))[FieldKey.MEMORY]
    # The single-candidate model omits the second capacity in the 'or' case;
    # source coverage must detect it rather than publishing a complete set.
    if "或" in source:
        assert not fs.complete
    else:
        assert fs.complete
    assert all(f.qualifier == "unknown" for f in fs.facts if f.role == "spec")
    assert decode_fact_sets(repo, encode_fact_sets(repo, {fs.fact_set_id: fs}))[fs.fact_set_id] == fs


@pytest.mark.parametrize("change,error", [
    ({"raw_value": "999"}, "fact_value"),
    ({"raw_unit": "GiB"}, "fact_value"),
    ({"evidence_refs": ["foreign"]}, "evidence_scope"),
])
def test_source_derived_semantics_does_not_repair_bad_numbers_units_or_refs(change, error):
    repo = repo_for("已装内存至少 16 GB", "已装内存 8 GB")
    original = model_for("memory")

    def invalid(operation, messages, schema):
        reply = original(operation, messages, schema)
        if reply.tool_calls[0]["args"]["facts"]:
            reply.tool_calls[0]["args"]["facts"][0].update(change)
        return reply

    with pytest.raises(ValueError, match=error):
        extract_fields(repo, "vendor-a", ["memory"], invalid)
