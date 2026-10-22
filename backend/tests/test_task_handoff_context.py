"""Historical task handoffs remain optional, bounded and separate from authority."""

import json

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from backend.app.agent.assistant import AssistantAgent
from backend.app.agent.assistant_context import assemble_assistant_context
from backend.app.agent.diagnostics import safe_trace_events
from backend.app.agent.policy import Budget
from backend.app.evidence.repository import EvidenceRepository
from backend.tests.test_assistant_agent import Decisions


def handoff():
    return {"version": 1, "current_scope": {"snapshot_id": "new"},
            "source_scope": {"conversation_id": "old", "snapshot_id": "old"},
            "changed_fields": ["snapshot_id"],
            "goal": {"message_id": "goal", "task_memory_id": "phase", "text": "历史采购目标999"},
            "recent_request": {"message_id": "old", "task_memory_id": "phase2"},
            "notes": [{"kind": "next_step", "text": "历史待核实事项", "source_message_ids": ["old"]}],
            "last_processing_status": "partial"}


def test_task_handoff_injects_history_boundary_and_current_scope_without_extra_calls():
    provider = Decisions(("chat", {"answer": "请确认本次要继续的事项。"}))
    result = AssistantAgent(EvidenceRepository.empty("c", "new"), provider,
        memory_layers={"task_handoff": handoff()}).ask("接着说")
    system = provider.messages[0][0].content
    assert result["status"] == "completed" and result["usage"]["model_calls"] == 1
    assert "历史采购目标999" in system and '"snapshot_id":"new"' in system
    assert "历史待办不自动成为当前待办" in system and "不能授权" in system
    assert result["context"]["task_handoff_selected"] == 1
    assert result["payload"]["candidate"] is None and result["dynamic_payload"] is None


def test_handoff_is_omitted_whole_before_recent_originals_when_window_is_tight():
    history = [(HumanMessage(content="近期原问题" * 30), AIMessage(content="近期原答复" * 30)) for _ in range(8)]
    baseline, audit = assemble_assistant_context("完整规则", "完整当前问题", history, {}, [],
        Budget(context_window=100000), history_available=True)
    messages, bounded = assemble_assistant_context("完整规则", "完整当前问题", history,
        {"task_handoff": handoff()}, [], Budget(context_window=100000, max_prompt=audit["input_bound"] + 10),
        history_available=True)
    assert [m.content for m in messages] == [m.content for m in baseline]
    assert bounded["history_turns_selected"] == 8 and not bounded["mandatory_overflow"]
    assert bounded["task_handoff_selected"] == 0 and bounded["task_handoff_omitted"] == 1


def test_missing_handoff_keeps_existing_context_without_new_guidance():
    messages, audit = assemble_assistant_context("当前规则", "当前问题", [], {}, [], Budget())
    assert "task_handoff" not in messages[0].content and "read_task_history" not in messages[0].content
    assert audit["task_handoff_selected"] == audit["task_handoff_omitted"] == 0


def test_oversized_handoff_is_rejected_without_clipping_text():
    data = handoff()
    data["goal"]["text"] = "长" * 2000
    with pytest.raises(ValueError, match="memory_scope"):
        assemble_assistant_context("规则", "问题", [], {"task_handoff": data}, [], Budget())


def test_read_task_history_uses_bound_callback_and_paired_tool_messages():
    calls = []
    def read(ids, **kwargs):
        calls.append((ids, kwargs))
        return [{"message_id": "old", "question": "旧完整问题", "conversation_id": "old"}]
    provider = Decisions(("read_task_history", {"message_ids": ["old"], "part": "question", "start_char": 2}),
                         ("chat", {"answer": "已回查历史，请确认这次要做什么。"}))
    result = AssistantAgent(EvidenceRepository.empty("c", "new"), provider,
        memory_layers={"task_handoff": handoff()}, task_history_reader=read).ask("回顾上次")
    assert result["status"] == "completed" and calls == [(["old"], {"part": "question", "start_char": 2})]
    tool = next(m for m in provider.messages[1] if m.type == "tool")
    observation = json.loads(tool.content)
    assert observation["notice"] == "历史讨论仅供回忆，不确认当前规则或事实。"
    assert "旧完整问题" in tool.content and result["context"]["task_history_reads"] == 1
    assert provider.messages[1][-2].tool_calls[0]["id"] == tool.tool_call_id
    events = safe_trace_events(result["events"])
    assert any(e["summary"].get("tool") == "read_task_history" for e in events)


def test_unbound_task_history_tool_is_unavailable_and_forbidden_history_stops_safely():
    plain = AssistantAgent(EvidenceRepository.empty("c", "s"), Decisions())
    assert "read_task_history" not in [s["function"]["name"] for s in plain.schemas]
    def forbidden(*_, **__):
        raise ValueError("history_scope")
    result = AssistantAgent(EvidenceRepository.empty("c", "s"),
        Decisions(("read_task_history", {"message_ids": ["foreign"]})), task_history_reader=forbidden).ask("回顾")
    assert result["status"] == "partial" and result["dynamic_payload"] is None
    assert result["usage"]["model_calls"] == 1


@pytest.mark.parametrize("question,operation", [("今天星期几", None), ("正式审查", "review")])
def test_clock_and_trusted_formal_retry_do_not_read_task_history(question, operation):
    def forbidden(*_, **__):
        pytest.fail("historical request cannot authorize new operations")
    result = AssistantAgent(EvidenceRepository.empty("c", "s"), Decisions(),
        memory_layers={"task_handoff": handoff()}, task_history_reader=forbidden,
        formal=lambda *_: {"status": "waiting_user", "answer": "请补充资料", "payload": None}).ask(
            question, formal_operation=operation)
    assert result["status"] in {"completed", "waiting_user"}
