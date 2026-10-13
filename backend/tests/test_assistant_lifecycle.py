"""Cancellation, invalidation and budget fences apply to conversational attempts."""

import pytest
from sqlalchemy import delete, select

from backend.app.db.models import (
    agent_runs,
    assistant_runs,
    project_members,
    run_attempts,
    task_memory,
)
from backend.app.db.run_executor import execute_run
from backend.app.db.run_repository import RepositoryError, RunRepository
from backend.tests import test_document_ingestion, test_dynamic_workflow
from backend.tests.test_assistant_agent import Decisions
from backend.tests.test_assistant_workflow import run, send
from backend.tests.test_dynamic_agent import ScriptProvider

ingestion_case = test_document_ingestion.ingestion_case
dynamic_case = test_dynamic_workflow.dynamic_case


def submit_empty(ingestion_case):
    client, _, _, case = ingestion_case
    conv = client.post(f"/api/v1/cases/{case}/conversations").json()["id"]
    response = client.post(f"/api/v1/conversations/{conv}/messages", headers={"Idempotency-Key": "empty-lifecycle"},
        json={"content": "你好", "operation": "assistant", "expected_revision": 0})
    assert response.status_code == 202, response.text
    return response.json()


def test_cancel_during_model_call_retains_known_usage_without_reply(ingestion_case):
    receipt = submit_empty(ingestion_case)
    _, engine, _, _ = ingestion_case

    class Cancel(Decisions):
        def decide(self, messages, schemas, **options):
            response = super().decide(messages, schemas, **options)
            RunRepository(engine).cancel(receipt["run_id"])
            with engine.connect() as connection:
                assert connection.scalar(select(run_attempts.c.outcome_unknown)) is True
            return response

    report = execute_run(engine, receipt["run_id"], receipt["attempt_id"], Cancel(("chat", {"answer": "不应提交的答复"})), heartbeat_interval_seconds=60)
    assert report.status == "cancelled" and report.model_calls == 1
    with engine.connect() as connection:
        attempt = connection.execute(select(run_attempts)).mappings().one()
        assert attempt["usage_json"]["actual_input"] == 100
        assert attempt["outcome_unknown"] is False
        assert connection.scalar(select(agent_runs.c.answer_text)) is None
        assert connection.scalar(select(assistant_runs.c.payload_json)) is None
        assert connection.execute(select(task_memory)).first() is None


def test_cancel_during_unknown_provider_error_preserves_uncertainty(ingestion_case):
    from backend.app.providers.base import ProviderError

    receipt = submit_empty(ingestion_case)
    _, engine, _, _ = ingestion_case

    class CancelUnknown(Decisions):
        def decide(self, messages, schemas, **options):
            RunRepository(engine).cancel(receipt["run_id"])
            with engine.connect() as connection:
                assert connection.scalar(select(run_attempts.c.outcome_unknown)) is True
            raise ProviderError("timeout", outcome_unknown=True)

    report = execute_run(engine, receipt["run_id"], receipt["attempt_id"],
                         CancelUnknown(), heartbeat_interval_seconds=60)
    assert report.status == "cancelled" and report.model_calls == 1
    with engine.connect() as connection:
        attempt = connection.execute(select(run_attempts)).mappings().one()
        assert attempt["outcome_unknown"] is True
        assert attempt["usage_json"]["model_calls"] == 1
        assert attempt["usage_json"]["actual_input"] is None
        assert attempt["usage_json"]["unknown_usage_calls"] == 1
        assert connection.scalar(select(agent_runs.c.answer_text)) is None
        assert connection.scalar(select(assistant_runs.c.payload_json)) is None
        assert connection.execute(select(task_memory)).first() is None


def test_memory_change_blocks_final_commit_and_retains_known_usage(ingestion_case):
    client, engine, _, _ = ingestion_case
    receipt = submit_empty(ingestion_case)

    class Change(Decisions):
        def decide(self, messages, schemas, **options):
            response = super().decide(messages, schemas, **options)
            changed = client.put("/api/v1/me/memories/answer_length", json={"value": "concise", "expected_revision": 0, "confirmed": True})
            assert changed.status_code == 200
            return response

    with pytest.raises(RepositoryError, match="memory_context_changed"):
        execute_run(engine, receipt["run_id"], receipt["attempt_id"], Change(("chat", {"answer": "不应提交"})), heartbeat_interval_seconds=60)
    with engine.connect() as connection:
        row = connection.execute(select(agent_runs)).mappings().one()
        assert row["status"] == "interrupted" and row["answer_text"] is None
        assert connection.scalar(select(run_attempts.c.usage_json))["model_calls"] == 1
        assert connection.execute(select(task_memory)).first() is None


