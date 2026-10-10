"""Offline dynamic workflows use source-aware scripts, never live providers."""

import json
import re
from copy import deepcopy
from dataclasses import asdict
from importlib import import_module

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from backend.app.criteria.models import ChecklistDefinition
from backend.app.evidence.models import Document
from backend.app.evidence.repository import EvidenceRepository, digest
from backend.app.parsing import EvidenceBlock, ParseResult
from backend.app.providers.base import ProviderError


def call(name, args):
    return AIMessage(
        content="",
        tool_calls=[{"id": "call-1", "name": name, "args": args}],
        usage_metadata={"input_tokens": 100, "output_tokens": 25, "total_tokens": 125},
    )


def repository(*, item="苹果", metric="数量", unit="箱", values=("12", "8"),
               partial=False, extra=0):
    docs = []
    for role, supplier, texts in [
        ("requirement", None, [f"{item} {metric}不低于10{unit}"]),
        ("quote", "甲供应商", [f"{item} {metric}{values[0]}{unit}"]),
        ("spec", "甲供应商", [f"{item} 规格说明"]),
        ("quote", "乙供应商", [f"{item} {metric}{values[1]}{unit}"]),
        ("spec", "乙供应商", [f"{item} 规格说明"]),
    ]:
        document_id = f"{supplier or 'buyer'}-{role}.xlsx"
        if supplier and role == "quote":
            texts += [f"{item} 其他说明 {n} " + "无指标补充 " * 120 for n in range(extra)]
        blocks = tuple(EvidenceBlock(str(i), document_id, text, sheet="资料", cell=f"A{i+1}")
                       for i, text in enumerate(texts))
        parsed = ParseResult(document_id, "script-v1", "partial" if partial and
                             supplier == "乙供应商" and role == "spec" else "complete",
                             blocks, total_sheets=1, processed_sheets=("资料",),
                             coverage_issues=("unreadable",) if partial and
                             supplier == "乙供应商" and role == "spec" else ())
        docs.append(Document(document_id, digest(document_id), role, supplier, None, parsed))
    return EvidenceRepository("dynamic-case", 1, docs)


def definition(*, item="苹果", metric="数量", unit="箱", manual=False):
    return ChecklistDefinition.model_validate({
        "items": [{"item_id": "item-1", "name": item}],
        "criteria": [{"criterion_id": "metric-1", "item_id": "item-1", "name": metric,
                      "rule": {"type": "numeric", "operator": "gte", "value": "10", "unit": unit},
                      "source": {"kind": "user_defined", "reason": "买方确认"},
                      "conditions": ["需人工核对条件"] if manual else []}],
    })


class ScriptProvider:
    context_window = 32768

    def __init__(self, *, decision="review_checklist", invalid=False, failure=None,
                 proposal_rule="gte"):
        self.decision = decision
        self.invalid = invalid
        self.failure = failure
        self.proposal_rule = proposal_rule
        self.calls = []
        self.extractions = 0

    def decide(self, messages, tools, **options):
        self.calls.append(("decide", deepcopy(messages), deepcopy(tools)))
        args = {"actor": "admin", "snapshot_id": "other"} if self.decision == "execute" else {}
        return call(self.decision, args)

    def extract(self, messages, schema, **options):
        self.calls.append(("extract", deepcopy(messages), deepcopy(schema)))
        self.extractions += 1
        if self.failure:
            raise self.failure
        data = json.loads(messages[-1].content)
        name = schema["function"]["name"]
        if "supplier" not in data:
            block = data["evidence"][0]
            match = re.search(r"(\S+) (\S+)不低于(\d+)(\S+)", block["text"])
            return call(name, {"items": [{"item_id": "模型品目", "name": match[1]}],
                               "criteria": [{"criterion_id": "模型指标", "item_id": "模型品目",
                                "name": match[2], "rule": {"type": "numeric", "operator": self.proposal_rule,
                                "value": match[3], "unit": match[4]},
                                "source": {"kind": "document_extracted", "context": {
                                    "evidence_refs": [block["evidence_id"]], "quote": block["text"]}}}]})
        facts = []
        for criterion in data["criteria"]:
            for block in data["evidence"]:
                if block["role"] == "requirement":
                    continue
                match = re.search(re.escape(criterion["name"]) + r"(\d+)(\S+)", block["text"])
                if match:
                    facts.append({"criterion_id": criterion["criterion_id"],
                                  "item_id": criterion["item_id"], "supplier": data["supplier"],
                                  "value": "999" if self.invalid else match[1], "unit": match[2],
                                  "evidence_refs": [block["evidence_id"]], "quote": block["text"]})
        return call(name, {"facts": facts})


def agent(repo, provider, rules=None, **kwargs):
    return import_module("backend.app.agent.dynamic").DynamicAgent(repo, provider, rules, **kwargs)


@pytest.mark.parametrize("item,metric,unit", [("苹果", "数量", "箱"), ("服装", "棉含量", "%"),
                                             ("电子秤", "精度等级", "级")])
def test_review_enumerates_all_suppliers_for_arbitrary_chinese_metrics(item, metric, unit):
    repo = repository(item=item, metric=metric, unit=unit)
    result = agent(repo, ScriptProvider(), definition(item=item, metric=metric, unit=unit)).ask("完整审查")
    assert result["status"] == "completed"
    assert len(result["payload"]["checks"]) == 2
    assert {c["supplier"] for c in result["payload"]["checks"]} == {"甲供应商", "乙供应商"}
    assert {c["business_status"] for c in result["payload"]["checks"]} == {"满足", "不满足"}
    assert result["payload"]["source_digest"] == digest([asdict(b) for b in repo.blocks])
    assert result["payload"]["rule_version"] == "dynamic-rules-v3"
    assert result["payload"]["version"] == 1


