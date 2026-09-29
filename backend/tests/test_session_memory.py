"""Persist only bounded, snapshot-scoped server results between Agent workers."""

from pathlib import Path

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from backend.app.agent.graph import AgentSession
from backend.app.agent.session_memory import restore_session, serialize_session
from backend.app.evidence.repository import load_case
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
