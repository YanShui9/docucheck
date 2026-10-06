"""Persist only bounded, snapshot-scoped server results between Agent workers."""

import json
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from backend.app.agent.graph import AgentSession
from backend.app.agent.session_memory import restore_session, serialize_session
from backend.app.evidence.repository import digest, load_case
from backend.app.extraction.fields import FieldCandidate, _validate_candidate
from backend.app.field_contract import FieldFactSet, FieldKey
from backend.app.rules.fields import (
    _evaluate_field_v1,
    _evaluate_field_v2,
    _evaluate_field_v3,
)
from evals.m2_agent import ScriptedProvider, _call

PACK = Path(__file__).resolve().parents[2] / "examples" / "dev-pack-001"
THREE_PACK = Path(__file__).resolve().parents[2] / "examples" / "dev-pack-003"


def _session():
    return AgentSession(load_case(PACK), object(), conversation_id="conversation-1")


def test_verified_result_and_focus_survive_new_session():
    first = _session()
    provider = ScriptedProvider(("vendor-a",))
    extracted = first.tools.execute("extract_facts", {
        "supplier": "vendor-a", "field_keys": ["memory"],
    }, lambda kind, messages, schema: provider.extract(messages, schema))
    checked = first.tools.execute("evaluate_checks", {
        "supplier": "vendor-a", "fact_set_id": extracted["data"]["fact_set_id"],
    }, None)
    result = first.tools.results[checked["data"]["check_result_id"]]
    first.focus = {"supplier": "vendor-a", "field_key": "memory"}
    first.history = [(HumanMessage(content="内存？"),
                      AIMessage(content="已核验。"))]
    memory = serialize_session(first, "已核验。")
    assert memory["recent_turns"] == [{"question": "内存？", "answer": "已核验。"}]

    restored = _session()
    restore_session(restored, memory,
                    [(HumanMessage(content="内存？"), AIMessage(content="已核验。"))])
    assert restored.focus == first.focus
    assert restored.history[0][0].content == "内存？"
    assert restored.tools.results[result.check_result_id] == result
    assert restored.tools.execute("get_check_results",
                                  {"supplier": "vendor-a"}, None)["evidence_refs"] == list(
                                      result.evidence_refs)


def _reviewed_session():
    session = AgentSession(load_case(PACK), ScriptedProvider(("vendor-a",)),
                           mode="thorough", conversation_id="conversation-1")
    run = session.ask("请完整审查供应商 A 的八项指标")
    assert run.status == "completed"
    return session, serialize_session(session, run.answer)


@pytest.mark.parametrize(("key", "value"), [
    ("business_status", "满足"), ("reason_code", "threshold_met"),
    ("fact_ids", ["foreign-fact"]), ("check_result_id", "modified-result"),
    ("processing_status", "partial"),
])
def test_restored_result_must_match_recalculation_from_verified_facts(key, value):
    _, saved = _reviewed_session()
    memory = next(item for item in saved["check_results"] if item["field_key"] == "memory")
    memory[key] = value
    restored = _session()
    with pytest.raises(ValueError, match="memory_scope"):
        restore_session(restored, saved, [])
    assert restored.tools.results == {}
    assert restored.history == []


@pytest.mark.parametrize("version", [1, 2])
def test_memory_without_verifiable_facts_keeps_context_but_drops_reusable_results(version):
    first, saved = _reviewed_session()
    saved["version"] = version
    saved["fact_sets_available"] = False
    saved["fact_sets"] = []
    restored = _session()
    restore_session(restored, saved, [])
    assert restored.history[0][0].content == first.history[0][0].content
    assert restored.focus == first.focus
    assert restored.tools.results == {}
    assert restored.tools.seen == set()


def test_available_fact_memory_cannot_omit_a_results_fact_set():
    _, saved = _reviewed_session()
    saved["fact_sets"] = [item for item in saved["fact_sets"]
                          if item["field_key"] != "memory"]
    with pytest.raises(ValueError, match="memory_scope"):
        restore_session(_session(), saved, [])


def test_serialization_drops_old_result_when_latest_fact_set_has_not_been_evaluated():
    first, _ = _reviewed_session()
    provider = ScriptedProvider(("vendor-a",))
    first.tools.execute("extract_facts", {
        "supplier": "vendor-a", "field_keys": ["memory"],
    }, lambda kind, messages, schema: provider.extract(messages, schema))
    saved = serialize_session(first, "取得更新的事实。")
    assert len(saved["check_results"]) == 7
    restored = _session()
    restore_session(restored, saved, [])
    assert all(item.field_key != "memory" for item in restored.tools.results.values())


def test_modified_snapshot_or_evidence_cannot_be_restored():
    session, memory = _reviewed_session()
    refs = memory["check_results"][0]["evidence_refs"]
    memory["check_results"][0]["evidence_refs"] = ["foreign-evidence"]
    with pytest.raises(ValueError, match="memory_scope"):
        restore_session(_session(), memory, [])
    memory["check_results"][0]["evidence_refs"] = refs
    memory["snapshot_id"] = "foreign-snapshot"
    with pytest.raises(ValueError, match="memory_scope"):
        restore_session(_session(), memory, [])
    memory["snapshot_id"] = session.repo.snapshot_id
    memory["repository_fingerprint"] = "changed-after-parsing"
    with pytest.raises(ValueError, match="memory_scope"):
        restore_session(_session(), memory, [])