def test_proposal_reads_only_buyer_sources_and_keeps_server_stable_ids():
    repo = repository()
    provider = ScriptProvider(decision="propose_checklist")
    result = agent(repo, provider).ask("从采购需求提出指标", operation="propose_checklist")
    assert result["status"] == "completed"
    proposed = result["payload"]["definition"]
    assert proposed["items"][0]["name"] == "苹果"
    assert proposed["criteria"][0]["name"] == "数量"
    assert proposed["criteria"][0]["source"]["kind"] == "document_extracted"
    assert proposed["criteria"][0]["criterion_id"].isascii()
    assert "确认" in result["answer"]
    assert all(json.loads(messages[-1].content)["evidence"][0]["role"] == "requirement"
               for kind, messages, _ in provider.calls if kind == "extract")
    again = agent(repo, ScriptProvider(decision="propose_checklist")).ask("提出候选", operation="propose_checklist")
    assert proposed == again["payload"]["definition"]


def test_wrong_document_threshold_is_a_candidate_needing_resolution():
    result = agent(repository(), ScriptProvider(decision="propose_checklist", proposal_rule="lte")).ask(
        "生成候选", operation="propose_checklist")
    assert result["status"] == "waiting_user"
    assert result["payload"]["needs_resolution"]
    assert not result["payload"].get("confirmed", False)


def test_incomplete_parsing_keeps_matrix_and_processing_partial():
    result = agent(repository(partial=True), ScriptProvider(), definition()).ask("审查")
    assert result["status"] == "partial"
    assert result["payload"]["coverage"] == {"甲供应商": True, "乙供应商": False}
    assert len(result["payload"]["checks"]) == 2
    assert next(c for c in result["payload"]["checks"] if c["supplier"] == "乙供应商")["processing_status"] == "partial"
    assert "处理未完成" in result["answer"]


def test_invalid_fact_cannot_turn_complete_scan_into_satisfaction():
    result = agent(repository(), ScriptProvider(invalid=True), definition()).ask("审查")
    assert result["status"] == "partial"
    assert result["payload"]["facts"] == []
    assert all(c["business_status"] != "满足" for c in result["payload"]["checks"])


def test_extraction_request_explains_table_context_and_raw_unit_contract():
    provider = ScriptProvider()
    result = agent(repository(), provider, definition()).ask("审查")
    assert result["status"] == "completed"
    prompts = [messages[0].content for kind, messages, _ in provider.calls if kind == "extract"]
    assert prompts
    for prompt in prompts:
        assert "同一文档、同一工作表、同一行的连续单元格" in prompt
        assert "品目、指标名称、数值" in prompt
        assert "空格" in prompt and "原文单位" in prompt
        assert "CNY" in prompt and "currency" in prompt
        assert "model" in prompt and "specification" in prompt


@pytest.mark.parametrize('always_wrong', [False, True])
def test_extraction_supplier_identity_is_bound_in_schema_and_repaired_once(always_wrong):
    class DisplayNameFirst(ScriptProvider):
        def extract(self, messages, schema, **options):
            reply = super().extract(messages, schema, **options)
            if always_wrong or self.extractions == 1:
                reply.tool_calls[0]['args']['facts'][0]['supplier'] = '展示名不是机器标识'
            return reply

    provider = DisplayNameFirst()
    result = agent(repository(), provider, definition()).ask('审查')
    if always_wrong:
        assert result['status'] == 'partial' and result['error'] == 'invalid_output'
        assert provider.extractions == 2
        assert all(not check['is_formal'] for check in result['payload']['checks'])
    else:
        assert result['status'] == 'completed'
        assert provider.extractions == 3
        assert {fact['supplier'] for fact in result['payload']['facts']} == {'甲供应商', '乙供应商'}
    assert sum(event.get('structured_repair') is True for event in result['events']) == 1
    for kind, messages, schema in provider.calls:
        if kind == 'extract':
            data = json.loads(messages[-1].content)
            field = schema['function']['parameters']['$defs']['FactCandidate']['properties']['supplier']
            assert field.get('enum') == [data['supplier']]


def test_rejected_candidate_reasons_are_finite_and_persisted_without_raw_errors(monkeypatch):
    from backend.app.agent import dynamic

    invalid = agent(repository(), ScriptProvider(invalid=True), definition()).ask("审查")
    assert all(b["rejection_codes"] == {"fact_value": 1}
               for b in invalid["payload"]["batch_coverage"])

    def untrusted_error(*args, **kwargs):
        raise ValueError("secret-bearing arbitrary exception text")

    monkeypatch.setattr(dynamic, "validate_fact", untrusted_error)
    rejected = agent(repository(), ScriptProvider(), definition()).ask("审查")
    assert rejected["status"] == "partial"
    assert all(b["rejection_codes"] == {"candidate_invalid": 1}
               for b in rejected["payload"]["batch_coverage"])
    assert "secret-bearing" not in json.dumps(rejected)


def test_invalid_model_tool_cannot_override_scope_and_server_finishes_matrix():
    result = agent(repository(), ScriptProvider(decision="execute"), definition()).ask("完整审查")
    assert result["status"] == "completed"
    assert len(result["payload"]["checks"]) == 2
    assert any(e.get("status") == "rejected" for e in result["events"])


