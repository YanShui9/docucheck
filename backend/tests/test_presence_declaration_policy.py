"""Positive material declarations remain reviewable until body validation exists."""

from copy import deepcopy

import pytest

from backend.app.agent.dynamic import review_plan
from backend.app.criteria import facts
from backend.app.criteria.facts import evaluate_checklist, revalidate_fact
from backend.app.evidence.repository import digest
from backend.tests.test_bound_enum_headers import candidate, checks, source, verify
from backend.tests.test_material_sources import independent_documents, material_sample

V11 = "dynamic-rules-v11"
V11_IDENTITIES = {
    "declaration": "26361297e83e9ed8c644aac7fe6fa6ecae70d01455b872b7d3f6049a9c2fd0ea",
    "echo": "c1c888dfed29f4171ee7d57317b0fe0404c1ed9ef1579ded447b7926d2b93593",
    "body": "f4e9c9e159d37dcebecd6fdb5f4086892dba726ac7e5aac0836e5293756fc4c7",
    "promise": "53a8c6824aa326ab7ec67493cfcaa02c1e82fec787659dd2320322a5e351126c",
    "absence": "862686e5ede5538d9620a385a8ade500824731a6da19b903cf9ff6bd459d5a6f",
    "enum": "3a44477054bbc5ed9fc09b2adbc5ff6008927b7e6f86d8e19b607e0706c60322",
    "numeric": "8a35c3d9f8f86ffbf8ddc716b0c06631ebb1856f3165b4a2a85f0db78ef32ac3",
}


def declaration_sample(kind):
    claim = {"promise": "检测报告 验收后再提交检测报告",
             "absence": "检测报告 未提供检测报告"}.get(kind, "检测报告 已提供检测报告")
    body = {"echo": claim, "body": "检测报告 检验结果为合格"}.get(kind, "检测报告 其他原文")
    sample, proposal = material_sample(claim=claim, body=body)
    if kind not in {"echo", "body"}:
        proposal.pop("material_sources")
    if kind == "absence":
        proposal["value"] = False
    return sample, proposal


def result(sample, proposal):
    fact = verify(sample, proposal, facts.BINDING_RULE_VERSION)
    return fact, checks(sample, [fact], facts.BINDING_RULE_VERSION)[0]


def test_new_bound_reviews_use_v12():
    assert facts.BINDING_RULE_VERSION == "dynamic-rules-v12"


@pytest.mark.parametrize("kind", ["declaration", "echo", "body"])
def test_positive_presence_requires_review_without_independently_validated_body(kind):
    sample, proposal = declaration_sample(kind)
    fact, check = result(sample, proposal)
    assert (check["business_status"], check["reason_code"]) == ("待人工确认", "material_body_unverified")
    # A decision guard must not fabricate uncertainty, absence, or new fact identities.
    assert fact == verify(sample, proposal, V11)
    assert fact["value"] is True and not fact["ambiguous"]
    assert fact["quote"] == proposal["quote"] and fact["evidence_refs"] == proposal["evidence_refs"]
    assert check["is_formal"] and check["processing_status"] == "complete"
    assert check["evidence_refs"] == facts.fact_source_refs(fact)
    assert check["fact_ids"] == [fact["fact_id"]]
    if kind != "declaration":
        assert fact["material_sources"] == proposal["material_sources"]
        assert check["material_source_groups"] == [
            {"fact_id": fact["fact_id"], "evidence_refs": proposal["material_sources"][0]["evidence_refs"]}]
    definition, repo, frozen = sample
    assert revalidate_fact(fact, definition, repo, "A", rule_version=facts.BINDING_RULE_VERSION,
                           item_bindings=frozen) == fact


@pytest.mark.parametrize("required,value,status,reason", [
    (True, False, "不满足", "threshold_failed"),
    (False, False, "满足", "threshold_met"),
    (False, True, "待人工确认", "material_body_unverified"),
])
def test_explicit_absence_keeps_original_required_presence_comparison(required, value, status, reason):
    sample = source("检测报告", {"type": "presence", "description": "检测报告", "must_be_present": required},
                    [("B6", "检测报告 已提供检测报告" if value else "检测报告 未提供检测报告")])
    fact, check = result(sample, candidate(sample[1], value))
    assert fact["value"] is value
    assert (check["business_status"], check["reason_code"]) == (status, reason)


def test_real_positive_negative_conflict_precedes_material_policy():
    sample = source("检测报告", {"type": "presence", "description": "检测报告"},
                    [("B6", "检测报告 已提供检测报告"), ("B7", "检测报告 未提供检测报告")])
    offered = [verify(sample, candidate(sample[1], value, indices=[i]), facts.BINDING_RULE_VERSION)
               for i, value in [(1, True), (2, False)]]
    check = checks(sample, offered, facts.BINDING_RULE_VERSION)[0]
    assert (check["business_status"], check["reason_code"]) == ("资料冲突", "conflicting_supplier_values")


