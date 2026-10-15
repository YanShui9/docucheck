"""Material provenance consists of independently verified, bounded source groups."""

from copy import deepcopy

import pytest

from backend.app.criteria import facts
from backend.app.criteria.facts import evaluate_checklist, revalidate_fact
from backend.app.criteria.item_bindings import (
    ReviewBindings,
    review_bindings,
    validate_bindings,
)
from backend.app.criteria.models import ChecklistDefinition
from backend.app.evidence.models import Document
from backend.app.evidence.repository import EvidenceRepository, digest
from backend.app.parsing import EvidenceBlock, ParseResult
from backend.tests.test_bound_enum_headers import candidate, checks, source, verify

V11 = "dynamic-rules-v11"


def material_sample(*, claim="检测报告 已提供检测报告", body="检测报告 检验结果为合格", description="检测报告"):
    sample = source("检测报告", {"type": "presence", "description": description},
                    [("B6", claim), ("B8", body), ("B9", "检测报告 签发日期2026年10月")])
    repo = sample[1]
    proposal = candidate(repo, True, indices=[1])
    proposal["material_sources"] = [{"evidence_refs": [repo.blocks[2].evidence_id], "quote": body}]
    return sample, proposal


def test_material_sources_allow_independent_body_without_forging_a_joint_quote():
    sample, proposal = material_sample()
    definition, repo, frozen = sample
    fact = verify(sample, proposal, V11)
    assert fact["material_sources"] == proposal["material_sources"]
    assert fact["evidence_refs"] == proposal["evidence_refs"]
    assert fact["quote"] == proposal["quote"]
    assert fact["material_binding_entry_digests"] == [fact["item_binding_entry_digest"]]
    assert not fact["ambiguous"] and checks(sample, [fact], V11)[0]["business_status"] == "满足"
    expected = [*proposal["evidence_refs"], *proposal["material_sources"][0]["evidence_refs"]]
    assert checks(sample, [fact], V11)[0]["evidence_refs"] == expected
    assert checks(sample, [fact], V11)[0]["material_source_groups"] == [
        {"fact_id": fact["fact_id"], "evidence_refs": proposal["material_sources"][0]["evidence_refs"]}]
    assert facts.fact_source_refs(fact) == expected
    assert facts.fact_source_groups(fact) == [tuple(proposal["evidence_refs"]), tuple(expected[1:])]
    assert facts.fact_candidate_data(fact)["material_sources"] == proposal["material_sources"]
    assert revalidate_fact(fact, definition, repo, "A", rule_version=V11, item_bindings=frozen) == fact


def test_v11_historical_behavior_does_not_follow_future_default_version(monkeypatch):
    sample, proposal = material_sample()
    monkeypatch.setattr(facts, "BINDING_RULE_VERSION", "dynamic-rules-v12")
    assert not verify(sample, proposal, V11)["ambiguous"]
    sample, proposal = material_sample(claim="检测报告 验收后再提交检测报告")
    assert verify(sample, proposal, V11)["ambiguous"]
    sample = source()
    assert not verify(sample, candidate(sample[1]), V11)["ambiguous"]
    sample = source("采购数量", {"type": "numeric", "operator": "eq", "value": "10", "unit": "台"},
                    [("B6", "数量10台")])
    assert not verify(sample, candidate(sample[1], "10", "台"), V11)["ambiguous"]


@pytest.mark.parametrize("version", ["dynamic-rules-v7", "dynamic-rules-v8", "dynamic-rules-v9", "dynamic-rules-v10"])
def test_old_versions_reject_material_sources(version):
    sample, proposal = material_sample()
    with pytest.raises(ValueError):
        verify(sample, proposal, version)


@pytest.mark.parametrize("groups", [[], [{"evidence_refs": ["x"], "quote": "报告"}] * 5])
def test_material_sources_have_explicit_nonempty_bounded_schema(groups):
    sample, proposal = material_sample()
    with pytest.raises(ValueError):
        verify(sample, proposal | {"material_sources": groups}, V11)


@pytest.mark.parametrize("context", [
    {"evidence_refs": ["a", "b", "c", "d", "e"], "quote": "检测报告"},
    {"evidence_refs": ["a", "a"], "quote": "检测报告"},
    {"evidence_refs": ["a"], "quote": "检测报告" * 501},
    {"evidence_refs": ["a"], "quote": " "},
])
def test_each_material_context_keeps_existing_evidence_context_limits(context):
    _, proposal = material_sample()
    with pytest.raises(ValueError):
        facts.MaterialFactCandidate.model_validate(proposal | {"material_sources": [context]})