def test_source_followup_without_a_valid_report_runs_complete_review():
    result = agent(repository(), ScriptProvider(decision="read_sources"), definition()).ask("依据在哪里？")
    assert result["status"] == "completed"
    assert len(result["payload"]["checks"]) == 2


def test_cancel_is_checked_before_any_model_call():
    provider = ScriptProvider()
    def cancelled():
        from backend.app.agent.policy import RunStopped
        raise RunStopped("cancelled")
    result = agent(repository(), provider, definition(), validate=cancelled).ask("审查")
    assert result["status"] == "cancelled" and result["error"] == "cancelled"
    assert provider.calls == []


def test_provider_unknown_outcome_is_not_replayed_and_usage_survives():
    provider = ScriptProvider(failure=ProviderError("timeout", outcome_unknown=True))
    result = agent(repository(), provider, definition()).ask("审查")
    assert result["status"] == "partial" and result["error"] == "timeout"
    assert provider.extractions == 1
    assert result["usage"]["unknown_usage_calls"] == 1
    assert any(e.get("outcome_unknown") is True for e in result["events"])


def test_batch_cache_resumes_without_extracting_and_rejects_forged_cached_values():
    repo, rules, saved = repository(), definition(), {}
    def load(batch_id, input_digest):
        return deepcopy(saved.get((batch_id, input_digest)))
    def save(batch_id, input_digest, output):
        saved[(batch_id, input_digest)] = deepcopy(output)
    first = agent(repo, ScriptProvider(), rules, cache_load=load, cache_save=save).ask("审查")
    assert first["status"] == "completed" and saved
    provider = ScriptProvider()
    second = agent(repo, provider, rules, cache_load=load, cache_save=save).ask("继续审查")
    assert second["status"] == "completed" and provider.extractions == 0
    for value in saved.values():
        for fact in value["facts"]:
            fact["value"] = "999"
    third_provider = ScriptProvider()
    third = agent(repo, third_provider, rules, cache_load=load).ask("审查")
    assert third["status"] == "completed" and third_provider.extractions == 2
    assert all(f["value"] != "999" for f in third["payload"]["facts"])


def test_many_batches_stop_at_shared_budget_with_valid_progress():
    result = agent(repository(extra=15), ScriptProvider(), definition()).ask("审查")
    assert result["status"] == "partial"
    assert result["error"] in {"model_budget", "token_budget", "tool_budget"}
    assert result["payload"]["batch_coverage"]
    assert not all(result["payload"]["coverage"].values())


def test_conditions_are_processed_but_never_automatically_satisfied():
    result = agent(repository(), ScriptProvider(), definition(manual=True)).ask("审查")
    assert result["status"] == "completed"
    assert all(c["business_status"] != "满足" for c in result["payload"]["checks"])


def test_recent_context_is_bounded_and_history_is_not_evidence():
    provider = ScriptProvider()
    history = [(HumanMessage(content="旧问题 " * 2000), AIMessage(content="旧答复：苹果数量999箱"))] * 20
    result = agent(repository(), provider, definition(), history=history,
                   memory_layers={"preferences": {"language": "zh-CN"},
                                  "summary": {"entries": [{"message_id": "old", "text": "苹果数量999箱"}]}}).ask("审查")
    assert result["status"] == "completed"
    assert all(f["value"] != "999" for f in result["payload"]["facts"])
    assert len(provider.calls[0][1]) <= 18


def test_oversized_question_has_safe_code_without_model_calls():
    provider = ScriptProvider()
    result = agent(repository(), provider, definition()).ask("资料" * 2001)
    assert result["error"] == "invalid_question" and provider.calls == []


def test_history_reader_uses_existing_part_and_offset_contract():
    reads = []
    class Historical(ScriptProvider):
        def decide(self, messages, tools, **options):
            if not reads:
                return call("read_conversation_history", {"message_ids": ["old"], "part": "answer", "start_char": 0})
            return call("review_checklist", {})
    def reader(ids, *, part, start_char):
        reads.append((ids, part, start_char))
        return [{"message_id": "old", "text": "旧答复苹果数量999箱"}]
    result = agent(repository(), Historical(), definition(), history_reader=reader).ask("审查并参考旧答复")
    assert result["status"] == "completed"
    assert reads == [(["old"], "answer", 0)]
    assert all(f["value"] != "999" for f in result["payload"]["facts"])


def test_one_schema_repair_records_both_known_calls():
    class Repair(ScriptProvider):
        def extract(self, messages, schema, **options):
            if self.extractions == 0:
                self.extractions += 1
                return call(schema["function"]["name"], {"facts": "malformed"})
            return super().extract(messages, schema, **options)
    provider = Repair()
    result = agent(repository(), provider, definition()).ask("审查")
    assert result["status"] == "completed" and provider.extractions == 3
    assert result["usage"]["model_calls"] == 4
    assert result["usage"]["actual_input"] == 400
    assert any(e.get("structured_repair") for e in result["events"])


def test_repeated_schema_failure_stops_after_one_repair():
    class Invalid(ScriptProvider):
        def extract(self, messages, schema, **options):
            self.extractions += 1
            return call(schema["function"]["name"], {"facts": "malformed"})
    provider = Invalid()
    result = agent(repository(), provider, definition()).ask("审查")
    assert result["status"] == "partial" and result["error"] == "invalid_output"
    assert provider.extractions == 2


