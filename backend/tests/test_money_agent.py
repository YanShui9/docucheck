"""Full formal processing keeps independent tax evidence without model selection."""

import json
from copy import deepcopy

import pytest

from backend.app.agent.dynamic import DynamicAgent, _extraction_messages, review_plan
from backend.app.criteria.facts import BINDING_RULE_VERSION
from backend.tests.test_bound_enum_headers import source
from backend.tests.test_dynamic_agent import call
from backend.tests.test_material_agent import MaterialProvider
from backend.tests.test_money_sources import independent


def money_sample(*, price="25", tax="含税", label="不含税单价"):
    cells = [("A6", label), ("B6", price), ("C6", "元/箱")]
    if tax is not None:
        cells += [("A7", "税费口径"), ("B7", tax)]
    return source("单价上限", {"type": "money", "operator": "lte", "value": "25",
                             "currency": "CNY", "per_unit": "箱", "tax_basis": "excluded"}, cells)


def money_candidate(repo, *, price="25"):
    blocks = repo.blocks[1:4]
    return {"criterion_id": "metric", "item_id": "sample", "supplier": "A",
            "value": price, "unit": "元/箱", "quote": " ".join(b.text for b in blocks),
            "evidence_refs": [b.evidence_id for b in blocks]}


def run_money(sample, provider, **kwargs):
    definition, repo, frozen = sample
    return DynamicAgent(repo, provider, definition, item_bindings=frozen, **kwargs).ask("正式审查报价")


@pytest.mark.parametrize("version", ["dynamic-rules-v13", "dynamic-rules-v14"])
def test_new_extraction_version_inherits_material_schema_and_presence_warning(version):
    text = _extraction_messages({}, bound=True, rule_version=version)[0].content
    assert "material_sources" in text
    assert "未独立验证正文语义前不得自动判满足" in text


def test_formal_money_conflict_is_complete_and_keeps_tax_refs_not_selected_by_model():
    sample = money_sample()
    provider = MaterialProvider([money_candidate(sample[1])])
    result = run_money(sample, provider)
    assert result["status"] == "completed", result
    check = result["payload"]["checks"][0]
    assert check["processing_status"] == "complete"
    assert check["business_status"] == "资料冲突"
    assert check["is_formal"]
    assert len(provider.requests) == 1
    fact = result["payload"]["facts"][0]
    tax_refs = {b.evidence_id for b in sample[1].blocks[4:]}
    assert tax_refs <= set(check["evidence_refs"])
    assert tax_refs == set(fact["tax_sources"][0]["evidence_refs"])
    assert "税口径" in result["answer"] and "冲突" in result["answer"]


@pytest.mark.parametrize(("price", "expected"), [("25", "满足"), ("26", "不满足")])
def test_formal_explicit_excluded_price_uses_deterministic_comparison(price, expected):
    sample = money_sample(price=price, tax=None)
    result = run_money(sample, MaterialProvider([money_candidate(sample[1], price=price)]))
    assert result["status"] == "completed", result
    assert result["payload"]["checks"][0]["business_status"] == expected


def test_money_cache_and_history_rebuild_same_independent_tax_metadata():
    sample = money_sample()
    cache = {}
    first = run_money(sample, MaterialProvider([money_candidate(sample[1])]),
        cache_save=lambda key, fingerprint, raw: cache.update({(key, fingerprint): deepcopy(raw)}))
    assert first["status"] == "completed", first
    assert BINDING_RULE_VERSION == "dynamic-rules-v15"
    assert cache and all("tax_sources" not in candidate
                         for raw in cache.values() for candidate in raw["facts"])
    provider = MaterialProvider([], failure=AssertionError("cached facts should be reused"))
    cached = run_money(sample, provider, cache_load=lambda key, fingerprint: cache.get((key, fingerprint)))
    assert cached["status"] == "completed" and not provider.requests
    assert cached["payload"]["facts"] == first["payload"]["facts"]
    readback = run_money(sample, MaterialProvider([], decision="get_checklist"),
                         previous_payload=first["payload"])
    assert readback["payload"]["facts"] == first["payload"]["facts"]


