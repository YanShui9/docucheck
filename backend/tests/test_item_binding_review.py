"""Confirmed item ranges enable independent numeric rows without forged quotes."""

from copy import deepcopy

import pytest

from backend.app.agent.dynamic import DynamicAgent, review_plan
from backend.app.criteria.facts import (
    evaluate_checklist,
    revalidate_fact,
    validate_fact,
)
from backend.app.criteria.item_bindings import validate_bindings
from backend.app.criteria.models import ChecklistDefinition
from backend.app.evidence.models import Document
from backend.app.evidence.repository import EvidenceRepository, digest
from backend.app.parsing import EvidenceBlock, ParseResult
from backend.tests.test_dynamic_agent import call

V4 = "dynamic-rules-v4"


def sample(*, extra=False, partial=False, classification="category", title="苹果手机", specification=None):
    definition = ChecklistDefinition.model_validate({
        "items": [{"item_id": "phone", "name": "苹果手机", "specification": "采购10台，具体型号等待确认"}],
        "criteria": [
            {"criterion_id": "qty", "item_id": "phone", "name": "数量", "rule": {
                "type": "numeric", "operator": "eq", "value": "10", "unit": "台"},
             "source": {"kind": "user_defined", "reason": "买方确认"}},
            {"criterion_id": "delivery", "item_id": "phone", "name": "交期", "rule": {
                "type": "duration", "operator": "lte", "value": "14", "calendar": "natural_days", "start_event": "订单确认"},
             "source": {"kind": "user_defined", "reason": "买方确认"}},
            {"criterion_id": "model", "item_id": "phone", "name": "具体型号", "rule": {
                "type": "manual", "instruction": "待买方明确型号"},
             "source": {"kind": "user_defined", "reason": "待确认"}},
        ],
    })
    if specification is not None:
        definition = definition.model_copy(update={"items": [definition.items[0].model_copy(update={"specification": specification})]})
    texts = [title, "数量10台", "交期订单确认后18个自然日内交付"]
    if extra:
        texts.append("尚未确认所属品目的数量200台")
    blocks = tuple(EvidenceBlock(str(n), "quote", text, sheet="报价", cell=f"B{n+3}")
                   for n, text in enumerate(texts))
    parsed = ParseResult("quote", "parser", "partial" if partial else "complete", blocks,
                         total_sheets=1, processed_sheets=("报价",), coverage_issues=("tail_unreadable",) if partial else ())
    repo = EvidenceRepository("case", 1, [Document("quote", "a"*64, "quote", "A", "苹果手机", parsed)])
    requests = [{"document_id": "quote", "source_sha256": "a"*64, "parser_version": "parser", "item_id": "phone",
                 "selector": {"kind": "xlsx", "sheet": "报价", "start_row": 4, "end_row": 5,
                              "start_column": 2, "end_column": 2},
                 "anchor": {"evidence_refs": [repo.blocks[0].evidence_id], "quote": "苹果手机"},
                 "identifier_class": classification}]
    payload = {"schema_version": 1, "case_id": "case", "snapshot_id": repo.snapshot_id,
               "checklist_version_id": "confirmed", "checklist_digest": digest(definition.model_dump(mode="json")),
               "entries": validate_bindings(requests, definition, repo).payload()}
    frozen = {"version_id": "binding-a", "digest": digest(payload), "payload": payload}
    return definition, repo, frozen


def candidate(repo, criterion="qty"):
    b = repo.blocks[1 if criterion == "qty" else 2]
    return {"criterion_id": criterion, "item_id": "phone", "supplier": "A",
            "value": "10" if criterion == "qty" else "18", "unit": "台" if criterion == "qty" else "个自然日",
            "evidence_refs": [b.evidence_id], "quote": b.text}


