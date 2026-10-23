"""History scope failures may correct tool choice once without relaxing readers."""

import json

import pytest

from backend.app.agent.assistant import AssistantAgent
from backend.app.agent.diagnostics import safe_trace_events
from backend.app.db.memory_repository import MemoryError
from backend.app.evidence.repository import EvidenceRepository
from backend.app.providers.base import ProviderError
from backend.tests import test_document_ingestion
from backend.tests.test_assistant_agent import Decisions
from backend.tests.test_task_handoff_workflow import (
    conversation,
    execute,
    manifest,
    send,
)

ingestion_case = test_document_ingestion.ingestion_case


def rejected(*args, **kwargs):
    raise MemoryError("history_scope")


def test_wrong_conversation_tool_can_correct_to_frozen_task_reader_once():
    calls = []

    def task_reader(ids, **kwargs):
        calls.append((ids, kwargs))
        assert ids == ["old"]
        return [{"message_id": "old", "part": "question", "text": "历史最后请求"}]

    provider = Decisions(
        ("read_conversation_history", {"message_ids": ["old"], "part": "question"}),
        ("read_task_history", {"message_ids": ["old"], "part": "question"}),
        ("chat", {"answer": "上一段最后请求是：历史最后请求。"}),
    )
    result = AssistantAgent(EvidenceRepository.empty("c", "s"), provider,
        history_reader=rejected, task_history_reader=task_reader).ask("上一段最后请求是什么，只回忆")
    assert result["status"] == "completed", result
    assert result["usage"]["model_calls"] == 3
    assert len(calls) == 1 and result["context"]["task_history_reads"] == 1
    observation = json.loads(provider.messages[1][-1].content)
    assert observation["error"] == "history_scope" and "read_task_history" in observation["notice"]
    for messages in provider.messages[1:]:
        assert messages[-2].tool_calls[0]["id"] == messages[-1].tool_call_id
    events = safe_trace_events(result["events"])
    assert any(e["summary"].get("tool") == "read_conversation_history"
               and e["summary"]["status"] == "rejected" for e in events)
    assert "历史最后请求" not in json.dumps(events, ensure_ascii=False)


@pytest.mark.parametrize("second", ["read_conversation_history", "read_task_history"])
def test_foreign_handle_never_becomes_readable_and_second_failure_stops(second):
    calls = []

    def reader(ids, **kwargs):
        calls.append(ids)
        return rejected()

    provider = Decisions(
        ("read_conversation_history", {"message_ids": ["private-secret"], "part": "question"}),
        (second, {"message_ids": ["foreign"], "part": "question"}),
    )
    result = AssistantAgent(EvidenceRepository.empty("c", "s"), provider,
        history_reader=reader, task_history_reader=reader).ask("回查最早原话")
    assert result["status"] == "partial" and len(calls) == 2
    assert result["error"] == "history_scope"
    assert "当前可回读" in result["answer"] and "history_scope" not in result["answer"]
    assert result["payload"]["candidate"] is None and not result["payload"]["citations"]
    assert "private-secret" not in json.dumps(safe_trace_events(result["events"]))


def test_chat_after_failed_read_uses_server_explanation_instead_of_invented_quote():
    provider = Decisions(
        ("read_conversation_history", {"message_ids": ["missing"], "part": "question"}),
        ("chat", {"answer": "杜撰的最早原话"}),
    )
    result = AssistantAgent(EvidenceRepository.empty("c", "s"), provider,
        history_reader=rejected).ask("最早的原话")
    assert result["status"] == "partial" and result["error"] == "history_scope"
    assert "杜撰" not in result["answer"] and "当前可回读" in result["answer"]


def test_repeated_successful_history_read_stops_before_decision_budget():
    calls = []

    def reader(ids, **kwargs):
        calls.append(ids)
        return [{"message_id": "old", "text": "历史原话"}]

    query = {"message_ids": ["old"], "part": "question"}
    result = AssistantAgent(EvidenceRepository.empty("c", "s"),
        Decisions(("read_conversation_history", query), ("read_conversation_history", query)),
        history_reader=reader).ask("回读")
    assert result["status"] == "partial" and result["error"] == "history_scope"
    assert result["usage"]["model_calls"] == 2 and len(calls) == 1
    assert result["events"][-1]["failure_detail"] == "history_no_progress"


