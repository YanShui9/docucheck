"""Versioned grammatical start events cannot alter old formal identities."""
from copy import deepcopy
from dataclasses import replace

import pytest

from backend.app.agent.dynamic import DynamicAgent, review_plan
from backend.app.criteria.facts import (
    evaluate_checklist,
    revalidate_fact,
    validate_fact,
)
from backend.app.criteria.item_bindings import validate_bindings
from backend.app.criteria.models import ChecklistDefinition
from backend.app.evidence.repository import EvidenceRepository, digest
from backend.tests.test_dynamic_agent import call
from backend.tests.test_item_binding_review import Provider, candidate, sample


@pytest.mark.parametrize("suffix", ["后", "之后", "以后", "起", "之日起"])
def test_v5_grammar_suffix_is_one_start_event_and_overdue_promise_fails(suffix):
    definition, repo, frozen = sample()
    data = definition.model_dump(mode="json")
    data["criteria"][1]["rule"]["start_event"] = "订单确认" + suffix
    definition = ChecklistDefinition.model_validate(data)
    frozen["payload"]["checklist_digest"] = digest(data)
    frozen["digest"] = digest(frozen["payload"])
    fact = validate_fact(candidate(repo, "delivery") | {"start_event": "订单确认后"},
                         definition, repo, "A", rule_version="dynamic-rules-v5", item_bindings=frozen)
    assert fact["start_event"] == "订单确认" and not fact["ambiguous"]
    checks = evaluate_checklist(definition, [fact], {"A": True}, snapshot_id=repo.snapshot_id,
                               rule_version="dynamic-rules-v5", item_coverage={"A": {"phone": True}}, item_bindings=frozen)
    assert checks[1]["business_status"] == "不满足"
    assert revalidate_fact(fact, definition, repo, "A", rule_version="dynamic-rules-v5", item_bindings=frozen) == fact


def test_v4_replay_and_manifest_identity_stay_distinct_from_v5():
    definition, repo, frozen = sample()
    old = validate_fact(candidate(repo, "delivery"), definition, repo, "A", rule_version="dynamic-rules-v4", item_bindings=frozen)
    old_plan, _ = review_plan(repo, definition, rule_version="dynamic-rules-v4", item_bindings=frozen)
    new_plan, _ = review_plan(repo, definition, rule_version="dynamic-rules-v5", item_bindings=frozen)
    assert old_plan["A"][0][-1]["batch_id"] != new_plan["A"][0][-1]["batch_id"]
    assert old_plan["A"][0][-1]["input_digest"] != new_plan["A"][0][-1]["input_digest"]
    assert revalidate_fact(deepcopy(old), definition, repo, "A", rule_version="dynamic-rules-v4", item_bindings=frozen) == old
    with pytest.raises(ValueError, match="fact_start_event"):
        validate_fact(candidate(repo, "delivery") | {"start_event": "合同签订后"}, definition, repo, "A",
                      rule_version="dynamic-rules-v5", item_bindings=frozen)


def test_v5_real_multiple_start_events_remain_ambiguous():
    from dataclasses import replace

    from backend.app.evidence.repository import EvidenceRepository

    definition, repo, frozen = sample()
    document = repo.documents[0]
    blocks = list(document.parsed.blocks)
    blocks[2] = replace(blocks[2], text="交期订单确认后或合同签订后18个自然日内交付")
    repo = EvidenceRepository("case", 1, [replace(document, parsed=replace(document.parsed, blocks=tuple(blocks)))])
    request = {k: v for k, v in frozen["payload"]["entries"][0].items()
               if k in {"document_id", "source_sha256", "parser_version", "item_id", "selector", "anchor", "identifier_class"}}
    request["anchor"] = {"evidence_refs": [repo.blocks[0].evidence_id], "quote": "苹果手机"}
    frozen["payload"].update(snapshot_id=repo.snapshot_id, entries=validate_bindings([request], definition, repo).payload())
    frozen["digest"] = digest(frozen["payload"])
    fact = validate_fact(candidate(repo, "delivery"), definition, repo, "A", rule_version="dynamic-rules-v5", item_bindings=frozen)
    assert fact["ambiguous"] and fact["start_event"] is None


