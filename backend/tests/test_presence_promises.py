"""A promise to supply material is not proof that it has already been supplied."""

import pytest

from backend.app.agent.dynamic import review_plan
from backend.app.criteria.facts import (
    BINDING_RULE_VERSION,
    evaluate_checklist,
    revalidate_fact,
)
from backend.app.evidence.repository import digest
from backend.tests.test_bound_enum_headers import candidate, checks, source, verify

V9 = "dynamic-rules-v9"
V9_IDENTITIES = {
    "enum": "31ae81fcd09936da0968b78353ff22cd4c7767148ae97ddd36b8b48eac465397",
    "numeric": "fe163e2884ac44415132dd42d086629f83ef47527aedea0040d14f0a62dfd842",
    "alias": "36f05d0735d8c10052978c3cb0fc91a7808f35a619b83de5f8b7346901348614",
    "money": "e8795bb9da20e018e60e587fde47a8ebb5a112f8cb0ebb87c3d47af342e535e7",
    "duration": "766175583304469df8405d9f0bed5db247fe4105cf773aab3ce605e4cc390466",
    "promise": "518632914641e7e6f00937370e1b5abe2e4ee2ddf6b7b2d3c888f712b13869fd",
}


def presence(text):
    return source("检测报告", {"type": "presence", "description": "检测报告"}, [("B6", text)])


@pytest.mark.parametrize("text", [
    "检测报告 将提供检测报告", "检测报告 将会提供检测报告", "检测报告 将于验收后提交检测报告",
    "检测报告 验收后提供检测报告", "检测报告 交货时提供检测报告", "检测报告 中标后补交检测报告",
    "检测报告 后续提供检测报告", "检测报告 届时提交检测报告", "检测报告 待提供检测报告",
    "检测报告 承诺提供检测报告", "检测报告 准备提供检测报告", "检测报告 提供时间为验收后",
    "检测报告 会提供检测报告", "检测报告 将在交货时提供检测报告",
    "检测报告 后续补齐检测报告", "检测报告 拟于2027年提供检测报告",
    "检测报告 验收后再提交检测报告", "检测报告 交货后再提供检测报告",
])
def test_current_bound_future_material_promise_requires_review(text):
    sample = presence(text)
    body = candidate(sample[1], True)
    fact = verify(sample, body)
    assert fact["ambiguous"]
    assert checks(sample, [fact])[0]["business_status"] == "待人工确认"
    assert fact["evidence_refs"] == body["evidence_refs"] and fact["quote"] == body["quote"]


def test_partial_coverage_cannot_turn_future_material_promise_into_absence():
    definition, repo, frozen = sample = presence("检测报告 将提供检测报告")
    fact = verify(sample, candidate(repo, True))
    check = evaluate_checklist(definition, [fact], {"A": False}, snapshot_id=repo.snapshot_id,
                               rule_version=BINDING_RULE_VERSION,
                               item_coverage={"A": {"sample": False}}, item_bindings=frozen)[0]
    assert check["processing_status"] == "partial" and check["business_status"] is None


def test_explicit_absence_stays_negative_despite_later_promise():
    sample = presence("检测报告 未提供检测报告，后续将提供")
    fact = verify(sample, candidate(sample[1], False))
    assert fact["value"] is False
    assert checks(sample, [fact])[0]["business_status"] == "不满足"


def test_future_promise_does_not_erase_conflicting_negative_fact():
    sample = source("检测报告", {"type": "presence", "description": "检测报告"},
                    [("B6", "检测报告 将提供检测报告"), ("B7", "检测报告 未提供检测报告")])
    facts = [verify(sample, candidate(sample[1], value, indices=[index]))
             for value, index in [(True, 1), (False, 2)]]
    assert checks(sample, facts)[0]["business_status"] == "资料冲突"


def test_report_title_is_not_itself_a_future_supply_promise():
    sample = source("质保承诺书", {"type": "presence", "description": "质保承诺书"},
                    [("B6", "质保承诺书 已提供质保承诺书")])
    # This patch addresses future promises; actual material provenance remains a separate gate.
    assert not verify(sample, candidate(sample[1], True))["ambiguous"]


@pytest.mark.parametrize("version", ["dynamic-rules-v8", V9])
def test_historical_promise_fact_semantics_are_not_silently_rewritten(version):
    definition, repo, frozen = sample = presence("检测报告 将提供检测报告")
    fact = verify(sample, candidate(repo, True), version=version)
    assert not fact["ambiguous"] and checks(sample, [fact], version)[0]["business_status"] == "满足"
    assert revalidate_fact(fact, definition, repo, "A", rule_version=version, item_bindings=frozen) == fact


@pytest.mark.parametrize("key,name,rule,cells,value,unit,basis", [
    ("enum", "品种", {"type": "enum", "values": ["红富士"]}, None, "红富士", "", {}),
    ("numeric", "数量", {"type": "numeric", "operator": "eq", "value": "10", "unit": "台"},
     [("B6", "数量不足10台")], "10", "台", {}),
    ("alias", "采购数量", {"type": "numeric", "operator": "eq", "value": "10", "unit": "台"},
     [("B6", "数量10台")], "10", "台", {}),
    ("money", "含税单价上限", {"type": "money", "operator": "lte", "value": "10", "currency": "CNY",
                            "per_unit": "台", "tax_basis": "included"},
     [("B6", "单价10元/台，不含税")], "10", "元/台", {"tax_basis": "excluded"}),
    ("duration", "交期", {"type": "duration", "operator": "lte", "value": "14", "calendar": "natural_days",
                       "start_event": "订单确认"},
     [("B6", "交期订单确认后10个自然日内交付")], "10", "个自然日", {"start_event": "订单确认后"}),
    ("promise", "检测报告", {"type": "presence", "description": "检测报告"},
     [("B6", "检测报告 将提供检测报告")], True, "", {}),
])
def test_pre_fix_v9_facts_checks_and_batch_manifest_remain_identical(key, name, rule, cells, value, unit, basis):
    definition, repo, frozen = sample = source(name, rule, cells)
    fact = verify(sample, candidate(repo, value, unit, **basis), version=V9)
    plan, _ = review_plan(repo, definition, rule_version=V9, item_bindings=frozen)
    assert digest({"fact": fact, "checks": checks(sample, [fact], V9),
                   "manifest": [entry for _, _, entry in plan["A"]]}) == V9_IDENTITIES[key]


def test_new_promise_rule_uses_a_distinct_cache_identity():
    definition, repo, frozen = presence("检测报告 将提供检测报告")
    old, _ = review_plan(repo, definition, rule_version=V9, item_bindings=frozen)
    current, _ = review_plan(repo, definition, rule_version=BINDING_RULE_VERSION, item_bindings=frozen)
    assert old["A"][0][-1]["input_digest"] != current["A"][0][-1]["input_digest"]
    assert old["A"][0][-1]["batch_id"] != current["A"][0][-1]["batch_id"]
