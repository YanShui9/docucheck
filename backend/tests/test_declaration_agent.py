"""Unverified material bodies keep positive declarations open in new reviews."""

import hashlib
from copy import deepcopy

import pytest

from backend.app.agent.dynamic import (
    DynamicAgent,
    _facts_schema,
    render_review,
    review_plan,
)
from backend.tests.test_material_agent import (
    MaterialProvider,
    material_candidate,
    material_sample,
    run,
)

V11 = "dynamic-rules-v11"
V12 = "dynamic-rules-v12"
V11_EXTRACTION_PROMPT_SHA256 = "0f04dc2a8a32fa362460e35b10045b3bad757581014966505864eff29ebd471d"
BODY_NOTICE = "材料正文尚未确认，不能仅凭声明判为已提供。"


def run_version(sample, provider, version, **kwargs):
    definition, repo, frozen = sample
    instance = DynamicAgent(repo, provider, definition, item_bindings=frozen, **kwargs)
    instance.rule_version = version
    return instance.ask("完整审查材料")


def assert_unverified(result):
    assert result["status"] == "completed"
    check = result["payload"]["checks"][0]
    assert check["business_status"] == "待人工确认"
    assert check["reason_code"] == "material_body_unverified"
    assert BODY_NOTICE in result["answer"]


def test_material_body_reason_is_explained_in_review_answer():
    check = {"supplier": "A", "item_id": "sample", "criterion_id": "metric",
             "processing_status": "complete", "business_status": "待人工确认",
             "reason_code": "material_body_unverified", "rule_version": V12}
    assert BODY_NOTICE in render_review([check], complete=True)


@pytest.mark.parametrize("source_kind", ["claim_only", "duplicate_claim", "body"])
def test_current_positive_declaration_stays_true_without_automatic_material_approval(source_kind):
    sample = material_sample(body="检测报告 已附检测报告" if source_kind == "duplicate_claim"
                             else "样品 检测报告编号JC-01，检测结果合格")
    raw = material_candidate(sample[1])
    if source_kind == "claim_only":
        raw.pop("material_sources")
    provider = MaterialProvider([raw])
    result = run(sample, provider)
    assert_unverified(result)
    fact = result["payload"]["facts"][0]
    assert fact["value"] is True
    assert fact["quote"] == raw["quote"] and fact["evidence_refs"] == raw["evidence_refs"]
    assert fact.get("material_sources") == raw.get("material_sources")
    assert len(provider.requests) == 1
    assert provider.schemas == [_facts_schema("A", material_sources=True)]
    prompt = provider.requests[0][0].content
    assert "肯定的true候选仍须如实提取" in prompt
    assert "不得改写为false或删除候选" in prompt
    assert "未独立验证正文语义前不得自动判满足" in prompt


def test_explicit_v11_keeps_exact_extraction_prompt_schema_and_positive_conclusion():
    sample = material_sample()
    provider = MaterialProvider([material_candidate(sample[1])])
    result = run_version(sample, provider, V11)
    assert result["status"] == "completed"
    assert result["payload"]["checks"][0]["business_status"] == "满足"
    assert BODY_NOTICE not in result["answer"]
    assert provider.schemas == [_facts_schema("A", material_sources=True)]
    assert hashlib.sha256(provider.requests[0][0].content.encode()).hexdigest() == V11_EXTRACTION_PROMPT_SHA256


def test_v11_cache_identity_cannot_reuse_success_for_v12_and_current_cache_keeps_sources():
    sample = material_sample()
    definition, repo, frozen = sample
    plans = {version: review_plan(repo, definition, rule_version=version, item_bindings=frozen)[0]["A"]
             for version in (V11, V12)}
    old_batch, old_data, old_entry = plans[V11][0]
    batch, data, entry = plans[V12][0]
    assert old_batch == batch and old_data == data
    assert old_entry["batch_id"] != entry["batch_id"]
    assert old_entry["input_digest"] != entry["input_digest"]
    cache = {}
    save = lambda key, input_digest, raw: cache.update({(key, input_digest): deepcopy(raw)})
    old = run_version(sample, MaterialProvider([material_candidate(repo)]), V11, cache_save=save)
    assert old["payload"]["checks"][0]["business_status"] == "满足"
    provider = MaterialProvider([material_candidate(repo)])
    current = run(sample, provider, cache_load=lambda key, input_digest: cache.get((key, input_digest)),
                  cache_save=save)
    assert_unverified(current)
    assert len(provider.requests) == 1 and len(cache) == 2
    cached_provider = MaterialProvider([], failure=AssertionError("must use current cache"))
    cached = run(sample, cached_provider, cache_load=lambda key, input_digest: cache.get((key, input_digest)))
    assert_unverified(cached)
    assert not cached_provider.requests
    assert cached["payload"]["facts"] == current["payload"]["facts"]
    assert current["payload"]["facts"] == old["payload"]["facts"]
    assert current["payload"]["checks"][0]["check_result_id"] != old["payload"]["checks"][0]["check_result_id"]


@pytest.mark.parametrize("needs_review", [False, True])
def test_v11_report_readback_or_manual_carry_recalculates_positive_fact_in_v12(needs_review):
    sample = material_sample()
    raw = material_candidate(sample[1])
    old = run_version(sample, MaterialProvider([raw]), V11)["payload"]
    assert old["checks"][0]["business_status"] == "满足"
    if needs_review:
        old["needs_review"] = True
    provider = MaterialProvider([] if needs_review else [raw], decision="get_checklist")
    current = run(sample, provider, previous_payload=deepcopy(old))
    assert_unverified(current)
    assert current["payload"]["rule_version"] == V12
    fact = current["payload"]["facts"][0]
    assert fact["value"] is True and fact["material_sources"] == raw["material_sources"]
    assert fact == old["facts"][0]
    assert current["payload"]["checks"][0]["check_result_id"] != old["checks"][0]["check_result_id"]
    assert len(provider.requests) == 1
    if needs_review:
        assert current["payload"]["needs_review"] is True
    assert any(event.get("error") == "report_invalid" for event in current["events"])
