"""Context diagnostics use the existing fenced, authorized Run transaction."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, delete, func, select, update

from backend.app.agent.context_usage import reserve_with_context
from backend.app.agent.policy import Budget, RunStopped
from backend.app.api.main import create_app
from backend.app.db.models import (
    agent_runs,
    cases,
    metadata,
    project_members,
    run_attempts,
    run_events,
)
from backend.app.db.run_executor import execute_run
from backend.app.db.run_repository import RepositoryError
from backend.app.runtime.lifecycle import RunStatus
from backend.tests import (
    test_assistant_workflow,
    test_document_ingestion,
    test_dynamic_workflow,
    test_run_repository,
)
from backend.tests.test_api_workspace_authorization import DemoAuthenticator
from backend.tests.test_assistant_agent import Decisions
from backend.tests.test_dynamic_agent import ScriptProvider

dynamic_case = test_dynamic_workflow.dynamic_case
ingestion_case = test_document_ingestion.ingestion_case
repository = test_run_repository.repository


@pytest.mark.parametrize("operation", ["assistant", "review"])
def test_executor_persists_local_rejection_and_history_refresh(dynamic_case, operation):
    client, engine, _, _, _ = dynamic_case
    if operation == "review":
        test_dynamic_workflow.confirm(dynamic_case)
        run_id = test_dynamic_workflow.message(dynamic_case, key="context-review")
        attempt_id = client.get(f"/api/v1/agent-runs/{run_id}").json()["active_attempt_id"]
    else:
        receipt = test_assistant_workflow.send(dynamic_case, "苹果采购要求是什么")
        run_id, attempt_id = receipt["run_id"], receipt["attempt_id"]
    provider = ScriptProvider()
    provider.context_window = 700
    result = execute_run(engine, run_id, attempt_id, provider, heartbeat_interval_seconds=60)
    assert result.status == "partial" and result.error_code == "context_budget"
    assert result.model_calls == 0 and provider.calls == []
    prefix = f"/api/v1/agent-runs/{run_id}"
    for _ in range(2):
        response = client.get(prefix + "/events")
        assert response.status_code == 200, response.text
        diagnostic = next(event for event in response.json()["events"] if event["kind"] == "agent_model")
        summary = diagnostic["summary"]
        assert summary["status"] == "rejected" and summary["error"] == "context_budget"
        assert summary["provider_called"] is False
        assert summary["context"]["model_window_tokens"] == 700
        assert summary["context"]["exact_tokens"] is False
        assert "苹果" not in str(summary)
    with engine.connect() as connection:
        usage = connection.scalar(select(run_attempts.c.usage_json).where(run_attempts.c.id == attempt_id))
        assert usage["model_calls"] == usage["unknown_usage_calls"] == 0
    if operation == "assistant":
        assert client.get(prefix + "/assistant-result").status_code == 200


def rejected_events():
    events = []
    with pytest.raises(RunStopped, match="context_budget"):
        reserve_with_context(Budget(max_prompt=1), [], [], phase="extract", output_cap=512, events=events)
    return events


@pytest.mark.parametrize("change", ["cancel", "snapshot"])
def test_stale_attempt_or_snapshot_cannot_publish_context_diagnostics(repository, change):
    receipt = test_run_repository._submit(repository)
    claim = repository.claim(receipt.run_id, receipt.attempt_id)
    if change == "cancel":
        repository.cancel(receipt.run_id)
    else:
        with repository.engine.begin() as connection:
            connection.execute(update(cases).where(cases.c.id == "case-a").values(revision=2))
    with repository.engine.connect() as connection:
        before = connection.scalar(select(func.count()).select_from(run_events))
    with pytest.raises(RepositoryError, match="stale_attempt|snapshot_changed"):
        repository.finish(receipt.run_id, receipt.attempt_id, claim.fence, claim.snapshot_id,
                          RunStatus.PARTIAL, error_code="context_budget", trace_events=rejected_events())
    with repository.engine.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(run_events)) == before
        assert connection.scalar(select(agent_runs.c.answer_text)) is None


def test_legacy_diagnostic_without_context_keeps_its_original_shape(repository):
    receipt = test_run_repository._submit(repository)
    claim = repository.claim(receipt.run_id, receipt.attempt_id)
    repository.finish(receipt.run_id, receipt.attempt_id, claim.fence, claim.snapshot_id,
        RunStatus.PARTIAL, trace_events=[{"kind": "model", "status": "failed", "operation": "extract",
                                        "error": "invalid_output"}])
    with repository.engine.connect() as connection:
        summary = connection.scalar(select(run_events.c.summary_json).where(run_events.c.kind == "agent_model"))
    assert summary == {"status": "failed", "operation": "extract", "error": "invalid_output"}


def test_revoked_access_cannot_publish_diagnostics_or_read_them(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'context-acl.db'}")
    metadata.create_all(engine)
    try:
        with TestClient(create_app(engine, authenticator=DemoAuthenticator())) as client:
            alice, bob = {"Authorization": "Bearer alice"}, {"Authorization": "Bearer bob"}
            project = client.post("/api/v1/projects", headers=alice, json={"name": "采购"}).json()
            case = client.post("/api/v1/cases", headers=alice, json={"project_id": project["id"]}).json()["id"]
            conv = client.post(f"/api/v1/cases/{case}/conversations", headers=alice).json()["id"]
            receipt = client.post(f"/api/v1/conversations/{conv}/messages",
                headers=alice | {"Idempotency-Key": "context-acl"},
                json={"content": "你好", "operation": "assistant", "expected_revision": 0}).json()
            url = f"/api/v1/agent-runs/{receipt['run_id']}/events"
            assert client.get(url, headers=bob).status_code == 404

            class Revoke(Decisions):
                def decide(self, *args, **kwargs):
                    reply = super().decide(*args, **kwargs)
                    with engine.begin() as connection:
                        connection.execute(delete(project_members).where(project_members.c.project_id == project["id"]))
                    return reply

            with pytest.raises(RepositoryError, match="source_access_changed"):
                execute_run(engine, receipt["run_id"], receipt["attempt_id"],
                            Revoke(("chat", {"answer": "不应提交"})), heartbeat_interval_seconds=60)
            with engine.connect() as connection:
                assert connection.scalar(select(agent_runs.c.status)) == "interrupted"
                assert connection.scalar(select(func.count()).select_from(run_events).where(
                    run_events.c.kind == "agent_model")) == 0
            assert client.get(url, headers=alice).status_code == 404
    finally:
        engine.dispose()