def test_service_derived_tax_sources_pass_authorized_read_checks(monkeypatch):
    sample = money_sample()
    reads = []
    original = DynamicAgent._read

    def record(agent, refs, budget):
        reads.append(set(refs))
        return original(agent, refs, budget)

    monkeypatch.setattr(DynamicAgent, "_read", record)
    result = run_money(sample, MaterialProvider([money_candidate(sample[1])]))
    assert result["status"] == "completed", result
    tax_refs = {b.evidence_id for b in sample[1].blocks[4:]}
    assert any(tax_refs <= refs for refs in reads)


def test_separate_source_batches_cannot_hide_tax_conflict_and_cache_rebuilds_it():
    sample = source("单价上限", {"type": "money", "operator": "lte", "value": "25",
        "currency": "CNY", "per_unit": "箱", "tax_basis": "excluded"}, [
        ("A6", "不含税单价"), ("B6", "25"), ("C6", "元/箱"),
        *((f"A{row}", "普通附录说明" * 70) for row in range(7, 13)),
        ("A13", "税费口径"), ("B13", "含税")])
    definition, repo, frozen = sample
    candidate = money_candidate(repo)
    plans, _ = review_plan(repo, definition, rule_version=BINDING_RULE_VERSION, item_bindings=frozen)
    batches = plans["A"]
    assert len(batches) > 1
    primary = set(candidate["evidence_refs"])
    tax_refs = {b.evidence_id for b in repo.blocks if b.cell in {"A13", "B13"}}
    assert not any(primary | tax_refs <= {b["evidence_id"] for b in batch} for batch, _, _ in batches)

    class BatchProvider(MaterialProvider):
        def extract(self, messages, schema, **options):
            self.requests.append(deepcopy(messages))
            data = json.loads(messages[-1].content)
            refs = {b["evidence_id"] for b in data["evidence"]}
            return call(schema["function"]["name"], {"facts": [candidate] if primary <= refs else []})

    cache = {}
    result = run_money(sample, BatchProvider([]), cache_save=lambda key, fingerprint, raw:
                       cache.update({(key, fingerprint): deepcopy(raw)}))
    assert result["status"] == "completed", result
    assert result["payload"]["checks"][0]["business_status"] == "资料冲突"
    assert tax_refs <= set(result["payload"]["checks"][0]["evidence_refs"])
    cached = run_money(sample, MaterialProvider([], failure=AssertionError("cache must be reused")),
                       cache_load=lambda key, fingerprint: cache.get((key, fingerprint)))
    assert cached["status"] == "completed", cached
    assert cached["payload"]["facts"] == result["payload"]["facts"]


@pytest.mark.parametrize("note", ["备注：本报价含税", "本次报价包含税费", "价格为税后价"])
def test_unrecognized_tax_qualifications_remain_visible_and_block_automatic_pass(note):
    sample = source("单价上限", {"type": "money", "operator": "lte", "value": "25",
        "currency": "CNY", "per_unit": "箱", "tax_basis": "excluded"}, [
        ("A6", "不含税单价"), ("B6", "25"), ("C6", "元/箱"), ("A7", note)])
    result = run_money(sample, MaterialProvider([money_candidate(sample[1])]))
    assert result["status"] == "completed", result
    assert result["payload"]["checks"][0]["business_status"] == "待人工确认"
    assert result["payload"]["facts"][0]["tax_sources"][0]["quote"] == note


@pytest.mark.parametrize("split_entry", [False, True])
def test_formal_and_cached_review_keep_unselected_price_from_other_entry(split_entry):
    sample, candidate = independent(split_entry=split_entry, second_text="单价30元/箱")
    cache = {}
    result = run_money(sample, MaterialProvider([candidate]), cache_save=lambda key, fingerprint, raw:
                       cache.update({(key, fingerprint): deepcopy(raw)}))
    assert result["status"] == "completed", result
    check = result["payload"]["checks"][0]
    assert (check["business_status"], check["reason_code"]) == ("待人工确认", "money_price_scope_unclear")
    assert "多个单价" in result["answer"]
    other = next(block for block in sample[1].blocks if block.text == "单价30元/箱")
    assert other.evidence_id in check["evidence_refs"]
    cached = run_money(sample, MaterialProvider([], failure=AssertionError("cache must be reused")),
                       cache_load=lambda key, fingerprint: cache.get((key, fingerprint)))
    assert cached["status"] == "completed", cached
    assert cached["payload"]["facts"] == result["payload"]["facts"]