def test_material_sources_normalize_each_horizontal_span_without_merging_groups():
    sample = source("检测报告", {"type": "presence", "description": "检测报告"},
                    [("B6", "检测报告 已提供检测报告"), ("B8", "检测报告"), ("C8", "检验合格")])
    repo = sample[1]
    proposal = candidate(repo, True, indices=[1]) | {"material_sources": [
        {"evidence_refs": [repo.blocks[3].evidence_id, repo.blocks[2].evidence_id],
         "quote": "检测报告   检验合格"}]}
    fact = verify(sample, proposal, V11)
    assert fact["material_sources"] == [{"evidence_refs": [repo.blocks[2].evidence_id, repo.blocks[3].evidence_id],
                                         "quote": "检测报告 检验合格"}]
    assert facts.fact_source_groups(fact)[1] == tuple(sorted(proposal["material_sources"][0]["evidence_refs"]))
    assert facts.fact_source_refs(facts.MaterialFactCandidate.model_validate(proposal)) == [
        *proposal["evidence_refs"], *proposal["material_sources"][0]["evidence_refs"]]


def test_four_distinct_material_sources_are_accepted_without_concatenating_quotes():
    sample = source("检测报告", {"type": "presence", "description": "检测报告"},
                    [("B6", "检测报告 已提供检测报告"),
                     *((f"B{row}", "检测报告 检验合格") for row in range(8, 12))])
    repo = sample[1]
    proposal = candidate(repo, True, indices=[1]) | {"material_sources": [
        {"evidence_refs": [b.evidence_id], "quote": b.text} for b in repo.blocks[2:]]}
    fact = verify(sample, proposal, V11)
    assert len(fact["material_sources"]) == 4
    assert len(checks(sample, [fact], V11)[0]["material_source_groups"]) == 4


@pytest.mark.parametrize("change", ["same_main", "repeated_group", "cross_row", "forged_quote", "missing_name", "extra_metadata"])
def test_material_source_cannot_bypass_source_group_contract(change):
    sample, proposal = material_sample()
    repo = sample[1]
    if change == "same_main":
        proposal["material_sources"] = [{key: proposal[key] for key in ("evidence_refs", "quote")}]
    elif change == "repeated_group":
        proposal["material_sources"] *= 2
    elif change == "cross_row":
        proposal["material_sources"][0]["evidence_refs"].append(repo.blocks[3].evidence_id)
    elif change == "forged_quote":
        proposal["material_sources"][0]["quote"] = "检测报告 伪造正文"
    elif change == "missing_name":
        sample, proposal = material_sample(body="检验结果为合格")
    elif change == "extra_metadata":
        proposal["material_binding_entry_digests"] = ["injected"]
    with pytest.raises(ValueError):
        verify(sample, proposal, V11)


@pytest.mark.parametrize("body", ["检测报告 未提供检测报告", "检测报告 检验结果合格；未附检测报告",
    "检测报告 检验结果合格；仅供参考", "检测报告 检验结果合格；验收后再提交检测报告",
    "检测报告 检验结果合格；另一型号", "检测报告 检验结果合格；部分内容"])
def test_entire_material_span_preserves_negative_uncertain_and_future_protection(body):
    sample, proposal = material_sample(body=body)
    proposal["material_sources"][0]["quote"] = "检测报告"
    fact = verify(sample, proposal, V11)
    assert fact["ambiguous"]
    assert checks(sample, [fact], V11)[0]["business_status"] == "待人工确认"


def test_material_body_cannot_replace_main_full_description_or_clear_main_ambiguity():
    sample, proposal = material_sample(description="检测报告（含签章）")
    with pytest.raises(ValueError, match="fact_value"):
        verify(sample, proposal, V11)
    sample, proposal = material_sample(claim="检测报告 验收后再提交检测报告")
    assert verify(sample, proposal, V11)["ambiguous"]


def test_material_sources_do_not_create_formal_result_for_incomplete_coverage():
    sample, proposal = material_sample()
    definition, repo, frozen = sample
    fact = verify(sample, proposal, V11)
    result = evaluate_checklist(definition, [fact], {"A": False}, snapshot_id=repo.snapshot_id,
                               rule_version=V11, item_coverage={"A": {"sample": False}}, item_bindings=frozen)[0]
    assert result["processing_status"] == "partial" and result["business_status"] is None


@pytest.mark.parametrize("field", ["quote", "evidence_refs", "material_binding_entry_digests"])
def test_material_fact_revalidation_rejects_nested_tampering(field):
    sample, proposal = material_sample()
    definition, repo, frozen = sample
    fact = deepcopy(verify(sample, proposal, V11))
    if field == "material_binding_entry_digests":
        fact[field] = ["forged"]
    else:
        fact["material_sources"][0][field] = "fake" if field == "quote" else [repo.blocks[3].evidence_id]
    with pytest.raises(ValueError):
        revalidate_fact(fact, definition, repo, "A", rule_version=V11, item_bindings=frozen)