@pytest.mark.parametrize("complete", [True, False])
def test_missing_facts_and_partial_coverage_remain_distinct(complete):
    definition, repo, frozen = sample = declaration_sample("declaration")[0]
    check = evaluate_checklist(definition, [], {"A": complete}, snapshot_id=repo.snapshot_id,
        rule_version=facts.BINDING_RULE_VERSION, item_coverage={"A": {"sample": complete}}, item_bindings=frozen)[0]
    assert (check["business_status"], check["reason_code"]) == (
        ("证据不足", "supplier_fact_missing") if complete else (None, "coverage_incomplete"))
    if not complete:
        _, proposal = declaration_sample("declaration")
        fact = verify(sample, proposal, facts.BINDING_RULE_VERSION)
        partial = evaluate_checklist(definition, [fact], {"A": False}, snapshot_id=repo.snapshot_id,
            rule_version=facts.BINDING_RULE_VERSION, item_coverage={"A": {"sample": False}}, item_bindings=frozen)[0]
        assert partial["processing_status"] == "partial" and partial["business_status"] is None


def test_requirement_manual_precedes_material_policy():
    (definition, repo, frozen), proposal = declaration_sample("declaration")
    definition = definition.model_copy(update={"criteria": [
        definition.criteria[0].model_copy(update={"conditions": ["签章需人工核对"]})]})
    frozen = deepcopy(frozen)
    frozen["payload"]["checklist_digest"] = digest(definition.model_dump(mode="json"))
    frozen["digest"] = digest(frozen["payload"])
    _, check = result((definition, repo, frozen), proposal)
    assert (check["business_status"], check["reason_code"]) == ("待人工确认", "requirement_manual")


@pytest.mark.parametrize("text,reason", [
    ("检测报告 验收后再提交检测报告", "semantics_unclear"),
    ("检测报告 另一型号已提供检测报告", "item_model_unclear"),
])
def test_existing_ambiguity_and_model_guards_precede_material_policy(text, reason):
    sample = source("检测报告", {"type": "presence", "description": "检测报告"}, [("B6", text)])
    fact, check = result(sample, candidate(sample[1], True))
    assert fact["ambiguous"]
    assert (check["business_status"], check["reason_code"]) == ("待人工确认", reason)


@pytest.mark.parametrize("change,match", [
    ("supplier", "supplier_scope"), ("quote", "quote"),
    ("overlap", "material_sources_overlap"), ("negation", "fact_value"),
])
def test_material_policy_does_not_bypass_fact_source_contract(change, match):
    sample, proposal = declaration_sample("body")
    if change == "supplier":
        proposal["supplier"] = "B"
    elif change == "quote":
        proposal["quote"] = "检测报告 伪造原文"
    elif change == "overlap":
        proposal["material_sources"] = [{key: proposal[key] for key in ("evidence_refs", "quote")}]
    else:
        sample, proposal = declaration_sample("absence")
        proposal["value"] = True
    with pytest.raises(ValueError, match=match):
        verify(sample, proposal, facts.BINDING_RULE_VERSION)


@pytest.mark.parametrize("options", [{"supplier": "B"}, {"item": "other"},
    {"primary_sku": "SKU-A", "source_sku": "SKU-B"}])
def test_independent_material_source_keeps_supplier_item_and_sku_scope(options):
    sample, proposal = independent_documents(**options)
    with pytest.raises(ValueError, match="supplier_scope|item_binding_scope|material_source_item|item_binding_conflict"):
        verify(sample, proposal, facts.BINDING_RULE_VERSION)


@pytest.mark.parametrize("version", [f"dynamic-rules-v{i}" for i in (7, 8, 9, 10, 11)])
@pytest.mark.parametrize("kind", ["declaration", "promise", "absence"])
def test_historical_presence_semantics_stay_frozen(version, kind):
    sample, proposal = declaration_sample(kind)
    if version == "dynamic-rules-v7":
        # Unbound v7 needs the item in the original clause, unlike bound versions.
        sample = source("检测报告", {"type": "presence", "description": "检测报告"},
                        [("B6", "样品 " + proposal["quote"])])
        proposal = candidate(sample[1], proposal["value"])
    fact = verify(sample, proposal, version)
    expected = ("不满足" if kind == "absence" else
                "待人工确认" if kind == "promise" and version in {"dynamic-rules-v10", V11} else "满足")
    assert checks(sample, [fact], version)[0]["business_status"] == expected
    definition, repo, frozen = sample
    assert revalidate_fact(fact, definition, repo, "A", rule_version=version, item_bindings=frozen) == fact


@pytest.mark.parametrize("kind", V11_IDENTITIES)
def test_v11_complete_fact_check_and_manifest_identity_remain_frozen(kind, monkeypatch):
    # Captured before v12; the public test does not read local verification artifacts.
    monkeypatch.setattr(facts, "BINDING_RULE_VERSION", "dynamic-rules-v99")
    if kind == "enum":
        sample = source()
        proposal = candidate(sample[1])
    elif kind == "numeric":
        sample = source("数量", {"type": "numeric", "operator": "eq", "value": "10", "unit": "台"},
                        [("B6", "数量10台")])
        proposal = candidate(sample[1], "10", "台")
    else:
        sample, proposal = declaration_sample(kind)
    definition, repo, frozen = sample
    fact = verify(sample, proposal, V11)
    plan, _ = review_plan(repo, definition, rule_version=V11, item_bindings=frozen)
    assert digest({"fact": fact, "checks": checks(sample, [fact], V11),
                   "manifest": [entry for _, _, entry in plan["A"]]}) == V11_IDENTITIES[kind]
