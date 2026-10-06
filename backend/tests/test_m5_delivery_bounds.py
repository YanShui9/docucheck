"""Delivery promises retain their upper bounds from source to business rules."""

import json

import pytest
from langchain_core.messages import AIMessage

from backend.app.evidence.models import Document
from backend.app.evidence.repository import EvidenceRepository
from backend.app.extraction.fields import extract_fields
from backend.app.field_contract import FieldKey
from backend.app.parsing import EvidenceBlock, ParseResult
from backend.app.rules.fields import evaluate_field


def delivery_result(required, offered, *, extra=None, unit="自然日", start="订单确认",
                    offered_value="9", extra_value="9", evaluator=evaluate_field):
    """Script only the proposed span; production code validates source semantics."""
    sources = [
        ("requirement", f"交付期限：订单确认后{required}", "9", "自然日"),
        ("quote", f"交付期限：{start}后{offered.replace('自然日', unit)}", offered_value, unit),
    ]
    if extra:
        sources.append(("spec", f"交付期限：订单确认后{extra}", extra_value, "自然日"))
    else:
        sources.append(("spec", "本型号技术规格不含商务承诺。", None, None))
    documents = []
    proposals = {}
    for role, text, value, raw_unit in sources:
        parsed = ParseResult(role, "bounds-test", "complete", (
            EvidenceBlock(f"raw-{role}", role, text),
        ))
        documents.append(Document(role, f"hash-{role}", role,
                                  None if role == "requirement" else "vendor-a",
                                  None if role == "requirement" else "MODEL-A", parsed))
        if value is not None:
            proposals[role] = (value, raw_unit)
    repo = EvidenceRepository("delivery-bounds", 1, documents)

    def model_call(operation, messages, schema):
        assert operation == "extract"
        facts = []
        for block in json.loads(messages[-1].content)["evidence"]:
            if block["role"] not in proposals:
                continue
            value, raw_unit = proposals[block["role"]]
            facts.append({"field_key": "delivery_days", "raw_value": value,
                          "raw_unit": raw_unit, "evidence_refs": [block["evidence_id"]]})
        return AIMessage(content="", tool_calls=[{
            "id": "source-spans", "name": "FieldCandidates", "args": {"facts": facts},
        }])

    fact_set = extract_fields(repo, "vendor-a", [FieldKey.DELIVERY_DAYS],
                              model_call)[FieldKey.DELIVERY_DAYS]
    assert fact_set.complete
    result = evaluator(repo, "vendor-a", FieldKey.DELIVERY_DAYS,
                            fact_set.facts, scan_complete=fact_set.complete)
    assert result.processing_status == "complete"
    return result, fact_set


@pytest.mark.parametrize("required,offered,expected", [
    ("不超过9个自然日交付", "不超过9个自然日交付", "满足"),
    ("不超过9个自然日交付", "少于9个自然日交付", "满足"),
    ("少于9个自然日交付", "少于9个自然日交付", "满足"),
    ("少于9个自然日交付", "不超过9个自然日交付", "不满足"),
    ("不超过9个自然日交付", "9个自然日内交付", "满足"),
    ("9个自然日内交付", "9个自然日内交付", "满足"),
    ("9个自然日交付", "不超过9个自然日交付", "待人工确认"),
    ("至少9个自然日交付", "不超过9个自然日交付", "待人工确认"),
], ids=["same-inclusive", "strict-offer", "same-strict", "strict-requirement",
        "within-offer", "within-both", "exact-requirement", "minimum-requirement"])
def test_source_upper_bounds_compare_without_becoming_exact_values(required, offered, expected):
    result, facts = delivery_result(required, offered)
    assert result.business_status == expected
    assert next(f for f in facts.facts if f.role == "quote").comparator in {"lte", "lt"}


@pytest.mark.parametrize("kwargs", [
    {"unit": "工作日"}, {"start": "合同签订"}, {"start": "起点不明"},
    {"extra": "少于9个自然日交付"}, {"extra": "9个自然日交付"},
    {"extra": "不超过10个自然日交付", "extra_value": "10"},
])
def test_bounds_with_incompatible_basis_or_different_promises_require_review(kwargs):
    result, _ = delivery_result("不超过9个自然日交付", "不超过9个自然日交付", **kwargs)
    assert result.business_status == "待人工确认"


def test_approximate_delivery_upper_bound_cannot_be_published_as_meeting():
    result, _ = delivery_result("不超过9个自然日交付", "大约不超过9个自然日交付")
    assert result.business_status == "待人工确认"


def test_same_upper_bound_from_both_supplier_sources_is_allowed():
    result, _ = delivery_result("不超过9个自然日交付", "不超过9个自然日交付",
                               extra="不超过9个自然日交付")
    assert result.business_status == "满足"


@pytest.mark.parametrize("offered,value,expected", [
    ("不超过7个自然日交付", "7", "满足"),
    ("不超过10个自然日交付", "10", "不满足"),
])
def test_supplier_upper_bound_must_guarantee_the_procurement_deadline(offered, value, expected):
    result, _ = delivery_result("不超过9个自然日交付", offered, offered_value=value)
    assert result.business_status == expected


@pytest.mark.parametrize("offered,extra,value", [
    ("不超过9个自然日交付", "10个自然日交付", "10"),
    ("少于9个自然日交付", "9个自然日交付", "9"),
])
def test_an_exact_supplier_day_outside_another_source_bound_is_a_conflict(offered, extra, value):
    result, facts = delivery_result("不超过9个自然日交付", offered,
                                   extra=extra, extra_value=value)
    assert result.business_status == "资料冲突"
    supplier_refs = {ref for fact in facts.facts if fact.role != "requirement"
                     for ref in fact.evidence_refs}
    assert set(result.reason_evidence_refs) == supplier_refs


def test_archived_v1_replay_retains_the_actual_previous_upper_bound_semantics(monkeypatch):
    source = "9个自然日内交付"
    with monkeypatch.context() as legacy:
        legacy.setattr("backend.app.rules.fields.RULE_VERSION", "fields-v1")
        old, _ = delivery_result(source, source)
    current, _ = delivery_result(source, source)
    assert old.rule_version == "fields-v1"
    assert old.business_status == "待人工确认"
    assert current.rule_version == "fields-v4"
    assert current.business_status == "满足"
    assert old.check_result_id != current.check_result_id


@pytest.mark.parametrize("required,offered,value", [
    ("不超过9个自然日交付", "不超过10个自然日交付", "10"),
    ("少于9个自然日交付", "不超过9个自然日交付", "9"),
    ("不超过9个自然日交付", "少于10个自然日交付", "10"),
])
def test_archived_v2_keeps_review_while_v3_rejects_the_looser_commitment(required, offered, value):
    from backend.app.rules.fields import _evaluate_field_v2

    old, _ = delivery_result(required, offered, offered_value=value, evaluator=_evaluate_field_v2)
    current, _ = delivery_result(required, offered, offered_value=value)
    assert old.rule_version == "fields-v2" and old.business_status == "待人工确认"
    assert current.rule_version == "fields-v4" and current.business_status == "不满足"
    assert current.reason_code == "threshold_failed"
    assert current.check_result_id != old.check_result_id