def test_source_change_after_provider_call_stops_and_preserves_known_usage():
    repo = repository()
    class Mutate(ScriptProvider):
        def decide(self, messages, tools, **options):
            result = super().decide(messages, tools, **options)
            repo.snapshot_id = "changed"
            return result
    result = agent(repo, Mutate(), definition()).ask("审查")
    assert result["status"] == "partial" and result["error"] == "source_access_changed"
    assert result["usage"]["actual_input"] == 100
    assert result["payload"]["facts"] == []


def test_verified_source_followup_can_reuse_report_and_quote_original():
    repo, provider = repository(), ScriptProvider()
    session = agent(repo, provider, definition())
    assert session.ask("审查")["status"] == "completed"
    refs = [b.evidence_id for b in repo.blocks if b.supplier == "甲供应商" and b.role == "quote"]
    provider.decide = lambda messages, tools, **options: call("read_sources", {"evidence_refs": refs})
    before = provider.extractions
    result = session.ask("甲供应商依据在哪里？")
    assert result["status"] == "completed" and provider.extractions == before
    assert "苹果 数量12箱" in result["answer"] and refs[0] in result["answer"]


def test_conflicting_proposal_candidates_are_kept_needing_resolution():
    class Conflict(ScriptProvider):
        def extract(self, messages, schema, **options):
            reply = super().extract(messages, schema, **options)
            raw = reply.tool_calls[0]["args"]
            duplicate = deepcopy(raw["criteria"][0])
            duplicate["criterion_id"] = "second"
            duplicate["rule"]["value"] = "11"
            raw["criteria"].append(duplicate)
            return call(schema["function"]["name"], raw)
    result = agent(repository(), Conflict(decision="propose_checklist")).ask("提出候选", operation="propose_checklist")
    assert result["status"] == "waiting_user"
    assert len(result["payload"]["definition"]["criteria"]) == 2
    assert len({c["criterion_id"] for c in result["payload"]["definition"]["criteria"]}) == 2
    assert result["payload"]["needs_resolution"]


def test_many_dynamic_metrics_split_contracts_and_scan_every_source_group():
    repo = repository()
    criteria = []
    documents = []
    for n in range(40):
        criterion = definition().model_dump(mode="json")["criteria"][0]
        criterion.update(criterion_id=f"metric-{n}", name=f"指标{n}-数量")
        criterion["source"]["reason"] = "买方明确确认本次水果采购的数量要求" * 6
        criteria.append(criterion)
    for doc in repo.documents:
        if doc.role == "quote":
            texts = [f"苹果 指标{n}-数量12箱" for n in range(40)]
            blocks = tuple(EvidenceBlock(str(n), doc.document_id, text, sheet="资料", cell=f"A{n+1}")
                           for n, text in enumerate(texts))
            parsed = ParseResult(doc.document_id, "script-v1", "complete", blocks,
                                 total_sheets=1, processed_sheets=("资料",))
            doc = Document(doc.document_id, doc.source_hash, doc.role, doc.supplier, None, parsed)
        documents.append(doc)
    repo = EvidenceRepository(repo.case_id, repo.revision, documents)
    rules = ChecklistDefinition.model_validate({"items": [{"item_id": "item-1", "name": "苹果"}],
                                                "criteria": criteria})
    result = agent(repo, ScriptProvider(), rules, mode="thorough").ask("完整审查全部四十项")
    assert result["status"] == "completed"
    assert len(result["payload"]["checks"]) == 80
    assert all(c["business_status"] == "满足" for c in result["payload"]["checks"])
    assert len({tuple(b["criterion_ids"]) for b in result["payload"]["batch_coverage"]}) > 1


def test_previous_payload_read_followup_revalidates_same_report_without_extracting():
    repo, rules = repository(), definition()
    previous = agent(repo, ScriptProvider(), rules).ask("审查")["payload"]
    refs = [b.evidence_id for b in repo.blocks if b.supplier == "甲供应商" and b.role == "quote"]
    provider = ScriptProvider()
    provider.decide = lambda messages, tools, **options: call("read_sources", {"evidence_refs": refs})
    result = agent(repo, provider, rules, previous_payload=previous).ask("依据在哪里？")
    assert result["status"] == "completed" and provider.extractions == 0
    assert refs[0] in result["answer"]


def test_invalid_previous_payload_forces_current_full_review():
    repo, rules = repository(), definition()
    previous = agent(repo, ScriptProvider(), rules).ask("审查")["payload"]
    previous["facts"][0]["value"] = "999"
    provider = ScriptProvider(decision="get_checklist")
    result = agent(repo, provider, rules, previous_payload=previous).ask("清单结论是什么？")
    assert result["status"] == "completed" and provider.extractions == 2
    assert all(f["value"] != "999" for f in result["payload"]["facts"])


def test_manifest_includes_all_planned_batches_when_first_provider_call_fails():
    result = agent(repository(extra=4), ScriptProvider(failure=ProviderError("timeout", outcome_unknown=True)),
                   definition()).ask("审查")
    assert result["status"] == "partial"
    assert len(result["payload"]["batch_manifest"]) > len(result["payload"]["batch_coverage"])
    assert {b["supplier"] for b in result["payload"]["batch_manifest"]} == {"甲供应商", "乙供应商"}
    assert all(b["batch_id"] and b["input_digest"] for b in result["payload"]["batch_manifest"])