def test_v4_independent_numeric_rows_with_separate_title_anchor():
    definition, repo, frozen = sample()
    legacy = validate_fact(candidate(repo), definition, repo, "A")
    assert not legacy["applicable"]
    facts = [validate_fact(candidate(repo, name), definition, repo, "A", rule_version=V4,
                           item_bindings=frozen) for name in ("qty", "delivery")]
    assert all(f["applicable"] and not f["ambiguous"] for f in facts)
    assert all(repo.blocks[0].evidence_id not in f["evidence_refs"] and "苹果手机" not in f["quote"] for f in facts)
    results = evaluate_checklist(definition, facts, {"A": True}, snapshot_id=repo.snapshot_id,
                                 rule_version=V4, item_coverage={"A": {"phone": True}}, item_bindings=frozen)
    assert [c["business_status"] for c in results] == ["满足", "不满足", "待人工确认"]
    assert revalidate_fact(facts[0], definition, repo, "A", rule_version=V4, item_bindings=frozen) == facts[0]


def test_v4_scope_and_binding_identity_are_not_model_arguments():
    definition, repo, frozen = sample(extra=True)
    bad = candidate(repo) | {"evidence_refs": [repo.blocks[-1].evidence_id], "quote": repo.blocks[-1].text, "value": "200"}
    with pytest.raises(ValueError, match="item_binding_scope"):
        validate_fact(bad, definition, repo, "A", rule_version=V4, item_bindings=frozen)
    fact = validate_fact(candidate(repo), definition, repo, "A", rule_version=V4, item_bindings=frozen)
    with pytest.raises(ValueError):
        revalidate_fact(fact, definition, repo, "A", rule_version=V4, item_bindings=frozen | {"version_id": "binding-b"})


@pytest.mark.parametrize("changes", [{"extra": True}, {"partial": True}])
def test_v4_unassigned_tail_and_partial_parse_cannot_prove_missing_evidence(changes):
    definition, repo, frozen = sample(**changes)
    plans, complete = review_plan(repo, definition, rule_version=V4, item_bindings=frozen)
    assert complete == {"A": {"phone": False}}
    assert all({b["evidence_id"] for b in batch} <= set(frozen["payload"]["entries"][0]["evidence_refs"])
               for batch, _, _ in plans["A"])


def test_v4_manual_requirement_remains_manual_when_processing_is_partial():
    definition, repo, frozen = sample()
    results = evaluate_checklist(definition, [], {"A": False}, snapshot_id=repo.snapshot_id,
                                 rule_version=V4, item_coverage={"A": {"phone": False}}, item_bindings=frozen)
    assert results[0]["business_status"] is None
    assert results[-1]["business_status"] == "待人工确认" and not results[-1]["is_formal"]


def test_v4_unknown_classification_cannot_clear_existing_product_identifier():
    definition, repo, frozen = sample(classification="unknown")
    fact = validate_fact(candidate(repo), definition, repo, "A", rule_version=V4, item_bindings=frozen)
    assert not fact["applicable"] and fact["ambiguous"]


class Provider:
    context_window = 32768

    def __init__(self):
        self.batches = []

    def decide(self, messages, tools, **options):
        return call("review_checklist", {})

    def extract(self, messages, schema, **options):
        import json
        data = json.loads(messages[-1].content)
        self.batches.append(deepcopy(data))
        facts = []
        for b in data["evidence"]:
            if not b["text"].startswith(("数量", "交期")):
                continue
            name = "qty" if b["text"].startswith("数量") else "delivery"
            if name not in {c["criterion_id"] for c in data["criteria"]}:
                continue
            facts.append({"criterion_id": name, "item_id": "phone", "supplier": data["supplier"],
                          "value": "10" if name == "qty" else "18", "unit": "台" if name == "qty" else "个自然日",
                          "evidence_refs": [b["evidence_id"]], "quote": b["text"]})
        return call("DynamicFactCandidates", {"facts": facts})


