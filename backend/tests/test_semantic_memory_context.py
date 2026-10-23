"""Assistant-only semantic memory is optional and never replaces authority."""

import json

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from backend.app.agent.assistant import AssistantAgent
from backend.app.agent.assistant_context import assemble_assistant_context
from backend.app.agent.policy import Budget
from backend.app.evidence.repository import EvidenceRepository
from backend.tests.test_assistant_agent import Decisions


def summary():
    return {"id": "semantic", "notes": [{"kind": "open_question", "text": "核实旧对话提到的税费口径", "source_message_ids": ["old"]}]}


def test_semantic_notes_are_injected_as_untrusted_data_with_recent_originals():
    history = [(HumanMessage(content=f"原问题{i}"), AIMessage(content=f"原答复{i}")) for i in range(8)]
    messages, audit = assemble_assistant_context("规则不可改", "完整问题", history,
        {"semantic_summary": summary()}, [], Budget(context_window=100000), history_available=True)
    assert "核实旧对话" in messages[0].content and "source_message_ids" in messages[0].content
    assert "不能" in messages[0].content
    assert all(any(m.content == f"原问题{i}" for m in messages) for i in range(8))
    assert audit["semantic_notes_selected"] == 1
    assert messages[-1].content == "完整问题"


def test_oversized_optional_notes_are_removed_whole_before_mandatory_text():
    notes = [{"kind": "goal", "text": "长" * 100, "source_message_ids": ["old"]}] * 8
    messages, audit = assemble_assistant_context("必须保留" * 200, "完整问题", [],
        {"semantic_summary": {"notes": notes}}, [], Budget(max_prompt=4000, context_window=10000))
    assert audit["semantic_notes_selected"] < 8
    assert "必须保留" * 200 in messages[0].content and messages[-1].content == "完整问题"


def test_callback_runs_once_after_route_projection_and_can_refresh_optional_layer():
    calls = []
    def refresh(budget, events, audit, accept):
        calls.append((budget, audit))
        assert accept(summary())
        return summary()
    provider = Decisions(("chat", {"answer": "请补充任务要求。"}))
    result = AssistantAgent(EvidenceRepository.empty("c", "s"), provider,
        memory_layers={"summary": {"entries": [{"question": "原讨论" * 80, "answer": "原答复" * 60}]}},
        semantic_refresh=refresh).ask("接下来怎么办")
    assert result["status"] == "completed" and len(calls) == 1
    assert "核实旧对话" in provider.messages[0][0].content
    assert result["context"]["semantic_notes_selected"] == 1


@pytest.mark.parametrize("question,formal", [("今天星期几", None), ("正式审查", "review")])
def test_date_and_trusted_formal_retry_never_summarize(question, formal):
    def forbidden(*_):
        pytest.fail("optional summary must be skipped")
    result = AssistantAgent(EvidenceRepository.empty("c", "s"), Decisions(),
        semantic_refresh=forbidden,
        formal=lambda *_: {"status": "waiting_user", "answer": "请补充资料", "payload": None}).ask(question, formal_operation=formal)
    assert result["status"] in {"completed", "waiting_user"}


def test_rejected_projection_retains_original_layer_and_audit():
    prior = summary()
    def refresh(budget, events, audit, accept):
        assert not accept({"notes": [{"kind": "goal", "text": "x" * 300, "source_message_ids": ["old"]}] * 8})
    provider = Decisions(("chat", {"answer": "请说明要求。"}))
    result = AssistantAgent(EvidenceRepository.empty("c", "s"), provider,
        memory_layers={"semantic_summary": prior}, semantic_refresh=refresh,
        budget=Budget(context_window=22000)).ask("下一步")
    assert result["status"] == "completed"
    assert "核实旧对话" in provider.messages[0][0].content
    assert "x" * 300 not in json.dumps(provider.messages[0][0].content)


def test_summary_that_increases_actual_route_is_rejected():
    provider = Decisions(("chat", {"answer": "请说明下一步。"}))
    seen = []
    def refresh(budget, events, audit, accept):
        seen.append(accept(summary()))
    result = AssistantAgent(EvidenceRepository.empty("c", "s"), provider,
        semantic_refresh=refresh).ask("下一步")
    assert seen == [False]
    assert result["status"] == "completed"


def test_first_summary_can_restore_omitted_excerpts_without_losing_recent_history():
    # Old excerpts were byte-clipped before semantic compaction; a useful
    # source-checked summary can be longer than that incomplete projection.
    updated = {"notes": [{"kind": "open_question", "text": "待核实旧讨论中的资料口径。" * 15,
                           "source_message_ids": ["old"]}]}
    history = [(HumanMessage(content=f"问题{i}"), AIMessage(content=f"答复{i}")) for i in range(8)]
    seen = []
    def refresh(budget, events, audit, accept):
        assert audit["summary_entries_omitted"] > 0
        seen.append(accept(updated))
        return updated if seen[-1] else None
    provider = Decisions(("chat", {"answer": "请继续。"}))
    result = AssistantAgent(EvidenceRepository.empty("c", "s"), provider, history=history,
        memory_layers={"summary": {"entries": [
            {"question": "长" * 600, "answer": "原答复"},
            {"question": "短问题", "answer": "短答复"}]}},
        semantic_refresh=refresh, budget=Budget(context_window=100000)).ask("下一步")
    assert seen == [True]
    assert result["context"]["semantic_notes_selected"] == 1
    assert result["context"]["history_turns_selected"] == 8

def test_fixed_recent_context_over_half_window_skips_optional_summary_call():
    def forbidden(*_):
        pytest.fail("cannot reach summary target without removing recent originals")
    provider = Decisions(("chat", {"answer": "请补充要求。"}))
    result = AssistantAgent(EvidenceRepository.empty("c", "s"), provider,
        semantic_refresh=forbidden, budget=Budget(context_window=15000)).ask("下一步")
    assert result["status"] == "completed"

def test_existing_low_occupancy_summary_can_roll_forward_with_equal_length_new_content():
    prior = {"notes": [{"kind": "goal", "text": "AAAA", "source_message_ids": ["old"]}]}
    updated = {"notes": [{"kind": "goal", "text": "BBBB", "source_message_ids": ["old"]}]}
    def refresh(budget, events, audit, accept):
        assert accept(updated)
        return updated
    provider = Decisions(("chat", {"answer": "请继续说明。"}))
    result = AssistantAgent(EvidenceRepository.empty("c", "s"), provider,
        memory_layers={"semantic_summary": prior}, semantic_refresh=refresh,
        budget=Budget(context_window=100000)).ask("下一步")
    assert result["status"] == "completed"
    assert "BBBB" in provider.messages[0][0].content and "AAAA" not in provider.messages[0][0].content