def test_shared_review_plan_rebuilds_the_exact_persisted_manifest():
    repo, rules = repository(), definition()
    module = import_module("backend.app.agent.dynamic")
    plans, source_complete = module.review_plan(repo, rules)
    manifest = [entry for supplier in sorted(plans) for _, _, entry in plans[supplier]]
    result = agent(repo, ScriptProvider(), rules).ask("审查")
    assert result["payload"]["batch_manifest"] == manifest
    assert source_complete == {"甲供应商": True, "乙供应商": True}


def test_untyped_provider_exception_has_unknown_outcome_and_is_not_replayed():
    class Broken(ScriptProvider):
        def extract(self, messages, schema, **options):
            self.extractions += 1
            raise ValueError("secret transport body")
    provider = Broken()
    result = agent(repository(), provider, definition()).ask("审查")
    assert result["status"] == "partial" and result["error"] == "provider_error"
    assert provider.extractions == 1 and result["usage"]["unknown_usage_calls"] == 1
    assert "secret" not in json.dumps(result)


def test_public_renderer_and_previous_report_retain_manual_review_notice():
    repo, rules = repository(), definition()
    previous = agent(repo, ScriptProvider(), rules).ask("审查")["payload"]
    previous["needs_review"] = True
    module = import_module("backend.app.agent.dynamic")
    assert "仍需人工复核" in module.render_review(previous["checks"], complete=True, needs_review=True)
    result = agent(repo, ScriptProvider(decision="get_checklist"), rules,
                   previous_payload=previous).ask("告诉我先前结论")
    assert result["status"] == "completed" and "仍需人工复核" in result["answer"]
    assert result["payload"]["needs_review"] is True


def test_proposal_stable_ids_ignore_model_local_ids_and_explicit_defaults():
    class Defaults(ScriptProvider):
        def extract(self, messages, schema, **options):
            reply = super().extract(messages, schema, **options)
            raw = reply.tool_calls[0]["args"]
            raw["items"][0]["item_id"] = "local-new-item"
            raw["items"][0]["specification"] = ""
            criterion = raw["criteria"][0]
            criterion.update(item_id="local-new-item", criterion_id="local-new-criterion",
                             required=True, enabled=True, conditions=[])
            criterion["rule"].update(lower=None, upper=None)
            return call(schema["function"]["name"], raw)
    first = agent(repository(), ScriptProvider(decision="propose_checklist")).ask("提出候选", operation="propose_checklist")
    second = agent(repository(), Defaults(decision="propose_checklist")).ask("提出候选", operation="propose_checklist")
    assert first["payload"]["definition"] == second["payload"]["definition"]


def test_first_decision_failure_preserves_confirmed_definition_and_incomplete_matrix():
    repo, rules = repository(), definition()
    class FailingDecision(ScriptProvider):
        def decide(self, messages, tools, **options):
            raise ProviderError("timeout", outcome_unknown=True)
    result = agent(repo, FailingDecision(), rules).ask("审查")
    assert result["status"] == "partial" and result["error"] == "timeout"
    assert result["payload"]["definition"] == rules.model_dump(mode="json")
    assert result["payload"]["coverage"] == {"甲供应商": False, "乙供应商": False}
    assert len(result["payload"]["checks"]) == 2
    assert all(c["processing_status"] == "partial" and c["business_status"] is None
               for c in result["payload"]["checks"])
    assert result["payload"]["facts"] == []


@pytest.mark.parametrize("code", ["checklist_changed", "batch_source_changed", "dynamic_source_changed"])
def test_dynamic_validation_change_keeps_specific_safe_code(code):
    def changed():
        raise ValueError(code)
    result = agent(repository(), ScriptProvider(), definition(), validate=changed).ask("审查")
    assert result["status"] == "partial" and result["error"] == code


def test_manual_alternative_conversion_failure_cannot_publish_formal_results(monkeypatch):
    from backend.app.criteria.facts import evaluate_checklist, validate_fact
    from backend.tests.test_dynamic_requirements import make_definition, make_repo

    workflow = import_module('backend.app.agent.dynamic')
    repo = make_repo(offers=[('A', '苹果交期18自然日前交付', None)])
    rules = make_definition(name='交期', rule={'type': 'duration', 'operator': 'lte',
        'value': '20', 'calendar': 'natural_days', 'start_event': '交付'})

    class DeliveryStart(ScriptProvider):
        extractions = 0

        def extract(self, messages, schema, **options):
            self.extractions += 1
            block = repo.blocks[-1]
            fact = {'criterion_id': 'weight', 'item_id': 'apple', 'supplier': 'A',
                    'value': '18', 'unit': '自然日', 'calendar': 'natural_days',
                    'start_event': '交付', 'quote': block.text,
                    'evidence_refs': [block.evidence_id]}
            return call(schema['function']['name'], {'facts': [fact]})

    plan = workflow.review_plan
    with monkeypatch.context() as patch:
        patch.setattr(workflow, 'RULE_VERSION', 'dynamic-rules-v1')
        patch.setattr(workflow, 'review_plan', lambda *args, **kwargs: plan(*args, rule_version='dynamic-rules-v1'))
        patch.setattr(workflow, 'validate_fact', lambda *args, **kwargs: validate_fact(*args, rule_version='dynamic-rules-v1'))
        patch.setattr(workflow, 'evaluate_checklist', lambda *args, **kwargs: evaluate_checklist(*args, **(kwargs | {'rule_version': 'dynamic-rules-v1'})))
        previous = agent(repo, DeliveryStart(), rules).ask('审查')['payload']
    assert previous['facts'][0]['start_event'] == '交付'
    previous['needs_review'] = True
    provider = DeliveryStart(decision='review_checklist')
    result = agent(repo, provider, rules, previous_payload=previous).ask('重新完整审查')
    assert result['status'] == 'partial'
    assert result['error'] == 'manual_alternative_revalidation_required'
    assert result['payload']['facts'] == []
    assert result['payload']['checks']
    assert all(not check['is_formal'] for check in result['payload']['checks'])
    assert provider.extractions == 0