@pytest.mark.parametrize("change", ["false", "numeric"])
def test_material_sources_only_belong_to_positive_presence_facts(change):
    if change == "false":
        sample, proposal = material_sample(claim="检测报告 未提供检测报告")
        proposal["value"] = False
    else:
        sample = source("数量", {"type": "numeric", "operator": "eq", "value": "10", "unit": "台"},
                        [("B6", "数量10台"), ("B8", "数量10台")])
        repo = sample[1]
        proposal = candidate(repo, "10", "台", indices=[1]) | {"material_sources": [
            {"evidence_refs": [repo.blocks[2].evidence_id], "quote": repo.blocks[2].text}]}
    with pytest.raises(ValueError, match="material_sources_rule"):
        verify(sample, proposal, V11)


def test_subclass_and_base_contract_preserve_distinct_json_shapes():
    sample, proposal = material_sample()
    obj = facts.MaterialFactCandidate.model_validate(proposal)
    assert verify(sample, obj, V11)["material_sources"] == proposal["material_sources"]
    with pytest.raises(ValueError):
        verify(sample, obj, "dynamic-rules-v10")
    with pytest.raises(ValueError):
        facts.FactCandidate.model_validate(proposal)
    base = facts.FactCandidate.model_validate({k: v for k, v in proposal.items() if k != "material_sources"})
    assert "material_sources" not in base.model_dump(mode="json")
    assert "material_sources" not in verify(sample, base, V11)


def independent_documents(*, supplier="A", item="sample", role="spec", source_sku=None, primary_sku=None,
                          body_sku=None, classification="category", specification="", primary_model=None,
                          body_model=None, include_item=False):
    definition = ChecklistDefinition.model_validate({
        "items": [{"item_id": "sample", "name": "样品", "specification": specification},
                  {"item_id": "other", "name": "其他商品"}],
        "criteria": [{"criterion_id": "metric", "item_id": "sample", "name": "检测报告",
            "rule": {"type": "presence", "description": "检测报告"},
            "source": {"kind": "user_defined", "reason": "买方确认"}}]})
    documents = []
    for doc_id, owner, doc_role, sku, text in [
        ("main", "A", "quote", primary_sku, "检测报告 已提供检测报告"),
        ("body", supplier, role, source_sku, "检测报告 检验合格" + (f"；型号{body_sku}" if body_sku else ""))]:
        anchor = "样品" if doc_id == "main" or item == "sample" else "其他商品"
        if sku:
            anchor += f" 型号{sku}"
        if include_item:
            text = "样品 " + text
        blocks = (EvidenceBlock("anchor", doc_id, anchor, page_idx=0), EvidenceBlock("text", doc_id, text, page_idx=1))
        documents.append(Document(doc_id, ("a" if doc_id == "main" else "b") * 64, doc_role,
                                  owner, primary_model if doc_id == "main" else body_model,
                                  ParseResult(doc_id, "parser", "complete", blocks,
                                                           total_pages=2, processed_pages=(0, 1))))
    repo = EvidenceRepository("case", 1, documents)
    entries = []
    for doc_id, item_id, sku in [("main", "sample", primary_sku), ("body", item, source_sku)]:
        doc = next(d for d in repo.documents if d.document_id == doc_id)
        anchor = next(b for b in repo.blocks if b.document_id == doc_id and b.page_idx == 0)
        context = {"evidence_refs": [anchor.evidence_id], "quote": anchor.text}
        entries.append({"document_id": doc_id, "source_sha256": doc.source_hash, "parser_version": "parser",
            "item_id": item_id, "selector": {"kind": "pdf", "start_page": 1, "end_page": 2},
            "anchor": context, "identifier_class": "sku" if sku else classification,
            "supplier_sku": sku, "sku_anchor": context if sku else None})
    payload = {"schema_version": 1, "case_id": "case", "snapshot_id": repo.snapshot_id,
        "checklist_version_id": "confirmed", "checklist_digest": digest(definition.model_dump(mode="json")),
        "entries": validate_bindings(entries, definition, repo).payload()}
    frozen = {"version_id": "binding-a", "digest": digest(payload), "payload": payload}
    main = next(b for b in repo.blocks if b.document_id == "main" and b.page_idx == 1)
    body = next(b for b in repo.blocks if b.document_id == "body" and b.page_idx == 1)
    proposal = {"criterion_id": "metric", "item_id": "sample", "supplier": "A", "value": True, "unit": "",
        "evidence_refs": [main.evidence_id], "quote": main.text,
        "material_sources": [{"evidence_refs": [body.evidence_id], "quote": body.text}]}
    return (definition, repo, frozen), proposal