def test_history_pagination_is_not_mistaken_for_repeated_read():
    starts = []

    def reader(ids, **kwargs):
        starts.append(kwargs["start_char"])
        return [{"message_id": "old", "text": "原话", "next_char": 100 if len(starts) == 1 else None}]

    result = AssistantAgent(EvidenceRepository.empty("c", "s"), Decisions(
        ("read_conversation_history", {"message_ids": ["old"], "part": "question"}),
        ("read_conversation_history", {"message_ids": ["old"], "part": "question", "start_char": 100}),
        ("chat", {"answer": "已回读原话。"})), history_reader=reader).ask("回读")
    assert result["status"] == "completed" and starts == [0, 100]


@pytest.mark.parametrize("code", ["memory_context_changed", "cancelled", "snapshot_changed"])
def test_non_scope_invalidation_is_not_recovered(code):
    from backend.app.agent.policy import RunStopped

    def invalid(*args, **kwargs):
        raise RunStopped(code)

    result = AssistantAgent(EvidenceRepository.empty("c", "s"),
        Decisions(("read_conversation_history", {"message_ids": ["old"], "part": "question"})),
        history_reader=invalid).ask("回读")
    assert result["error"] == code and result["usage"]["model_calls"] == 1


def test_unknown_model_outcome_after_history_rejection_is_not_replayed():
    class UnknownAfterRead(Decisions):
        def decide(self, *args, **kwargs):
            if self.messages:
                raise ProviderError("timeout", outcome_unknown=True)
            return super().decide(*args, **kwargs)

    result = AssistantAgent(EvidenceRepository.empty("c", "s"), UnknownAfterRead(
        ("read_conversation_history", {"message_ids": ["old"], "part": "question"})),
        history_reader=rejected).ask("回读")
    assert result["error"] == "timeout" and result["usage"]["model_calls"] == 2
    assert result["usage"]["unknown_usage_calls"] == 1


def test_recovery_cannot_execute_historical_business_request():
    provider = Decisions(
        ("read_conversation_history", {"message_ids": ["missing"], "part": "question"}),
        ("review_checklist", {}),
    )

    def formal(*args):
        pytest.fail("history recovery must not start formal review")

    result = AssistantAgent(EvidenceRepository.empty("c", "s"), provider,
        history_reader=rejected, formal=formal).ask("回忆上次要做什么，不执行")
    assert result["status"] == "partial" and result["error"] == "invalid_output"
    assert result["dynamic_payload"] is None


def test_cancellation_after_range_rejection_stops_before_correction():
    from backend.app.agent.policy import RunStopped

    cancelled = False

    def reader(*args, **kwargs):
        nonlocal cancelled
        cancelled = True
        return rejected()

    def validate():
        if cancelled:
            raise RunStopped("cancelled")

    result = AssistantAgent(EvidenceRepository.empty("c", "s"), Decisions(
        ("read_conversation_history", {"message_ids": ["old"], "part": "question"})),
        history_reader=reader, validate=validate).ask("回读")
    assert result["status"] == "cancelled" and result["usage"]["model_calls"] == 1


@pytest.mark.parametrize("foreign", [False, True])
def test_api_worker_wrong_tool_recovery_keeps_frozen_source_whitelist(ingestion_case, foreign):
    client, engine, _, case = ingestion_case
    old = send(client, conversation(client, case), "上一段最后请求：先核对交期", "old")
    assert execute(engine, old, Decisions(("chat", {"answer": "请提供资料。"}))).status == "completed"
    new = send(client, conversation(client, case), "上一段最后请求是什么？只回忆", "new")
    target = new["message_id"] if foreign else old["message_id"]
    actions = [
        ("read_conversation_history", {"message_ids": [old["message_id"]], "part": "question"}),
        ("read_task_history", {"message_ids": [target], "part": "question"}),
    ]
    if not foreign:
        actions.append(("chat", {"answer": "上一段最后请求是：先核对交期。"}))
    provider = Decisions(*actions)
    report = execute(engine, new, provider)
    assert report.status == ("partial" if foreign else "completed"), report
    assert report.model_calls == (2 if foreign else 3)
    assert not manifest(engine, new).get("history_reads")
    if foreign:
        assert "当前可回读" in report.answer_text
    else:
        assert "上一段最后请求：先核对交期" in provider.messages[2][-1].content
    assert client.get(f"/api/v1/agent-runs/{new['run_id']}/checks").json()["checks"] == []
    payload = client.get(f"/api/v1/agent-runs/{new['run_id']}/assistant-result").json()["result"]
    assert payload["candidate"] is None and not payload["citations"]