@pytest.mark.parametrize("second", ["valid", "same_error", "drop_numeric"])
def test_fact_basis_correction_is_once_and_cannot_turn_dropped_numeric_fact_into_coverage(second):
    definition, repo, frozen = sample()

    class BasisCorrection(Provider):
        def extract(self, messages, schema, **options):
            reply = super().extract(messages, schema, **options)
            if len(self.batches) == 1 or second == "same_error":
                # The original contains 订单确认后, not 合同签订后.
                reply.tool_calls[0]["args"]["facts"][1]["start_event"] = "合同签订后"
            elif second == "drop_numeric":
                reply.tool_calls[0]["args"]["facts"] = []
            return reply

    provider = BasisCorrection()
    result = DynamicAgent(repo, provider, definition, item_bindings=frozen).ask("审查", operation="review")
    assert len(provider.batches) == 2
    assert provider.batches[0] == provider.batches[1]
    if second == "valid":
        assert result["status"] == "completed"
        assert [c["business_status"] for c in result["payload"]["checks"]] == ["满足", "不满足", "待人工确认"]
    else:
        assert result["error"] == "coverage_incomplete"
        assert not any(c["is_formal"] for c in result["payload"]["checks"])


def test_out_of_scope_candidates_are_not_replayed_for_semantic_correction():
    definition, repo, frozen = sample(extra=True)

    class WrongScope(Provider):
        def extract(self, messages, schema, **options):
            super().extract(messages, schema, **options)
            return call("DynamicFactCandidates", {"facts": [candidate(repo) | {
                "evidence_refs": [repo.blocks[-1].evidence_id], "quote": repo.blocks[-1].text, "value": "200"}]})

    provider = WrongScope()
    result = DynamicAgent(repo, provider, definition, item_bindings=frozen).ask("审查", operation="review")
    assert len(provider.batches) == 1
    assert result["error"] == "coverage_incomplete"
    assert result["payload"]["facts"] == []


@pytest.mark.parametrize("invalid_conflict", [False, True])
def test_fact_correction_keeps_verified_conflict_or_incomplete_source_in_result_and_cache(invalid_conflict):
    from dataclasses import replace

    definition, repo, frozen = sample()
    original = repo.documents[0]
    parsed = replace(original.parsed, blocks=(*original.parsed.blocks,
                     EvidenceBlock("conflict", "quote", "数量20台", sheet="报价", cell="B6")))
    repo = EvidenceRepository("case", 1, [replace(original, parsed=parsed)])
    requests = [{"document_id": "quote", "source_sha256": "a" * 64, "parser_version": "parser", "item_id": "phone",
                 "selector": {"kind": "xlsx", "sheet": "报价", "start_row": 4, "end_row": 6, "start_column": 2, "end_column": 2},
                 "anchor": {"evidence_refs": [repo.blocks[0].evidence_id], "quote": "苹果手机"}, "identifier_class": "category"}]
    frozen["payload"].update(snapshot_id=repo.snapshot_id, entries=validate_bindings(requests, definition, repo).payload())
    frozen["digest"] = digest(frozen["payload"])

    class ConflictCorrection(Provider):
        def extract(self, messages, schema, **options):
            reply = super().extract(messages, schema, **options)
            if len(self.batches) == 1:
                reply.tool_calls[0]["args"]["facts"][1]["start_event"] = "合同签订后"
                reply.tool_calls[0]["args"]["facts"].append(candidate(repo) | {
                    "value": "19" if invalid_conflict else "20", "quote": repo.blocks[-1].text,
                    "evidence_refs": [repo.blocks[-1].evidence_id]})
            else:
                # Deliberately omit the conflicting source in the correction;
                # the production merge must retain it, or keep coverage partial.
                reply.tool_calls[0]["args"]["facts"] = [
                    f for f in reply.tool_calls[0]["args"]["facts"]
                    if repo.blocks[-1].evidence_id not in f["evidence_refs"]]
            return reply

    cache = {}
    provider = ConflictCorrection()
    result = DynamicAgent(repo, provider, definition, item_bindings=frozen,
                          cache_save=lambda key, identity, raw: cache.update({(key, identity): raw})).ask("审查", operation="review")
    if invalid_conflict:
        assert result["error"] == "coverage_incomplete"
        assert not any(c["is_formal"] for c in result["payload"]["checks"])
        assert cache == {}
        return
    assert result["status"] == "completed"
    assert result["payload"]["checks"][0]["business_status"] == "资料冲突"
    assert {f["value"] for f in result["payload"]["facts"] if f["criterion_id"] == "qty"} == {"10", "20"}
    reused = Provider()
    cached = DynamicAgent(repo, reused, definition, item_bindings=frozen,
                          cache_load=lambda key, identity: cache.get((key, identity))).ask("审查", operation="review")
    assert reused.batches == [] and cached["status"] == "completed"
    assert cached["payload"]["checks"][0]["business_status"] == "资料冲突"


