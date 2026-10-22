"""Request repacking never changes the immutable parent coverage contract."""

import json
from copy import deepcopy

import pytest

from backend.app.agent.context_batches import repack_review_request
from backend.app.agent.policy import Budget, RunStopped
from backend.app.criteria.models import ChecklistDefinition
from backend.app.evidence.models import Document
from backend.app.evidence.repository import EvidenceRepository, digest
from backend.app.parsing import EvidenceBlock, ParseResult
from backend.app.providers.base import ProviderError
from backend.tests.test_dynamic_agent import (
    ScriptProvider,
    agent,
    call,
    definition,
    repository,
)


def data(*, criteria=2, rows=2):
    return {"supplier": "甲", "items": [{"item_id": "apple", "name": "苹果"}],
            "criteria": [{"criterion_id": str(n), "rule": {"type": "numeric"}}
                         for n in range(criteria)],
            "evidence": [{"evidence_id": f"{row}-{col}", "document_id": "d",
                          "sheet": "报价", "cell": f"{col}{row}", "text": "苹果数量"}
                         for row in range(1, rows + 1) for col in ("A", "B")]}


def test_full_request_repacking_covers_criterion_source_matrix_without_mutation():
    parent = data()
    frozen = deepcopy(parent)
    parts = repack_review_request(parent, lambda p: len(p["criteria"]) == 1 and len(p["evidence"]) == 2)
    assert parent == frozen
    assert len(parts) == 4
    expected = {(c["criterion_id"], b["evidence_id"])
                for c in parent["criteria"] for b in parent["evidence"]}
    actual = [(c["criterion_id"], b["evidence_id"])
              for p in parts for c in p["criteria"] for b in p["evidence"]]
    assert set(actual) == expected and len(actual) == len(expected)
    for p in parts:
        assert p["items"] == parent["items"]
        assert {b["cell"][1:] for b in p["evidence"]} in ({"1"}, {"2"})


def test_three_split_limit_cannot_turn_into_unbounded_recursive_retries():
    probes = []
    with pytest.raises(RunStopped, match="context_budget"):
        repack_review_request(data(criteria=8), lambda p: probes.append(p) or len(p["criteria"]) == 1)
    assert len(probes) <= 10


@pytest.mark.parametrize("kind", ["presence", "manual"])
def test_material_or_manual_rule_keeps_cross_block_context(kind):
    parent = data(criteria=1)
    parent["criteria"][0]["rule"]["type"] = kind
    with pytest.raises(RunStopped, match="context_budget"):
        repack_review_request(parent, lambda p: len(p["evidence"]) <= 2)


def test_single_source_row_or_paragraph_is_never_cut():
    for parent in (data(criteria=1, rows=1),
                   {**data(criteria=1), "evidence": [{"evidence_id": "p", "text": "长段落"}]}):
        with pytest.raises(RunStopped, match="context_budget"):
            repack_review_request(parent, lambda _: False)


def test_already_fitting_request_is_reused_without_splits():
    parent = data()
    assert repack_review_request(parent, lambda _: True) == [parent]


def request_repo():
    blocks = tuple(EvidenceBlock(str(n), "quote.xlsx", text, sheet="报价", cell=f"A{n+1}")
                   for n, text in enumerate(["苹果 数量12箱"] + ["苹果 其他说明 " + "备注" * 100] * 5))
    parsed = ParseResult("quote.xlsx", "script-v1", "complete", blocks,
                         total_sheets=1, processed_sheets=("报价",))
    doc = Document("quote.xlsx", digest("quote.xlsx"), "quote", "甲供应商", None, parsed)
    return EvidenceRepository("dynamic-case", 1, [doc])


