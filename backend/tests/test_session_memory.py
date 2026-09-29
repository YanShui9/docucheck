"""Persist only bounded, snapshot-scoped server results between Agent workers."""

from dataclasses import asdict
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from backend.app.agent.graph import AgentSession
from backend.app.agent.session_memory import restore_session, serialize_session
from backend.app.evidence.repository import digest, load_case
from backend.app.extraction.fields import FieldCandidate, _validate_candidate
from backend.app.field_contract import FieldFactSet, FieldKey
from backend.app.rules.memory import CheckResult

PACK = Path(__file__).resolve().parents[2] / "examples" / "dev-pack-001"


def _session():
    return AgentSession(load_case(PACK), object(), conversation_id="conversation-1")


def test_verified_result_and_focus_survive_new_session():
    first = _session()
    ref = next(block.evidence_id for block in first.repo.blocks
               if block.supplier == "vendor-a")
    result = CheckResult("result-1", first.repo.snapshot_id, "vendor-a",
                         "memory", "complete", "满足", "threshold_met",
                         (ref,), (ref,), ("fact-1",))
    first.tools.results[result.check_result_id] = result
    first.tools.seen.add(ref)
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
    assert restored.tools.results["result-1"] == result
    assert restored.tools.execute("get_check_results",
                                  {"supplier": "vendor-a"}, None)["evidence_refs"] == [ref]


def test_modified_snapshot_or_evidence_cannot_be_restored():
    session = _session()
    ref = next(block.evidence_id for block in session.repo.blocks
               if block.supplier == "vendor-a")
    session.tools.results["result-1"] = CheckResult(
        "result-1", session.repo.snapshot_id, "vendor-a", "memory",
        "complete", "满足", "threshold_met", (ref,), (ref,), ("fact-1",))
    memory = serialize_session(session, "已核验。")
    memory["check_results"][0]["evidence_refs"] = ["foreign-evidence"]
    with pytest.raises(ValueError, match="memory_scope"):
        restore_session(_session(), memory, [])
    memory["check_results"][0]["evidence_refs"] = [ref]
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
    assert saved["version"] == 2
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