@pytest.mark.parametrize("decision", ["review_checklist", "get_checklist"])
@pytest.mark.parametrize("tampered", [False, True])
def test_v4_manual_alternative_is_revalidated_then_carried_into_full_v5_review(monkeypatch, decision, tampered):
    from backend.app.agent import dynamic

    definition, repo, frozen = sample()
    original = repo.documents[0]
    blocks = list(original.parsed.blocks)
    blocks[1] = replace(blocks[1], text="数量10台或20台")
    repo = EvidenceRepository("case", 1, [replace(original, parsed=replace(original.parsed, blocks=tuple(blocks)))])
    request = {k: v for k, v in frozen["payload"]["entries"][0].items()
               if k in {"document_id", "source_sha256", "parser_version", "item_id", "selector", "anchor", "identifier_class"}}
    request["anchor"] = {"evidence_refs": [repo.blocks[0].evidence_id], "quote": "苹果手机"}
    frozen["payload"].update(snapshot_id=repo.snapshot_id, entries=validate_bindings([request], definition, repo).payload())
    frozen["digest"] = digest(frozen["payload"])
    with monkeypatch.context() as patch:
        patch.setattr(dynamic, "BINDING_RULE_VERSION", "dynamic-rules-v4")
        result = DynamicAgent(repo, Provider(), definition, item_bindings=frozen).ask("审查")
    assert result["status"] == "completed"
    previous = deepcopy(result["payload"])
    alternative = validate_fact(candidate(repo) | {"value": "20"}, definition, repo, "A",
                                rule_version="dynamic-rules-v4", item_bindings=frozen)
    previous["facts"].append(alternative)
    previous["needs_review"] = True
    if tampered:
        previous["item_binding_digest"] = "tampered"

    class ReadOrReview(Provider):
        def decide(self, messages, tools, **options):
            return call(decision, {})

    provider = ReadOrReview()
    current = DynamicAgent(repo, provider, definition, item_bindings=frozen,
                           previous_payload=previous).ask("重新审查")
    if tampered:
        assert current["error"] == "manual_alternative_revalidation_required"
        assert provider.batches == []
        assert not any(c["is_formal"] for c in current["payload"]["checks"])
        return
    assert current["status"] == "completed" and current["payload"]["needs_review"]
    assert current["payload"]["rule_version"] == "dynamic-rules-v5"
    assert len(provider.batches) == len(current["payload"]["batch_manifest"]) == 1
    assert {f["value"] for f in current["payload"]["facts"] if f["criterion_id"] == "qty"} == {"10", "20"}
    assert current["payload"]["checks"][0]["business_status"] == "资料冲突"
    assert current["payload"]["batch_manifest"] != previous["batch_manifest"]


def test_v4_suffix_ambiguity_and_identity_are_unchanged():
    definition, repo, frozen = sample()
    data = definition.model_dump(mode="json")
    data["criteria"][1]["rule"]["start_event"] = "订单确认后"
    definition = ChecklistDefinition.model_validate(data)
    frozen["payload"]["checklist_digest"] = digest(data)
    frozen["digest"] = digest(frozen["payload"])
    old = validate_fact(candidate(repo, "delivery"), definition, repo, "A", rule_version="dynamic-rules-v4", item_bindings=frozen)
    assert old["ambiguous"] and old["start_event"] is None
    assert revalidate_fact(old, definition, repo, "A", rule_version="dynamic-rules-v4", item_bindings=frozen) == old


@pytest.mark.parametrize("proposed", [{"start_event": "收到预付款后"}, {"calendar": "working_days"}])
def test_v5_start_grammar_does_not_merge_distinct_payment_events_or_calendars(proposed):
    definition, repo, frozen = sample()
    with pytest.raises(ValueError, match="fact_start_event|fact_calendar"):
        validate_fact(candidate(repo, "delivery") | proposed, definition, repo, "A",
                      rule_version="dynamic-rules-v5", item_bindings=frozen)