@pytest.mark.parametrize('legacy', [False, True])
def test_full_review_keeps_revalidated_manual_alternatives_and_reviews_all_batches(monkeypatch, legacy):
    from backend.app.criteria.facts import evaluate_checklist, validate_fact

    original, rules, documents, saved = repository(), definition(), [], {}
    for doc in original.documents:
        if doc.supplier == "甲供应商" and doc.role == "quote":
            parsed = ParseResult(doc.document_id, "script-v1", "complete",
                                 (EvidenceBlock("0", doc.document_id, "苹果 数量12箱或8箱", sheet="资料", cell="A1"),),
                                 total_sheets=1, processed_sheets=("资料",))
            doc = Document(doc.document_id, doc.source_hash, doc.role, doc.supplier, None, parsed)
        documents.append(doc)
    repo = EvidenceRepository(original.case_id, original.revision, documents)
    class OneValue(ScriptProvider):
        def extract(self, messages, schema, **options):
            reply = super().extract(messages, schema, **options)
            data = json.loads(messages[-1].content)
            quote_refs = {b["evidence_id"] for b in data["evidence"] if b["role"] == "quote"}
            reply.tool_calls[0]["args"]["facts"] = [f for f in reply.tool_calls[0]["args"]["facts"]
                                                    if set(f["evidence_refs"]) <= quote_refs]
            for fact in reply.tool_calls[0]["args"]["facts"]:
                fact["unit"] = "箱"
            return reply
    def load(batch_id, input_digest):
        return deepcopy(saved.get((batch_id, input_digest)))
    def save(batch_id, input_digest, output):
        saved[(batch_id, input_digest)] = deepcopy(output)
    workflow = import_module('backend.app.agent.dynamic')
    plan = workflow.review_plan
    with monkeypatch.context() as patch:
        if legacy:
            patch.setattr(workflow, 'RULE_VERSION', 'dynamic-rules-v1')
            patch.setattr(workflow, 'review_plan', lambda *args, **kwargs: plan(*args, rule_version='dynamic-rules-v1'))
            patch.setattr(workflow, 'validate_fact', lambda *args, **kwargs: validate_fact(*args, rule_version='dynamic-rules-v1'))
            patch.setattr(workflow, 'evaluate_checklist', lambda *args, **kwargs: evaluate_checklist(*args, **(kwargs | {'rule_version': 'dynamic-rules-v1'})))
        previous = agent(repo, OneValue(), rules, cache_load=load, cache_save=save).ask("审查")["payload"]
    source = next(b for b in repo.blocks if b.supplier == "甲供应商" and b.role == "quote")
    alternative = validate_fact({"criterion_id": "metric-1", "item_id": "item-1", "supplier": "甲供应商",
                                 "value": "8", "unit": "箱", "quote": source.text,
                                 "evidence_refs": [source.evidence_id]}, rules, repo, "甲供应商")
    previous["facts"].append(alternative)
    previous["needs_review"] = True
    previous["checks"] = evaluate_checklist(rules, previous["facts"], previous["coverage"], snapshot_id=repo.snapshot_id,
                                           rule_version=previous['rule_version'])
    provider = OneValue(decision="review_checklist")
    result = agent(repo, provider, rules, previous_payload=previous, cache_load=load).ask("重新完整审查")
    assert result["status"] == "completed"
    assert result["payload"].get("needs_review") is True
    assert {f["value"] for f in result["payload"]["facts"] if f["supplier"] == "甲供应商"} == {"12", "8"}
    assert next(c for c in result["payload"]["checks"] if c["supplier"] == "甲供应商")["business_status"] == "资料冲突"
    assert "仍需人工复核" in result["answer"]
    assert len(result["payload"]["batch_coverage"]) == len(result["payload"]["batch_manifest"]) == 2
    assert all(b["complete"] and b["cached"] is (not legacy) for b in result["payload"]["batch_coverage"])


def test_user_defined_checklist_completes_without_an_extra_buyer_document():
    original = repository()
    repo = EvidenceRepository(original.case_id, original.revision,
                              [d for d in original.documents if d.role != "requirement"])
    result = agent(repo, ScriptProvider(), definition()).ask("完整审查手工清单")
    assert result["status"] == "completed"
    assert all(result["payload"]["coverage"].values())
    assert len(result["payload"]["checks"]) == 2
    assert {c["business_status"] for c in result["payload"]["checks"]} == {"满足", "不满足"}


@pytest.mark.parametrize("source_kind", ["user_defined", "document_extracted"])
def test_definition_referencing_missing_buyer_source_stays_incomplete(source_kind):
    original, raw = repository(), definition().model_dump(mode="json")
    buyer = next(b for b in original.blocks if b.role == "requirement")
    source = {"kind": source_kind, "context": {"evidence_refs": [buyer.evidence_id], "quote": buyer.text}}
    if source_kind == "user_defined":
        source["reason"] = "买方明确确认并引用需求原文"
    raw["criteria"][0]["source"] = source
    repo = EvidenceRepository(original.case_id, original.revision,
                              [d for d in original.documents if d.role != "requirement"])
    result = agent(repo, ScriptProvider(), raw).ask("审查")
    assert result["status"] == "partial"
    assert not any(result["payload"]["coverage"].values())