def test_verified_fact_set_survives_new_session_and_version_one_still_reads():
    first = _session()
    block = next(block for block in first.repo.blocks
                 if block.supplier == "vendor-a" and "1 TB" in block.text)
    fact = _validate_candidate(FieldCandidate(
        field_key=FieldKey.STORAGE, raw_value="1", raw_unit="TB",
        evidence_refs=[block.evidence_id],
    ), first.repo, "vendor-a")
    scanned = (block.evidence_id,)
    identity = digest([first.repo.snapshot_id, "vendor-a", "storage", scanned,
                       False, [asdict(fact) | {"value": str(fact.value)}]])
    fact_set = FieldFactSet(
        fact_set_id=identity, snapshot_id=first.repo.snapshot_id,
        supplier="vendor-a", field_key=FieldKey.STORAGE, facts=(fact,),
        scanned_ids=scanned, complete=False, coverage_issues=("example",),
    )
    first.tools.fact_sets[identity] = fact_set
    saved = serialize_session(first, "已核验。")
    assert saved["version"] == 3
    assert saved["fact_sets_available"] is True
    restored = _session()
    restore_session(restored, saved, [])
    assert restored.tools.fact_sets[identity] == fact_set

    old = {key: value for key, value in saved.items()
           if key not in {"fact_sets", "fact_sets_available"}}
    old["version"] = 1
    historical = _session()
    restore_session(historical, old, [])
    assert historical.tools.fact_sets == {}

    saved["fact_sets"][0]["facts"][0]["raw_value"] = "2"
    rejected = _session()
    with pytest.raises(ValueError, match="memory_scope"):
        restore_session(rejected, saved, [])
    assert rejected.tools.fact_sets == {}
    assert rejected.tools.results == {}


def test_full_review_memory_field_result_survives_session_restore():
    first = AgentSession(load_case(PACK), ScriptedProvider(("vendor-a",)),
                         mode="thorough", conversation_id="conversation-1")
    run = first.ask("请完整审查供应商 A 的八项指标")
    assert run.status == "completed"
    saved = serialize_session(first, run.answer)
    assert len(saved["check_results"]) == 8
    assert next(result for result in saved["check_results"]
                if result["field_key"] == "memory")["rule_version"] == "fields-v4"

    restored = _session()
    restore_session(restored, saved, [])
    assert len(restored.tools.results) == 8


@pytest.mark.parametrize("old_rule_version", ["fields-v1", "fields-v2", "fields-v3"])
def test_old_rule_results_cannot_be_reused_as_current_after_version_change(old_rule_version):
    _, saved = _reviewed_session()
    for result in saved["check_results"]:
        result["rule_version"] = old_rule_version
    restored = _session()
    with pytest.raises(ValueError, match="memory_scope"):
        restore_session(restored, saved, [])
    assert restored.tools.results == {}
    assert restored.tools.fact_sets == {}


@pytest.mark.parametrize("evaluator", [_evaluate_field_v1, _evaluate_field_v2, _evaluate_field_v3])
def test_real_archived_results_are_verified_then_recomputed_for_current_session(evaluator):
    first, saved = _reviewed_session()
    expected = deepcopy(saved)
    saved["check_results"] = []
    for fact_set in first.tools.fact_sets.values():
        result = evaluator(first.repo, fact_set.supplier, fact_set.field_key,
                           fact_set.facts, scan_complete=fact_set.complete)
        saved["check_results"].append({**asdict(result),
            "evidence_refs": list(result.evidence_refs),
            "reason_evidence_refs": list(result.reason_evidence_refs),
            "fact_ids": list(result.fact_ids)})
    archived = deepcopy(saved)
    restored = _session()
    restore_session(restored, saved, [])
    current = serialize_session(restored, expected["last_answer"])
    assert {row["check_result_id"] for row in current["check_results"]} == {
        row["check_result_id"] for row in expected["check_results"]}
    assert all(row["rule_version"] == "fields-v4" for row in current["check_results"])
    assert current["fact_sets"] == expected["fact_sets"]
    assert saved == archived  # replay must not relabel the archived outcome


def test_standard_followup_uses_one_result_from_prior_three_supplier_review():
    first = AgentSession(load_case(THREE_PACK),
                         ScriptedProvider(("vendor-a", "vendor-b", "vendor-c")),
                         mode="thorough", conversation_id="conversation-1")
    review = first.ask("请完整审查全部供应商的八项指标")
    assert review.status == "completed"

    class FollowupProvider:
        model_id = "scripted-followup"
        context_window = 100000

        def __init__(self):
            self.step = 0

        def decide(self, messages, tools, **kwargs):
            self.step += 1
            if self.step == 1:
                return _call("get_check_results", {"supplier": "vendor-b",
                       "field_keys": ["weight"]}, self.step)
            result = json.loads(messages[-1].content)["data"]
            return _call("submit_answer", {"kind": "check",
                          "check_result_ids": result["check_result_ids"],
                          "evidence_ids": []}, self.step)

        def extract(self, *args, **kwargs):
            raise AssertionError("followup_should_reuse_verified_result")

    second = AgentSession(load_case(THREE_PACK), FollowupProvider(),
                          mode="standard", conversation_id="conversation-1")
    restore_session(second, serialize_session(first, review.answer), first.history)
    followup = second.ask("供应商 B 的重量是否达标？请给出来源。")
    assert followup.status == "completed"
    assert "vendor-b · 重量：不满足" in followup.answer