def test_agent_repackages_full_requests_and_keeps_parent_manifest_and_cache():
    from backend.app.agent.dynamic import review_plan

    repo, rules, provider = request_repo(), definition(), ScriptProvider()
    cached = {}
    original_manifest = [m for group in review_plan(repo, rules)[0].values() for _, _, m in group]
    result = agent(repo, provider, rules, budget=Budget(max_prompt=14000),
                   cache_save=lambda batch, fingerprint, raw: cached.update({batch: (fingerprint, raw)})).ask("完整审查")
    assert result["status"] == "completed"
    assert provider.extractions > 1
    assert result["payload"]["batch_manifest"] == original_manifest
    assert len(cached) == len(original_manifest) == 1
    coverage = result["payload"]["batch_coverage"][0]
    assert coverage["request_parts"] == coverage["completed_parts"] > 1
    assert coverage["complete"]
    assert len(result["payload"]["facts"]) == 1
    seen = [b["evidence_id"] for kind, messages, _ in provider.calls if kind == "extract"
            for b in json.loads(messages[-1].content)["evidence"]]
    assert sorted(seen) == sorted(b.evidence_id for b in repo.blocks)


def test_unsplittable_rule_stops_before_extraction_with_readable_explanation():
    rules = definition().model_dump(mode="json")
    rules["criteria"][0]["rule"] = {"type": "manual", "instruction": "需核验" * 30}
    provider = ScriptProvider()
    result = agent(request_repo(), provider, ChecklistDefinition.model_validate(rules),
                   budget=Budget(max_prompt=14000)).ask("完整审查")
    assert result["status"] == "partial"
    assert result["error"] == "context_budget"
    assert provider.extractions == 0
    assert "context_budget" not in result["answer"]
    assert "已完成" in result["answer"] and "未完成" in result["answer"]
    assert not any(result["payload"]["coverage"].values())
    rejected = [e for e in result["events"] if e.get("error") == "context_budget"]
    assert rejected[-1]["provider_called"] is False
    assert rejected[-1]["context"]["legacy_input_estimate"] > rejected[-1]["context"]["legacy_prompt_limit"]


def test_split_request_has_room_for_both_format_and_semantic_correction():
    class Repairing(ScriptProvider):
        def extract(self, messages, schema, **options):
            self.invalid = not any("上次事实" in str(m.content) for m in messages)
            first = self.extractions == 0
            response = super().extract(messages, schema, **options)
            return call(schema["function"]["name"], {"wrong": True}) if first else response

    provider = Repairing()
    result = agent(request_repo(), provider, definition(), budget=Budget(max_prompt=14000)).ask("完整审查")
    assert result["status"] == "completed"
    assert any(e.get("structured_repair") for e in result["events"])
    assert any(e.get("semantic_repair") for e in result["events"])
    assert all(e["context"]["legacy_input_estimate"] <= e["context"]["legacy_prompt_limit"]
               for e in result["events"] if e["kind"] == "model")
    assert len(result["payload"]["facts"]) == 1


@pytest.mark.parametrize("stop", ["unknown", "calls", "input", "output"])
def test_partial_parts_keep_verified_facts_without_saving_complete_cache(stop):
    budget = Budget(max_prompt=14000, max_calls=2 if stop == "calls" else 16)
    saved = []

    class StopAfterFirst(ScriptProvider):
        def extract(self, messages, schema, **options):
            if self.extractions:
                self.extractions += 1
                raise OSError("private transport detail")
            response = super().extract(messages, schema, **options)
            if stop == "input":
                budget.max_input = 101
            if stop == "output":
                budget.max_output = 51
            return response

    provider = StopAfterFirst()
    result = agent(request_repo(), provider, definition(), budget=budget,
                   cache_save=lambda *args: saved.append(args)).ask("完整审查")
    assert result["status"] == "partial"
    assert result["error"] == {"unknown": "provider_error", "calls": "model_budget",
                               "input": "input_budget", "output": "output_budget"}[stop]
    assert provider.extractions == (2 if stop == "unknown" else 1)
    assert saved == []
    assert not any(result["payload"]["coverage"].values())
    assert result["payload"]["batch_coverage"][0]["complete"] is False
    # input/output are exhausted by record(), before this response is validated.
    if stop in {"unknown", "calls"}:
        assert len(result["payload"]["facts"]) == 1
        assert result["payload"]["batch_coverage"][0]["completed_parts"] == 1
    if stop == "unknown":
        assert result["usage"]["unknown_usage_calls"] == 1
        assert any(e.get("outcome_unknown") for e in result["events"])
        assert "private transport" not in str(result)