def test_provided_partial_buyer_document_conservatively_keeps_manual_review_partial():
    original, documents = repository(), []
    for doc in original.documents:
        if doc.role == "requirement":
            parsed = ParseResult(doc.document_id, doc.parsed.parser_version, "partial", doc.parsed.blocks,
                                 total_sheets=1, processed_sheets=("资料",), coverage_issues=("unreadable",))
            doc = Document(doc.document_id, doc.source_hash, doc.role, doc.supplier, None, parsed)
        documents.append(doc)
    repo = EvidenceRepository(original.case_id, original.revision, documents)
    result = agent(repo, ScriptProvider(), definition()).ask("审查")
    assert result["status"] == "partial"
    assert not any(result["payload"]["coverage"].values())


@pytest.mark.parametrize("metric,suffix,rule", [
    ("数量", "不低于", {"type": "numeric", "operator": "gte", "value": "10", "unit": "箱"}),
    ("净重", "至少", {"type": "numeric", "operator": "gte", "value": "10", "unit": "kg"}),
    ("含税单价", "不高于", {"type": "money", "operator": "lte", "value": "10", "currency": "CNY",
                          "per_unit": "箱", "tax_basis": "included"}),
    ("交付期限", "不超过", {"type": "duration", "operator": "lte", "value": "10",
                          "calendar": "natural_days", "start_event": "订单确认"}),
    ("风味强度", "≥", {"type": "numeric", "operator": "gte", "value": "10", "unit": "级"}),
])
def test_proposal_metric_names_strip_comparison_suffix_and_keep_quote_rule_and_stable_id(metric, suffix, rule):
    original, documents = repository(), []
    unit = rule.get("unit", "元/箱" if rule["type"] == "money" else "自然日")
    quote = f"苹果 {'订单确认后' if rule['type'] == 'duration' else ''}{metric}{suffix}10{unit}"
    for doc in original.documents:
        if doc.role == "requirement":
            parsed = ParseResult(doc.document_id, doc.parsed.parser_version, "complete",
                                 (EvidenceBlock("0", doc.document_id, quote, sheet="资料", cell="A1"),),
                                 total_sheets=1, processed_sheets=("资料",))
            doc = Document(doc.document_id, doc.source_hash, doc.role, None, None, parsed)
        documents.append(doc)
    repo = EvidenceRepository(original.case_id, original.revision, documents)
    class Naming(ScriptProvider):
        def __init__(self, metric_name):
            super().__init__(decision="propose_checklist")
            self.metric_name = metric_name
        def extract(self, messages, schema, **options):
            block = json.loads(messages[-1].content)["evidence"][0]
            return call(schema["function"]["name"], {
                "items": [{"item_id": "local-item", "name": "苹果"}],
                "criteria": [{"criterion_id": "local-criterion", "item_id": "local-item", "name": self.metric_name,
                              "rule": rule, "source": {"kind": "document_extracted", "context": {
                                  "evidence_refs": [block["evidence_id"]], "quote": block["text"]}}}],
            })
    suffixed = agent(repo, Naming(metric + suffix)).ask("提出候选", operation="propose_checklist")
    canonical = agent(repo, Naming(metric)).ask("提出候选", operation="propose_checklist")
    assert suffixed["status"] == canonical["status"] == "completed"
    candidate = suffixed["payload"]["definition"]["criteria"][0]
    assert candidate["name"] == metric
    assert candidate["source"]["context"]["quote"] == quote
    assert {k: candidate["rule"][k] for k in rule} == rule
    assert suffixed["payload"]["definition"] == canonical["payload"]["definition"]


@pytest.mark.parametrize("rule", [
    {"type": "enum", "operator": "eq", "values": ["一级"]},
    {"type": "presence", "description": "材料"},
    {"type": "manual", "instruction": "人工核对条款"},
])
def test_comparison_name_normalization_is_limited_to_quantitative_document_candidates(rule):
    module = import_module("backend.app.agent.dynamic")
    raw = {"items": [{"item_id": "local-item", "name": "苹果"}],
           "criteria": [{"criterion_id": "local-criterion", "item_id": "local-item", "name": "属性不低于",
                         "rule": rule, "source": {"kind": "document_extracted", "context": {
                             "evidence_refs": ["e" * 64], "quote": "苹果 属性不低于 原文待确认"}}}]}
    assert module._canonical_proposal(raw).criteria[0].name == "属性不低于"


def test_comparison_name_normalization_never_creates_an_empty_attribute():
    module = import_module("backend.app.agent.dynamic")
    raw = {"items": [{"item_id": "local-item", "name": "苹果"}],
           "criteria": [{"criterion_id": "local-criterion", "item_id": "local-item", "name": "不低于",
                         "rule": {"type": "numeric", "operator": "gte", "value": "10", "unit": "箱"},
                         "source": {"kind": "document_extracted", "context": {
                             "evidence_refs": ["e" * 64], "quote": "苹果 不低于10箱"}}}]}
    assert module._canonical_proposal(raw).criteria[0].name == "不低于"