def test_semantic_correction_does_not_replay_unknown_request_or_exceed_call_budget():
    from backend.app.agent.policy import Budget
    from backend.app.providers.base import ProviderError

    definition, repo, frozen = sample()

    class UnknownCorrection(Provider):
        def extract(self, messages, schema, **options):
            reply = super().extract(messages, schema, **options)
            if len(self.batches) == 1:
                reply.tool_calls[0]["args"]["facts"][1]["start_event"] = "合同签订后"
                return reply
            raise ProviderError("connection_error", outcome_unknown=True)

    for max_calls, expected in [(2, "model_budget"), (3, "connection_error")]:
        provider = UnknownCorrection()
        result = DynamicAgent(repo, provider, definition, item_bindings=frozen,
                              budget=Budget(max_calls=max_calls)).ask("审查", operation="review")
        assert result["error"] == expected
        assert len(provider.batches) == max_calls - 1
        assert result["usage"]["unknown_usage_calls"] == (1 if max_calls == 3 else 0)
        assert not any(c["is_formal"] for c in result["payload"]["checks"])


def test_v4_agent_uses_only_bound_range_and_keeps_binding_in_cache_identity():
    definition, repo, frozen = sample()
    provider = Provider()
    result = DynamicAgent(repo, provider, definition, item_bindings=frozen).ask("审查", operation="review")
    assert result["status"] == "completed", result
    assert [c["business_status"] for c in result["payload"]["checks"]] == ["满足", "不满足", "待人工确认"]
    assert all(b["text"] != "苹果手机" for data in provider.batches for b in data["evidence"])
    assert result["payload"]["item_binding_digest"] == frozen["digest"]
    old_plan, _ = review_plan(repo, definition, rule_version=V4, item_bindings=frozen)
    new_plan, _ = review_plan(repo, definition, rule_version=V4, item_bindings=frozen | {"version_id": "binding-b"})
    assert old_plan["A"][0][-1]["batch_id"] != new_plan["A"][0][-1]["batch_id"]


@pytest.mark.parametrize("manual_count", [1, 9])
def test_review_output_allocation_handles_eleven_metrics_without_changing_batches(manual_count):
    from backend.app.providers.base import ProviderError

    definition, repo, frozen = sample()
    data = definition.model_dump(mode="json")
    for index in range(manual_count - 1):
        data["criteria"].append({**deepcopy(data["criteria"][-1]),
                                 "criterion_id": f"manual-{index}", "name": f"人工指标{index}"})
    definition = ChecklistDefinition.model_validate(data)
    frozen["payload"]["checklist_digest"] = digest(data)
    frozen["digest"] = digest(frozen["payload"])
    planned, _ = review_plan(repo, definition, rule_version=V4, item_bindings=frozen)

    class OutputLimited(Provider):
        def __init__(self):
            super().__init__()
            self.output_caps = []

        def extract(self, messages, schema, **options):
            self.output_caps.append(options["max_output_tokens"])
            # Reproduce the live eleven-metric response truncated by the old
            # 1536-token cap. No replay or relaxed evidence validation is used.
            if manual_count == 9 and options["max_output_tokens"] < 2000:
                raise ProviderError("invalid_output", detail="output_limit")
            return super().extract(messages, schema, **options)

    provider = OutputLimited()
    result = DynamicAgent(repo, provider, definition, item_bindings=frozen).ask("审查", operation="review")
    assert result["status"] == "completed", result["error"]
    assert len(result["payload"]["checks"]) == manual_count + 2
    assert [c["business_status"] for c in result["payload"]["checks"]] == ["满足", "不满足", *(["待人工确认"] * manual_count)]
    assert provider.output_caps == [1536 if manual_count == 1 else 4096]
    assert result["payload"]["batch_manifest"] == [entry for _, _, entry in planned["A"]]


