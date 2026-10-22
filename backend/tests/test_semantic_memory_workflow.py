"""SQLite API-to-worker compaction, retry, and original-message readback."""

import json

import pytest
from langchain_core.messages import AIMessage
from sqlalchemy import select

from backend.app.db.models import agent_runs, conversation_summaries, run_events
from backend.app.db.run_executor import execute_run
from backend.app.providers.base import ProviderError
from backend.tests import test_document_ingestion
from backend.tests.test_assistant_agent import Decisions

ingestion_case = test_document_ingestion.ingestion_case


class SummarizingProvider(Decisions):
    context_window = 1000000
    summary_calls = 0

    def __init__(self, error=None, invalid=False):
        super().__init__(("chat", {"answer": "请继续说明任务。"}))
        self.error, self.invalid = error, invalid

    def extract(self, messages, schema, **kwargs):
        self.summary_calls += 1
        self.summary_messages = messages
        if self.error:
            raise self.error
        data = json.loads(messages[1].content.removeprefix("HISTORY_DATA="))
        return AIMessage(content="", tool_calls=[{"name": "summarize_conversation", "id": "sum", "args": {
            "notes": [{"kind": "goal", "text": "继续整理采购问题，旧答复需要核实。",
                       "source_message_ids": ["foreign" if self.invalid else data["sources"][0]["message_id"]]}]}}],
            usage_metadata={"input_tokens": 500, "output_tokens": 40, "total_tokens": 540})


def send(client, conv, key):
    response = client.post(f"/api/v1/conversations/{conv}/messages", headers={"Idempotency-Key": key},
        json={"content": "继续说明采购目标" + "补充讨论" * 60, "operation": "assistant", "expected_revision": 0})
    assert response.status_code == 202, response.text
    return response.json()


def populate(case):
    client, engine, _, case_id = case
    conv = client.post(f"/api/v1/cases/{case_id}/conversations").json()["id"]
    receipts = []
    for i in range(12):
        receipt = send(client, conv, f"history-{i}")
        provider = Decisions(("chat", {"answer": "请继续说明任务要求。" + "讨论说明" * 40}))
        provider.context_window = 1000000
        report = execute_run(engine, receipt["run_id"], receipt["attempt_id"], provider, heartbeat_interval_seconds=60)
        assert report.status == "completed", report
        receipts.append(receipt)
    return conv, receipts


def test_worker_saves_semantic_memory_and_recent_originals_remain_readable(ingestion_case):
    client, engine, _, _ = ingestion_case
    conv, receipts = populate(ingestion_case)
    provider, receipt = SummarizingProvider(), send(client, conv, "with-summary")
    report = execute_run(engine, receipt["run_id"], receipt["attempt_id"], provider, heartbeat_interval_seconds=60)
    assert report.status == "completed" and provider.summary_calls == 1 and report.model_calls == 2
    assert "旧答复需要核实" in provider.messages[0][0].content
    with engine.connect() as connection:
        rows = connection.execute(select(conversation_summaries)).mappings().all()
        manifest = connection.scalar(select(agent_runs.c.context_manifest_json).where(agent_runs.c.id == receipt["run_id"]))
    assert any(row["summary_json"]["kind"] == "semantic_memory_v1" for row in rows)
    assert manifest["semantic_memory"]["summary_id"]
    assert "旧答复需要核实" not in json.dumps(manifest, ensure_ascii=False)
    # Existing immutable conversations and phases are preserved.
    old = client.get(f"/api/v1/agent-runs/{receipts[0]['run_id']}/assistant-result")
    assert old.status_code == 200 and "讨论说明" in old.json()["result"]["answer"]
    next_provider, next_receipt = SummarizingProvider(), send(client, conv, "keep-summary")
    next_report = execute_run(engine, next_receipt["run_id"], next_receipt["attempt_id"], next_provider, heartbeat_interval_seconds=60)
    assert next_report.status == "completed" and next_provider.summary_calls == 0
    assert "旧答复需要核实" in next_provider.messages[0][0].content
    # A summary handle can still read the complete authorized original question.
    source_id = json.loads(provider.summary_messages[1].content.removeprefix("HISTORY_DATA="))["sources"][0]["message_id"]
    reader = Decisions(("read_conversation_history", {"message_ids": [source_id], "part": "question"}),
                       ("chat", {"answer": "已回读原来的讨论，请继续说明要求。"}))
    reader.context_window = 1000000
    read_receipt = send(client, conv, "read-summary-source")
    read_report = execute_run(engine, read_receipt["run_id"], read_receipt["attempt_id"], reader, heartbeat_interval_seconds=60)
    assert read_report.status == "completed" and read_report.model_calls == 2
    assert any("补充讨论" * 60 in message.content for message in reader.messages[-1] if message.type == "tool")
    with engine.connect() as connection:
        read_manifest = connection.scalar(select(agent_runs.c.context_manifest_json).where(agent_runs.c.id == read_receipt["run_id"]))
    assert read_manifest["history_reads"][0]["message_id"] == source_id
    # Failed later rolling output keeps the last valid summary, rather than deleting it.
    extra = send(client, conv, "before-invalid-roll")
    filler = Decisions(("chat", {"answer": "继续说明任务。"}))
    filler.context_window = 1000000
    assert execute_run(engine, extra["run_id"], extra["attempt_id"], filler, heartbeat_interval_seconds=60).status == "completed"
    failed_roll, failed_receipt = SummarizingProvider(invalid=True), send(client, conv, "invalid-roll")
    failed_report = execute_run(engine, failed_receipt["run_id"], failed_receipt["attempt_id"], failed_roll, heartbeat_interval_seconds=60)
    assert failed_report.status == "completed" and failed_roll.summary_calls == 1
    assert "旧答复需要核实" in failed_roll.messages[0][0].content
    with engine.connect() as connection:
        saved = connection.scalars(select(conversation_summaries.c.summary_json)).all()
    assert len([row for row in saved if row["kind"] == "semantic_memory_v1"]) == 1


