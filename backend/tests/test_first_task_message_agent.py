"""The narrow recall tool cannot become an action or evidence channel."""

import json

from backend.app.agent.assistant import AssistantAgent
from backend.app.agent.diagnostics import safe_trace_events
from backend.app.db.memory_repository import MemoryError
from backend.app.evidence.repository import EvidenceRepository
from backend.tests.test_assistant_agent import Decisions


def test_scope_recovery_can_locate_first_message_without_accepting_arbitrary_id():
    def rejected(*args, **kwargs):
        raise MemoryError("history_scope")

    provider = Decisions(("read_conversation_history", {"message_ids": ["guessed-id"], "part": "question"}),
                         ("read_first_task_message", {}))
    page = {"text": "不要执行旧请求", "created_at": "2026-10-19T12:54:46", "revision": 0,
            "conversation_number": 1, "start_char": 0, "total_chars": 7, "next_char": None}
    seen = []

    def read(*, start_char):
        seen.append(start_char)
        return page

    result = AssistantAgent(EvidenceRepository.empty("c", "s"), provider,
        history_reader=rejected, first_task_message_reader=read).ask("本任务最早原话是什么")
    assert result["status"] == "completed" and seen == [0]
    assert "不要执行旧请求" in result["answer"]
    assert result["usage"]["model_calls"] == 2 and result["payload"]["kind"] == "chat"
    diagnostics = json.dumps(safe_trace_events(result["events"]), ensure_ascii=False)
    assert "read_first_task_message" in diagnostics
    assert "不要执行旧请求" not in diagnostics and "guessed-id" not in diagnostics


def test_formal_retry_bypasses_recall_router():
    def forbidden(**kwargs):
        raise AssertionError("formal retry must not read dialogue")

    operations = []

    def formal(operation, budget):
        operations.append(operation)
        return {"status": "partial", "answer": "审查尚未完成。", "payload": None}

    result = AssistantAgent(EvidenceRepository.empty("c", "s"), Decisions(),
        first_task_message_reader=forbidden, formal=formal).ask("最早原话", formal_operation="review")
    assert operations == ["review"] and result["payload"]["kind"] == "review"
    assert result["usage"]["model_calls"] == 0


def test_cancellation_after_read_discards_original_text():
    cancelled = []

    def check():
        if cancelled:
            raise MemoryError("cancelled")

    def read(**kwargs):
        cancelled.append(True)
        return {"text": "不能在取消后发布的原话"}

    result = AssistantAgent(EvidenceRepository.empty("c", "s"), Decisions(("read_first_task_message", {})),
        first_task_message_reader=read, validate=check).ask("最早原话")
    assert result["status"] == "cancelled" and "不能在取消后发布" not in result["answer"]