def test_review_output_allocation_still_obeys_shared_output_budget(monkeypatch):
    from backend.app.agent.policy import MODE_LIMITS
    from backend.app.providers.base import ProviderError

    definition, repo, frozen = sample()
    data = definition.model_dump(mode="json")
    for index in range(8):
        data["criteria"].append({**deepcopy(data["criteria"][-1]),
                                 "criterion_id": f"manual-{index}", "name": f"人工指标{index}"})
    definition = ChecklistDefinition.model_validate(data)
    frozen["payload"]["checklist_digest"] = digest(data)
    frozen["digest"] = digest(frozen["payload"])
    monkeypatch.setitem(MODE_LIMITS, "standard", {**MODE_LIMITS["standard"], "max_output": 800})

    class Limited(Provider):
        def __init__(self):
            super().__init__()
            self.output_caps = []

        def extract(self, messages, schema, **options):
            self.output_caps.append(options["max_output_tokens"])
            raise ProviderError("invalid_output", detail="output_limit")

    provider = Limited()
    result = DynamicAgent(repo, provider, definition, item_bindings=frozen).ask("审查", operation="review")
    assert result["status"] == "partial" and result["error"] == "invalid_output"
    assert provider.output_caps == [775]
    assert result["usage"]["output_reserved"] <= 800
    assert not any(c["is_formal"] for c in result["payload"]["checks"])


def test_anchor_full_block_business_fact_is_not_coverage():
    definition, repo, frozen = sample(title="苹果手机，数量20台")
    result = DynamicAgent(repo, Provider(), definition, item_bindings=frozen).ask("审查", operation="review")
    assert result["status"] == "partial"
    assert result["payload"]["item_coverage"] == {"A": {"phone": False}}
    assert not any(c["is_formal"] for c in result["payload"]["checks"])


@pytest.mark.parametrize("specification", ["型号：苹果十五代", "型号：iPhone 17 Pro", "M1", "苹果十五代", "iPhone 17 Pro"])
def test_unparsed_explicit_sku_never_clears_model_guard(specification):
    title = "苹果手机，型号：M2"
    definition, repo, frozen = sample(title=title, specification=specification)
    fact = validate_fact(candidate(repo), definition, repo, "A", rule_version=V4, item_bindings=frozen)
    assert not fact["applicable"] and fact["ambiguous"]


def test_arbitrary_sku_label_cannot_hide_anchor_quantity():
    definition, repo, frozen = sample(title="苹果手机，数量20台")
    raw = frozen["payload"]["entries"][0]
    from backend.app.criteria.item_bindings import ItemBindingEntry

    entry = {k: v for k, v in raw.items() if k in ItemBindingEntry.model_fields}
    entry.update(supplier_sku="数量20台", sku_anchor={"evidence_refs": [repo.blocks[0].evidence_id], "quote": "数量20台"})
    payload = frozen["payload"] | {"entries": validate_bindings([entry], definition, repo).payload()}
    frozen = frozen | {"payload": payload, "digest": digest(payload)}
    _, complete = review_plan(repo, definition, rule_version=V4, item_bindings=frozen)
    assert complete == {"A": {"phone": False}}


@pytest.mark.parametrize("specification", ["", "苹果手机", "采购10台", "数量：10台", "具体型号等待确认"])
def test_generic_or_pending_buyer_spec_does_not_become_exact_sku(specification):
    definition, repo, frozen = sample(specification=specification)
    assert validate_fact(candidate(repo), definition, repo, "A", rule_version=V4, item_bindings=frozen)["applicable"]


def test_v4_empty_enabled_scope_is_not_claimed_complete():
    definition, repo, frozen = sample()
    definition = definition.model_copy(update={"criteria": [c.model_copy(update={"enabled": False}) for c in definition.criteria]})
    payload = frozen["payload"] | {"checklist_digest": digest(definition.model_dump(mode="json"))}
    frozen = frozen | {"payload": payload, "digest": digest(payload)}
    assert evaluate_checklist(definition, [], {"A": False}, snapshot_id=repo.snapshot_id, rule_version=V4,
                              item_coverage={"A": {}}, item_bindings=frozen) == []