@pytest.mark.parametrize("error", [None, ProviderError("rate_limit")])
def test_invalid_or_known_failed_summary_falls_back_to_answer(ingestion_case, error):
    client, engine, _, _ = ingestion_case
    conv, _ = populate(ingestion_case)
    provider, receipt = SummarizingProvider(error=error, invalid=True), send(client, conv, "bad-summary")
    report = execute_run(engine, receipt["run_id"], receipt["attempt_id"], provider, heartbeat_interval_seconds=60)
    assert report.status == "completed" and report.model_calls == 2 and provider.summary_calls == 1
    with engine.connect() as connection:
        rows = connection.scalars(select(conversation_summaries.c.summary_json)).all()
    assert not any(row["kind"] == "semantic_memory_v1" for row in rows)


def test_unknown_summary_stops_then_explicit_retry_does_not_replay_it(ingestion_case):
    client, engine, _, _ = ingestion_case
    conv, _ = populate(ingestion_case)
    provider = SummarizingProvider(error=ProviderError("timeout", outcome_unknown=True))
    receipt = send(client, conv, "unknown-summary")
    report = execute_run(engine, receipt["run_id"], receipt["attempt_id"], provider, heartbeat_interval_seconds=60)
    assert report.status == "partial" and report.model_calls == 1 and provider.summary_calls == 1
    assert not provider.messages
    retry = client.post(f"/api/v1/agent-runs/{receipt['run_id']}/retry", headers={"Idempotency-Key": "explicit-retry"}, json={"expected_revision": 0})
    assert retry.status_code == 202, retry.text
    retried = SummarizingProvider()
    report = execute_run(engine, receipt["run_id"], retry.json()["attempt_id"], retried, heartbeat_interval_seconds=60)
    assert report.status == "completed" and retried.summary_calls == 0 and report.model_calls == 1
    with engine.connect() as connection:
        markers = connection.execute(select(run_events).where(run_events.c.run_id == receipt["run_id"],
            run_events.c.kind == "semantic_summary_started")).all()
    assert len(markers) == 1


def test_successive_equal_size_summaries_advance_coverage_watermark(ingestion_case):
    client, engine, _, _ = ingestion_case
    conv, _ = populate(ingestion_case)
    for i in range(5):
        provider, receipt = SummarizingProvider(), send(client, conv, f"rolling-{i}")
        report = execute_run(engine, receipt["run_id"], receipt["attempt_id"], provider, heartbeat_interval_seconds=60)
        assert report.status == "completed", report
        assert provider.summary_calls == (1 if i in {0, 4} else 0)
    with engine.connect() as connection:
        rows = connection.scalars(select(conversation_summaries.c.summary_json).order_by(conversation_summaries.c.version)).all()
    semantic = [row for row in rows if row["kind"] == "semantic_memory_v1"]
    assert [row["through_version"] for row in semantic] == [4, 8]
    assert semantic[0]["notes"] == semantic[1]["notes"]