def test_empty_chat_never_instantiates_embedding_retriever(ingestion_case):
    receipt = submit_empty(ingestion_case)

    def unavailable(*args):
        raise AssertionError("empty case must not initialize vector retrieval")

    assert execute_run(ingestion_case[1], receipt["run_id"], receipt["attempt_id"], Decisions(("chat", {"answer": "你好"})),
        heartbeat_interval_seconds=60, retriever_factory=unavailable).status == "completed"


def test_formal_subworkflow_cannot_reset_call_budget(dynamic_case, monkeypatch):
    from backend.app.agent.policy import MODE_LIMITS

    test_dynamic_workflow.confirm(dynamic_case)
    limits = MODE_LIMITS["standard"] | {"max_calls": 2}
    monkeypatch.setitem(MODE_LIMITS, "standard", limits)
    receipt = send(dynamic_case, "正式审查")
    provider = ScriptProvider()
    report = run(dynamic_case, receipt, provider)
    assert report.status == "partial" and report.error_code == "model_budget"
    assert report.model_calls == 2 and provider.extractions == 0
    checks = dynamic_case[0].get(f"/api/v1/agent-runs/{receipt['run_id']}/checks").json()["checks"]
    assert not any(c["is_formal"] for c in checks)


def test_persisted_retriever_resources_close_after_invalid_model_output(dynamic_case):
    from backend.app.evidence.retrieval import Retriever

    receipt = send(dynamic_case, "你好")
    closed = []

    class Managed(Retriever):
        def close(self):
            closed.append(True)

    report = execute_run(dynamic_case[1], receipt["run_id"], receipt["attempt_id"], Decisions(("execute", {"actor": "admin"})),
        heartbeat_interval_seconds=60, retriever_factory=lambda engine, repo, claim: Managed(repo))
    assert report.status == "partial" and closed == [True]


def test_assistant_result_acl_and_revocation_before_commit(tmp_path):
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine

    from backend.app.api.main import create_app
    from backend.app.db.models import metadata
    from backend.tests.test_api_workspace_authorization import DemoAuthenticator

    engine = create_engine(f"sqlite:///{tmp_path / 'acl.db'}")
    metadata.create_all(engine)
    client = TestClient(create_app(engine, authenticator=DemoAuthenticator()))
    alice, bob = {"Authorization": "Bearer alice"}, {"Authorization": "Bearer bob"}
    project = client.post("/api/v1/projects", headers=alice, json={"name": "采购"}).json()
    case = client.post("/api/v1/cases", headers=alice, json={"project_id": project["id"]}).json()["id"]
    conv = client.post(f"/api/v1/cases/{case}/conversations", headers=alice).json()["id"]
    receipt = client.post(f"/api/v1/conversations/{conv}/messages", headers=alice | {"Idempotency-Key": "authorized"},
        json={"content": "你好", "operation": "assistant", "expected_revision": 0}).json()
    url = f"/api/v1/agent-runs/{receipt['run_id']}/assistant-result"
    assert client.get(url, headers=bob).status_code == 404

    class Revoke(Decisions):
        def decide(self, messages, schemas, **options):
            response = super().decide(messages, schemas, **options)
            with engine.begin() as connection:
                connection.execute(delete(project_members).where(project_members.c.project_id == project["id"]))
            return response

    with pytest.raises(RepositoryError, match="source_access_changed"):
        execute_run(engine, receipt["run_id"], receipt["attempt_id"], Revoke(("chat", {"answer": "不应提交"})), heartbeat_interval_seconds=60)
    with engine.connect() as connection:
        assert connection.scalar(select(agent_runs.c.status)) == "interrupted"
        assert connection.scalar(select(run_attempts.c.usage_json))["model_calls"] == 1
    assert client.get(url, headers=alice).status_code == 404
    engine.dispose()


def test_terminal_schema_validation_rejects_invalid_payload_before_writing(ingestion_case):
    from backend.app.agent.assistant import AssistantAgent
    from backend.app.evidence.repository import EvidenceRepository
    from backend.app.runtime.lifecycle import RunStatus

    receipt = submit_empty(ingestion_case)
    _, engine, _, case = ingestion_case
    runs = RunRepository(engine)
    claim = runs.claim(receipt["run_id"], receipt["attempt_id"])
    payload = AssistantAgent(EvidenceRepository.empty(case, claim.snapshot_id),
        Decisions(("chat", {"answer": "你好"}))).ask("你好")["payload"]
    payload["source_reads"] = {f"evidence-{index}": "原文" for index in range(97)}
    with pytest.raises(RepositoryError, match="assistant_source_changed"):
        runs.finish(claim.run_id, claim.active_attempt_id, claim.fence, claim.snapshot_id,
            RunStatus.PARTIAL, answer_text=payload["answer"], assistant_payload=payload)
    with engine.connect() as connection:
        assert connection.scalar(select(assistant_runs.c.payload_json)) is None
        assert connection.scalar(select(agent_runs.c.status)) == "running"