def test_material_source_may_come_from_an_independently_bound_document():
    sample, proposal = independent_documents()
    fact = verify(sample, proposal, V11)
    assert not fact["ambiguous"]
    assert fact["material_binding_entry_digests"] != [fact["item_binding_entry_digest"]]


@pytest.mark.parametrize("options", [{"supplier": "B"}, {"item": "other"},
    {"primary_sku": "SKU-A", "source_sku": "SKU-B"},
    {"primary_sku": "SKU-A", "source_sku": "SKU-A", "body_sku": "SKU-B"}])
def test_material_source_rejects_other_supplier_item_and_sku(options):
    with pytest.raises(ValueError, match="supplier_scope|item_binding_scope|material_source_item|item_binding_conflict"):
        sample, proposal = independent_documents(**options)
        verify(sample, proposal, V11)


def test_unknown_body_identity_does_not_become_proof_from_a_range_alone():
    sample, proposal = independent_documents(classification="unknown")
    fact = verify(sample, proposal, V11)
    assert fact["ambiguous"]


@pytest.mark.parametrize("body_model,specification,ambiguous", [
    ("SKU-B", "SKU-A", True),
    ("SKU-A", "SKU-A", False),
    ("sku-a", "SKU-A", False),
    (None, "SKU-A", False),
    ("SKU-B", "", False),
])
def test_unknown_body_raw_model_retains_main_source_specification_guard(body_model, specification, ambiguous):
    sample, proposal = independent_documents(classification="unknown", specification=specification,
        primary_model="SKU-A", body_model=body_model, include_item=True)
    # Both item names are visible and neither source text explicitly declares an SKU;
    # the frozen document model is still an applicability constraint for unknown bindings.
    fact = verify(sample, proposal, V11)
    assert fact["applicable"]
    assert fact["ambiguous"] is ambiguous
    assert checks(sample, [fact], V11)[0]["business_status"] == ("待人工确认" if ambiguous else "满足")
    primary_only = {key: value for key, value in proposal.items() if key != "material_sources"}
    assert not verify(sample, primary_only, "dynamic-rules-v10")["ambiguous"]


def test_known_body_sku_binding_keeps_existing_explicit_identity_behavior():
    sample, proposal = independent_documents(specification="A-100", primary_sku="A-100", source_sku="A-100",
        primary_model="A-100", body_model="A-100", include_item=True)
    fact = verify(sample, proposal, V11)
    assert fact["applicable"] and not fact["ambiguous"]
    assert checks(sample, [fact], V11)[0]["business_status"] == "满足"


@pytest.mark.parametrize("field", ["source_sha256", "parser_version"])
def test_material_source_checks_original_hash_and_parser_against_server_binding(field):
    sample, proposal = independent_documents()
    definition, repo, frozen = sample
    # ReviewBindings is the already validated server object; still compare each source block.
    validated = review_bindings(frozen, definition, repo).bindings
    from dataclasses import replace
    altered = replace(validated.entries[1], **{field: "c" * 64 if field == "source_sha256" else "different"})
    bindings = replace(validated, entries=(validated.entries[0], altered))
    context = ReviewBindings("binding-a", frozen["digest"], repo.case_id, repo.snapshot_id,
                             digest(definition.model_dump(mode="json")), bindings)
    with pytest.raises(ValueError, match="item_binding_source_changed"):
        facts.validate_fact(proposal, definition, repo, "A", rule_version=V11, item_bindings=context)


V10_IDENTITIES = {
    "enum": "82f1d2c40afc2bd5412c246b891b40aa3d1391b3504a6e3651fdcc7757101529",
    "numeric": "53e90cbb1490b082fe22ec112bc36832525fd58fd7d904de11ae630d022abadb",
    "alias": "328b81cd03a6aa76e6f103306fc8ae224fbb5a2e4d3e1e70444c9d95233b8c04",
    "money": "407dc5ab341c711f401a38aebe3e2c7cabda6e882c860225492c777ffd779e80",
    "duration": "c1a57d04f4786bff80df0d43a5f907b93e80210d543cd24669334ce9c049d166",
    "promise": "3fb808d633afdc39c76a315f6af06ba3f04ff2c800211a5341febfd497fd33c8",
}


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
def test_v10_complete_fact_check_and_batch_manifest_identity_remains_frozen(key, name, rule, cells, value, unit, basis):
    from backend.app.agent.dynamic import review_plan

    definition, repo, frozen = sample = source(name, rule, cells)
    fact = verify(sample, candidate(repo, value, unit, **basis), "dynamic-rules-v10")
    plan, _ = review_plan(repo, definition, rule_version="dynamic-rules-v10", item_bindings=frozen)
    assert digest({"fact": fact, "checks": checks(sample, [fact], "dynamic-rules-v10"),
                   "manifest": [entry for _, _, entry in plan["A"]]}) == V10_IDENTITIES[key]
