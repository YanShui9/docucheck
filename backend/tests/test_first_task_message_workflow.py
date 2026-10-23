"""API -> worker -> stored answer returns exact original words without an ID."""

from sqlalchemy import select

from backend.app.db.models import agent_runs
from backend.tests import test_task_handoff_workflow
from backend.tests.test_assistant_agent import Decisions
from backend.tests.test_task_handoff_workflow import conversation, execute, send

ingestion_case = test_task_handoff_workflow.ingestion_case


def test_lookup_is_terminal_exact_and_readable(ingestion_case):
    client, engine, _, case = ingestion_case
    original = "我想买苹果20箱。然后每箱的单价不得超过25块"
    old = send(client, conversation(client, case), original, "old")
    execute(engine, old, Decisions(("chat", {"answer": "请确认清单。"})))
    current = send(client, conversation(client, case), "本采购任务最早一条原话是什么", "current")
    provider = Decisions(("read_first_task_message", {}))
    report = execute(engine, current, provider)
    assert report.status == "completed", report
    assert report.model_calls == 1
    payload = client.get(f"/api/v1/agent-runs/{current['run_id']}/assistant-result").json()["result"]
    assert original in payload["answer"]
    assert "对话 1" in payload["answer"] and "北京时间" in payload["answer"]
    assert "资料版本 0" in payload["answer"]
    assert old["message_id"] not in payload["answer"] and "message_id" not in payload["answer"]
    assert payload["kind"] == "chat" and payload["candidate"] is None and payload["changes"] == []
    assert client.get(f"/api/v1/agent-runs/{current['run_id']}/checks").json()["checks"] == []
    with engine.connect() as connection:
        manifest = connection.scalar(select(agent_runs.c.context_manifest_json).where(agent_runs.c.id == current["run_id"]))
    assert manifest["first_task_message"]["source"]["message_id"] == old["message_id"]
    assert original not in str(manifest)


def test_no_prior_message_does_not_echo_current_question(ingestion_case):
    client, engine, _, case = ingestion_case
    current = send(client, conversation(client, case), "最早原话", "current")
    report = execute(engine, current, Decisions(("read_first_task_message", {})))
    assert report.status == "completed"
    assert "本次提问之前" in report.answer_text and "没有" in report.answer_text
    assert "message_id" not in report.answer_text


def test_tool_schema_forbids_model_supplied_scope(ingestion_case):
    client, engine, _, case = ingestion_case
    current = send(client, conversation(client, case), "最早原话", "current")
    provider = Decisions(("read_first_task_message", {"case_id": "other-case"}))
    report = execute(engine, current, provider)
    assert report.status == "partial" and report.error_code == "invalid_output"


def test_long_original_is_explicitly_partial(ingestion_case):
    client, engine, _, case = ingestion_case
    original = "中文采购要求。" * 250
    old = send(client, conversation(client, case), original, "old")
    execute(engine, old, Decisions(("chat", {"answer": "请确认。"})))
    current = send(client, conversation(client, case), "回读最早原话", "current")
    report = execute(engine, current, Decisions(("read_first_task_message", {})))
    assert report.status == "completed"
    assert "本段" in report.answer_text and "继续" in report.answer_text
    assert f"共{len(original)}字" in report.answer_text


def test_earlier_original_from_another_case_is_not_visible(ingestion_case):
    client, engine, _, case = ingestion_case
    other = client.post("/api/v1/cases").json()["id"]
    foreign = send(client, conversation(client, other), "另一个任务的最早原话不可读取", "foreign")
    execute(engine, foreign, Decisions(("chat", {"answer": "已收到。"})))
    local = send(client, conversation(client, case), "当前任务自己的原话", "local")
    execute(engine, local, Decisions(("chat", {"answer": "请确认。"})))
    current = send(client, conversation(client, case), "最早原话", "current")
    report = execute(engine, current, Decisions(("read_first_task_message", {})))
    assert report.status == "completed"
    assert "当前任务自己的原话" in report.answer_text and "另一个任务" not in report.answer_text


def test_past_end_page_explains_position_without_misstating_task_scope(ingestion_case):
    client, engine, _, case = ingestion_case
    old = send(client, conversation(client, case), "短原话", "old")
    execute(engine, old, Decisions(("chat", {"answer": "请确认。"})))
    current = send(client, conversation(client, case), "继续最早原话", "current")
    report = execute(engine, current, Decisions(("read_first_task_message", {"start_char": 100})))
    assert report.status == "partial"
    assert "位置" in report.answer_text and "从头" in report.answer_text
    assert "交接" not in report.answer_text
