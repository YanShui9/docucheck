"""Repeated table metadata may be removed only from current bound enum choices."""

import pytest
from openpyxl.utils.cell import coordinate_to_tuple

from backend.app.agent.dynamic import review_plan
from backend.app.criteria.facts import (
    BINDING_RULE_VERSION,
    RULE_VERSION,
    evaluate_checklist,
    revalidate_fact,
    validate_fact,
)
from backend.app.criteria.item_bindings import validate_bindings
from backend.app.criteria.models import ChecklistDefinition
from backend.app.evidence.models import Document
from backend.app.evidence.repository import EvidenceRepository, digest
from backend.app.parsing import EvidenceBlock, ParseResult

V8 = "dynamic-rules-v8"
# Captured before the v9 fix: complete canonical fact, checks and manifest.
V8_IDENTITIES = {
    "enum": "525dd635da13effa565358928f07c21d8f26a0cf378da37da4526b0946a0720a",
    "数量": "ffcd369b6cd8d40769ae860c4e5bcc8ed81f2bb196292d959c69552bba232a09",
    "采购数量": "2bbd240f98149c92cfa329ed188235cbc170d9a7a0f199b2eeb4b10a434836bd",
    "含税单价上限": "40ac84e7952c79bf41f842e2cd4cac72f0059a65e0c5dd2a3ecaee6c1635f18b",
    "交期": "268e796c5a80beda8d81baf952117498a395f28b152fd23d5a84ebd1e4901425",
}


def source(name="品种", rule=None, cells=None):
    rule = rule or {"type": "enum", "values": ["红富士"]}
    if rule["type"] == "enum":
        rule = {"operator": "in", **rule}
    definition = ChecklistDefinition.model_validate({
        "items": [{"item_id": "sample", "name": "样品"}],
        "criteria": [{"criterion_id": "metric", "item_id": "sample", "name": name,
                      "rule": rule,
                      "source": {"kind": "user_defined", "reason": "买方确认"}}],
    })
    cells = cells or [("A6", name), ("B6", name + " 红富士")]
    blocks = (EvidenceBlock("anchor", "quote", "样品", sheet="报价", cell="A1"),
              *(EvidenceBlock(str(i), "quote", text, sheet="报价", cell=cell)
                for i, (cell, text) in enumerate(cells)))
    repo = EvidenceRepository("case", 1, [Document(
        "quote", "a" * 64, "quote", "A", None,
        ParseResult("quote", "parser", "complete", blocks, total_sheets=1, processed_sheets=("报价",)))])
    entries = validate_bindings([{
        "document_id": "quote", "source_sha256": "a" * 64, "parser_version": "parser", "item_id": "sample",
        "selector": {"kind": "xlsx", "sheet": "报价", "start_row": 6,
                     "end_row": max(coordinate_to_tuple(cell)[0] for cell, _ in cells),
                     "start_column": 1, "end_column": max(coordinate_to_tuple(cell)[1] for cell, _ in cells)},
        "anchor": {"evidence_refs": [repo.blocks[0].evidence_id], "quote": "样品"},
        "identifier_class": "category",
    }], definition, repo).payload()
    payload = {"schema_version": 1, "case_id": "case", "snapshot_id": repo.snapshot_id,
               "checklist_version_id": "confirmed", "checklist_digest": digest(definition.model_dump(mode="json")),
               "entries": entries}
    return definition, repo, {"version_id": "binding-a", "digest": digest(payload), "payload": payload}


def candidate(repo, value="红富士", unit="", indices=None, **basis):
    blocks = [repo.blocks[i] for i in (indices or range(1, len(repo.blocks)))]
    return {"criterion_id": "metric", "item_id": "sample", "supplier": "A", "value": value, "unit": unit,
            "evidence_refs": [b.evidence_id for b in blocks], "quote": " ".join(b.text for b in blocks), **basis}


def verify(sample, body=None, version=BINDING_RULE_VERSION):
    definition, repo, frozen = sample
    return validate_fact(body or candidate(repo), definition, repo, "A", rule_version=version, item_bindings=frozen)


def checks(sample, facts, version=BINDING_RULE_VERSION):
    definition, repo, frozen = sample
    return evaluate_checklist(definition, facts, {"A": True}, snapshot_id=repo.snapshot_id,
                              rule_version=version, item_coverage={"A": {"sample": True}}, item_bindings=frozen)


def assert_v8_identity(sample, fact, key):
    definition, repo, frozen = sample
    plan, _ = review_plan(repo, definition, rule_version=V8, item_bindings=frozen)
    assert digest({"fact": fact, "checks": checks(sample, [fact], V8),
                   "manifest": [entry for _, _, entry in plan["A"]]}) == V8_IDENTITIES[key]


@pytest.mark.parametrize("name,value", [("品种", "红富士"), ("尺码", "L"), ("面料", "纯棉")])
def test_current_bound_enum_repeated_header_preserves_complete_source(name, value):
    sample = source(name, {"type": "enum", "values": [value]}, [("A6", name), ("B6", name + " " + value)])
    body = candidate(sample[1], value)
    fact = verify(sample, body)
    assert not fact["ambiguous"]
    assert checks(sample, [fact])[0]["business_status"] == "满足"
    assert fact["quote"] == body["quote"] and fact["evidence_refs"] == body["evidence_refs"]


@pytest.mark.parametrize("cells,indices", [
    ([("A6", "品种"), ("B6", "品种 红富士")], [2]),
    ([("A6", "品种"), ("B6", "红富士")], None),
])
def test_labeled_value_only_and_plain_header_with_bare_value_still_work(cells, indices):
    sample = source(cells=cells)
    fact = verify(sample, candidate(sample[1], indices=indices))
    assert not fact["ambiguous"] and checks(sample, [fact])[0]["business_status"] == "满足"


