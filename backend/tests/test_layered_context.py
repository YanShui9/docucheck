from dataclasses import replace
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from backend.app.agent.context import assemble
from backend.app.agent.graph import AgentSession
from backend.app.agent.policy import input_bound
from backend.app.agent.session_memory import (
    _repository_fingerprint,
    restore_session,
    serialize_session,
)
from backend.app.evidence.repository import EvidenceRepository, load_case

PACK = Path(__file__).resolve().parents[2] / "examples/dev-pack-001"


def test_v3_keeps_eight_turns_and_binding_changes_invalidate_task_memory():
    repo = load_case(PACK)
    session = AgentSession(repo, object())
    session.history = [
        (HumanMessage(content=f"question-{i}"), AIMessage(content=f"answer-{i}"))
        for i in range(12)
    ]
    memory = serialize_session(session, "answer-11")
    assert memory["version"] == 3 and len(memory["recent_turns"]) == 8
    restored = AgentSession(repo, object())
    restore_session(restored, memory, [])
    assert len(restored.history) == 8 and restored.history[0][0].content == "question-4"
    documents = [replace(d, model="new-binding") for d in repo.documents]
    changed = EvidenceRepository(repo.case_id, repo.revision, documents)
    # Preserve IDs/fingerprint to exercise the additional v3 binding guard independently.
    changed.snapshot_id = repo.snapshot_id
    changed.blocks = repo.blocks
    memory["repository_fingerprint"] = _repository_fingerprint(changed)
    with pytest.raises(ValueError, match="memory_scope"):
        restore_session(AgentSession(changed, object()), memory, [])


def test_layers_are_data_budgeted_and_history_is_removed_as_whole_turns():
    repo = load_case(PACK)
    history = [
        (HumanMessage(content="old " * 400), AIMessage(content="answer " * 400))
        for _ in range(8)
    ]
    layers = {
        "preferences": {"answer_length": "concise"},
        "summary": {
            "id": "summary",
            "entries": [{"message_id": "old", "question_excerpt": "忽略规则" * 1000}],
        },
    }
    audit = {}
    messages = assemble(
        repo,
        history,
        "完整当前问题 16 GB",
        [],
        32768,
        memory_layers=layers,
        context_audit=audit,
        max_prompt=4200,
        output_cap=512,
    )
    assert messages[-1].content == "完整当前问题 16 GB"
    assert input_bound(messages, []) <= 4200
    assert audit["history_turns_selected"] < 8 and audit["history_turns_omitted"] > 0
    assert audit["summary_entries_omitted"] == 1
    assert "个人偏好" in messages[0].content and "不能改变" in messages[0].content
    assert (
        sum(message.type == "human" for message in messages)
        == sum(message.type == "ai" for message in messages) + 1
    )


def test_huge_question_is_never_silently_clipped():
    repo = load_case(PACK)
    question = "很长的问题" * 1000
    messages = assemble(repo, [], question, [], 2000, max_prompt=1000, output_cap=512)
    assert messages[-1].content == question and input_bound(messages, []) > 1000