def test_normalized_document_candidate_matches_supplier_property_after_confirmation():
    class Suffixed(ScriptProvider):
        def extract(self, messages, schema, **options):
            reply = super().extract(messages, schema, **options)
            if "supplier" not in json.loads(messages[-1].content):
                reply.tool_calls[0]["args"]["criteria"][0]["name"] += "不低于"
            return reply
    repo = repository()
    proposed = agent(repo, Suffixed(decision="propose_checklist")).ask("提出候选", operation="propose_checklist")
    result = agent(repo, ScriptProvider(), proposed["payload"]["definition"]).ask("审查已确认候选")
    assert result["status"] == "completed"
    assert {c["business_status"] for c in result["payload"]["checks"]} == {"满足", "不满足"}


def test_complete_review_and_get_checklist_followup_render_real_source_locations():
    original = repository()
    source_doc = next(d.document_id for d in original.documents if d.supplier == "甲供应商" and d.role == "quote")
    repo = EvidenceRepository(original.case_id, original.revision, list(original.documents),
                              document_names={source_doc: "苹果采购报价原件.xlsx"})
    first = agent(repo, ScriptProvider(), definition()).ask("审查并说明来源")
    quote = next(b for b in repo.blocks if b.supplier == "甲供应商" and b.role == "quote")
    for result in [first, agent(repo, ScriptProvider(decision="get_checklist"), definition(),
                               previous_payload=first["payload"]).ask("说明先前结果来源")]:
        assert result["status"] == "completed"
        assert "苹果采购报价原件.xlsx" in result["answer"] and "资料!A1" in result["answer"]
        assert quote.text in result["answer"] and quote.evidence_id in result["answer"]


def test_source_renderer_pdf_pages_and_same_named_item_labels_are_explicit():
    module = import_module("backend.app.agent.dynamic")
    block = EvidenceBlock("page", "quote.pdf", "苹果 净重12kg", page_idx=1)
    parsed = ParseResult("quote.pdf", "script-v1", "complete", (block,), total_pages=2,
                         processed_pages=(0, 1))
    repo = EvidenceRepository("case", 1, [Document("quote.pdf", digest("quote.pdf"), "quote", "甲供应商", None, parsed)],
                              document_names={"quote.pdf": "采购规格原件.pdf"})
    source = repo.blocks[0]
    checks = [{"supplier": "甲供应商", "item_name": item, "criterion_name": "净重",
               "processing_status": "complete", "business_status": "满足", "evidence_refs": [source.evidence_id],
               "reason_evidence_refs": [source.evidence_id]} for item in ("苹果", "梨")]
    answer = module.render_review(checks, complete=True, repo=repo)
    assert "甲供应商 · 苹果 · 净重" in answer and "甲供应商 · 梨 · 净重" in answer
    assert "采购规格原件.pdf" in answer and "第 2 页" in answer
    assert answer.count(source.text) == 1 and answer.count(source.evidence_id) == 1


def test_source_renderer_limits_quotes_and_total_section_with_explicit_api_notice():
    module = import_module("backend.app.agent.dynamic")
    repo = repository(extra=40)
    refs = [b.evidence_id for b in repo.blocks if b.supplier == "甲供应商"]
    checks = [{"supplier": "甲供应商", "item_name": "苹果", "criterion_name": "数量",
               "processing_status": "partial", "business_status": None,
               "evidence_refs": refs, "reason_evidence_refs": refs}]
    answer = module.render_review(checks, complete=False, repo=repo)
    section = answer.split("核验来源：\n", 1)[1]
    assert len(section) <= 16000
    assert "600" in section and "其余来源见证据API" in section


def test_first_decision_has_current_scoped_source_handles():
    repo, provider = repository(), ScriptProvider()
    result = agent(repo, provider, definition()).ask("审查并说明来源")
    assert result["status"] == "completed"
    first_messages = provider.calls[0][1]
    quote = next(b for b in repo.blocks if b.supplier == "甲供应商" and b.role == "quote")
    assert quote.evidence_id in first_messages[0].content


def test_source_guard_runs_after_rendering_originals():
    repo = repository()
    read_original = repo.read
    quote_refs = {b.evidence_id for b in repo.blocks if b.role == "quote"}
    def read(refs):
        blocks = read_original(refs)
        if set(refs) == quote_refs:
            repo.snapshot_id = "changed-during-render"
        return blocks
    repo.read = read
    result = agent(repo, ScriptProvider(), definition()).ask("审查并说明来源")
    assert result["status"] == "partial" and result["error"] == "source_access_changed"


def test_read_sources_cites_requested_source_once_and_keeps_review_matrix():
    repo, rules = repository(), definition()
    previous = agent(repo, ScriptProvider(), rules).ask("审查")["payload"]
    source = next(b for b in repo.blocks if b.supplier == "甲供应商" and b.role == "quote")
    provider = ScriptProvider()
    provider.decide = lambda messages, tools, **options: call("read_sources", {"evidence_refs": [source.evidence_id]})
    result = agent(repo, provider, rules, previous_payload=previous).ask("读取该出处")
    assert len(result["payload"]["checks"]) == 2
    assert result["answer"].count(source.evidence_id) == 1
    assert result["answer"].count(source.text) == 1


def test_read_sources_without_old_report_reviews_and_cites_requested_buyer_source():
    repo, rules, provider = repository(), definition(), ScriptProvider()
    source = next(b for b in repo.blocks if b.role == "requirement")
    provider.decide = lambda messages, tools, **options: call("read_sources", {"evidence_refs": [source.evidence_id]})
    result = agent(repo, provider, rules).ask("读取采购需求依据")
    assert result["status"] == "completed" and len(result["payload"]["checks"]) == 2
    assert source.evidence_id in result["answer"] and source.text in result["answer"]