@pytest.mark.parametrize("cells", [
    [("A6", "品种"), ("B6", "品种 红富士 或 嘎啦 可选")],
    [("A6", "可选品种"), ("B6", "品种 红富士")],
    [("A6", "非品种"), ("B6", "品种 红富士")],
    [("A6", "品种 嘎啦"), ("B6", "品种 红富士")],
    [("A6", "品种 红富士"), ("B6", "品种")],
    [("A6", "品种"), ("B6", "品种 红富士"), ("C6", "品种 嘎啦")],
    [("A6", "品种"), ("B6", "品种 红富士；产地待确认")],
    [("A6", "品种"), ("B6", "品种 品种 红富士")],
    [("A6", "品种"), ("B6", "非品种 红富士")],
    [("A6", "品种"), ("B6", "备注 非品种 红富士")],
])
def test_enum_headers_do_not_clean_options_qualifications_or_multiple_value_cells(cells):
    sample = source(cells=cells)
    fact = verify(sample)
    assert fact["ambiguous"] and checks(sample, [fact])[0]["business_status"] == "待人工确认"


def test_header_that_is_an_allowed_value_is_not_discarded():
    sample = source(rule={"type": "enum", "values": ["品种", "红富士"]})
    assert verify(sample)["ambiguous"]


@pytest.mark.parametrize("name", [
    "非品种", "不含品种", "无面料", "没有品种", "禁止面料", "拒绝材质", "否定类别",
    "排除材质", "免用面料", "避免面料", "淘汰材质",
])
def test_negated_metric_names_are_not_treated_as_pure_headers(name):
    assert verify(source(name))["ambiguous"]


@pytest.mark.parametrize("version", ["dynamic-rules-v4", "dynamic-rules-v5", "dynamic-rules-v6", V8])
def test_historical_bound_enums_keep_repeated_header_ambiguity(version):
    assert verify(source(), version=version)["ambiguous"]


@pytest.mark.parametrize("cells,error", [
    ([("A6", "品种"), ("B6", "品种 非红富士")], "fact_value"),
    ([("A6", "品种"), ("B7", "品种 红富士")], "evidence_relationship"),
])
def test_enum_negation_and_cross_row_sources_remain_rejected(cells, error):
    sample = source(cells=cells)
    with pytest.raises(ValueError, match=error):
        verify(sample)


def test_real_distinct_enum_values_remain_conflicting():
    sample = source(cells=[("A6", "品种"), ("B6", "品种 红富士"),
                           ("A7", "品种"), ("B7", "品种 嘎啦")])
    facts = [verify(sample, candidate(sample[1], value, indices=indices))
             for value, indices in [("红富士", [1, 2]), ("嘎啦", [3, 4])]]
    assert checks(sample, facts)[0]["business_status"] == "资料冲突"


def test_v8_enum_fact_checks_and_manifest_remain_frozen():
    sample = source()
    definition, repo, frozen = sample
    old = verify(sample, version=V8)
    assert old["ambiguous"] and checks(sample, [old], V8)[0]["business_status"] == "待人工确认"
    assert revalidate_fact(old, definition, repo, "A", rule_version=V8, item_bindings=frozen) == old
    assert_v8_identity(sample, old, "enum")
    old_plan, _ = review_plan(repo, definition, rule_version=V8, item_bindings=frozen)
    new_plan, _ = review_plan(repo, definition, rule_version=BINDING_RULE_VERSION, item_bindings=frozen)
    assert BINDING_RULE_VERSION == "dynamic-rules-v14" and RULE_VERSION == "dynamic-rules-v7"
    assert old_plan["A"][0][-1]["input_digest"] != new_plan["A"][0][-1]["input_digest"]
    assert old_plan["A"][0][-1]["batch_id"] != new_plan["A"][0][-1]["batch_id"]


@pytest.mark.parametrize("name,rule,text,value,unit,basis,ambiguous,comparator", [
    ("数量", {"type": "numeric", "operator": "eq", "value": "10", "unit": "台"},
     "数量不足10台", "10", "台", {}, True, "unknown"),
    ("采购数量", {"type": "numeric", "operator": "eq", "value": "10", "unit": "台"},
     "数量10台", "10", "台", {}, False, "eq"),
    ("含税单价上限", {"type": "money", "operator": "lte", "value": "10", "currency": "CNY",
                     "per_unit": "台", "tax_basis": "included"},
     "单价10元/台，不含税", "10", "元/台", {"tax_basis": "excluded"}, False, "eq"),
    ("交期", {"type": "duration", "operator": "lte", "value": "14", "calendar": "natural_days",
              "start_event": "订单确认"},
     "交期订单确认后10个自然日内交付", "10", "个自然日", {"start_event": "订单确认后"}, False, "lte"),
])
def test_v8_numeric_alias_tax_and_normalized_duration_keep_original_semantics(
    name, rule, text, value, unit, basis, ambiguous, comparator,
):
    sample = source(name, rule, [("B6", text)])
    definition, repo, frozen = sample
    fact = verify(sample, candidate(repo, value, unit, **basis), version=V8)
    assert fact["ambiguous"] is ambiguous and fact["comparator"] == comparator
    assert revalidate_fact(fact, definition, repo, "A", rule_version=V8, item_bindings=frozen) == fact
    assert_v8_identity(sample, fact, name)
    if rule["type"] == "money":
        assert fact["tax_basis"] == "excluded" and checks(sample, [fact], V8)[0]["business_status"] == "待人工确认"
    if rule["type"] == "duration":
        assert fact["start_event"] == "订单确认" and checks(sample, [fact], V8)[0]["business_status"] == "满足"