def test_child_cannot_reference_evidence_from_another_child():
    repo = request_repo()
    source = repo.blocks[0]

    class Escaping(ScriptProvider):
        def extract(self, messages, schema, **options):
            response = super().extract(messages, schema, **options)
            if not response.tool_calls[0]["args"]["facts"]:
                response = call(schema["function"]["name"], {"facts": [{
                    "criterion_id": "metric-1", "item_id": "item-1", "supplier": "甲供应商",
                    "value": "12", "unit": "箱", "evidence_refs": [source.evidence_id], "quote": source.text}]})
            return response

    saved = []
    result = agent(repo, Escaping(), definition(), budget=Budget(max_prompt=14000),
                   cache_save=lambda *args: saved.append(args)).ask("完整审查")
    assert result["status"] == "partial" and result["error"] == "coverage_incomplete"
    progress = result["payload"]["batch_coverage"][0]
    assert progress["rejection_codes"] == {"candidate_scope": progress["request_parts"] - 1}
    assert saved == [] and len(result["payload"]["facts"]) == 1


def test_known_good_fact_survives_unknown_semantic_repair():
    class FailsRepair(ScriptProvider):
        def extract(self, messages, schema, **options):
            if self.extractions:
                self.extractions += 1
                raise OSError("unknown")
            response = super().extract(messages, schema, **options)
            candidate = deepcopy(response.tool_calls[0]["args"]["facts"][0])
            candidate["value"] = "999"
            response.tool_calls[0]["args"]["facts"].append(candidate)
            return response

    result = agent(request_repo(), FailsRepair(), definition(), budget=Budget(max_prompt=14000)).ask("完整审查")
    assert result["status"] == "partial" and result["error"] == "provider_error"
    assert len(result["payload"]["facts"]) == 1
    assert not any(result["payload"]["coverage"].values())


def test_completed_parent_cache_survives_failure_and_is_reused():
    class FailsSecondParent(ScriptProvider):
        def extract(self, messages, schema, **options):
            if self.extractions:
                raise ProviderError("unavailable")
            return super().extract(messages, schema, **options)

    repo, rules, cache = repository(), definition(), {}
    first = agent(repo, FailsSecondParent(), rules,
                  cache_save=lambda b, fingerprint, raw: cache.update({b: (fingerprint, raw)})).ask("完整审查")
    assert first["status"] == "partial" and len(cache) == 1
    provider = ScriptProvider()
    second = agent(repo, provider, rules,
                   cache_load=lambda b, fingerprint: cache[b][1] if b in cache and cache[b][0] == fingerprint else None).ask("完整审查")
    assert second["status"] == "completed" and provider.extractions == 1
    assert second["payload"]["batch_manifest"] == first["payload"]["batch_manifest"]
    assert any(p["cached"] for p in second["payload"]["batch_coverage"])


def test_repair_preflight_covers_feedback_with_json_escape_expansion():
    from langchain_core.messages import HumanMessage, SystemMessage

    from backend.app.agent.dynamic import (
        FORMAT_REPAIR,
        _candidate_repair_feedback,
        _repair_envelope,
        _semantic_feedback,
    )
    from backend.app.agent.policy import input_bound

    original = [SystemMessage(content="原文抽取"), HumanMessage(content='{"evidence":[]}')]
    for value in ('"\\' * 400, "\x00\t\r\n" * 100, "中文😀" * 150):
        feedback = _candidate_repair_feedback([{"error_code": "fact_value", "value": value}])
        assert len(feedback.encode("utf-8")) <= 2048
        actual = [original[0], SystemMessage(content=FORMAT_REPAIR),
                  _semantic_feedback({"fact_value": 128}, True), HumanMessage(content=feedback), original[1]]
        assert input_bound(actual, []) <= input_bound(_repair_envelope(original, True), [])


def test_repacking_trace_exposes_only_bounded_numbers():
    from backend.app.agent.diagnostics import safe_trace_events

    result = agent(request_repo(), ScriptProvider(), definition(), budget=Budget(max_prompt=14000)).ask("完整审查")
    traces = safe_trace_events(result["events"])
    progress = next(e["summary"] for e in traces if e["kind"] == "agent_tool"
                    and "context_repack_count" in e["summary"])
    assert 1 < progress["request_parts"] <= 4
    assert progress["completed_parts"] == progress["request_parts"]
    assert progress["context_repack_count"] == progress["request_parts"] - 1
    assert "其他说明" not in str(traces)
