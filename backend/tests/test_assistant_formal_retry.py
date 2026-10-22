"""Explicit retries keep a server-persisted formal operation within the same Run."""

from dataclasses import replace

import pytest
from sqlalchemy import delete, insert, select, update

from backend.app.db.assistant_repository import AssistantRepository
from backend.app.db.models import (
    agent_runs,
    assistant_runs,
    cases,
    dynamic_runs,
    projects,
    run_events,
)
from backend.app.db.run_repository import RepositoryError, RunRepository
from backend.app.providers.base import ProviderError
from backend.tests import (
    test_assistant_workflow,
    test_document_ingestion,
    test_dynamic_workflow,
)
from backend.tests.test_assistant_agent import Decisions
from backend.tests.test_dynamic_agent import ScriptProvider, call

ingestion_case = test_document_ingestion.ingestion_case
dynamic_case = test_dynamic_workflow.dynamic_case


class FailLastBatch(ScriptProvider):
    def extract(self, *args, **kwargs):
        if self.extractions:
            raise ProviderError("rate_limit")
        return super().extract(*args, **kwargs)


class ChatOnOuterRouting(ScriptProvider):
    """A second routing request would lose intent; the dynamic decision stays valid."""

    outer_calls = 0

    def decide(self, messages, tools, **options):
        if any(tool["function"]["name"] == "chat" for tool in tools):
            self.outer_calls += 1
            return call("chat", {"answer": "可以先说明下一步需求。"})
        return super().decide(messages, tools, **options)


@pytest.mark.parametrize("operation", ["review", "proposal"])
def test_explicit_retry_keeps_persisted_formal_operation(dynamic_case, operation):
    if operation == "review":
        test_dynamic_workflow.confirm(dynamic_case)
        first = FailLastBatch()
    else:
        first = ScriptProvider(decision="propose_checklist", failure=ProviderError("rate_limit"))
    receipt = test_assistant_workflow.send(dynamic_case, "正式审查" if operation == "review" else "整理买方清单")
    assert test_assistant_workflow.run(dynamic_case, receipt, first).status == "partial"
    url = f"/api/v1/agent-runs/{receipt['run_id']}"
    with dynamic_case[1].connect() as connection:
        assert connection.scalar(select(dynamic_runs.c.workflow).where(dynamic_runs.c.run_id == receipt["run_id"])) == operation
    retry = dynamic_case[0].post(url + "/retry", headers={"Idempotency-Key": "formal-retry"},
        json={"expected_revision": 3})
    assert retry.status_code == 202, retry.text
    provider = ChatOnOuterRouting(decision="review_checklist" if operation == "review" else "propose_checklist")
    report = test_assistant_workflow.run(dynamic_case, retry.json(), provider)
    assert report.status == "completed", report
    assert provider.outer_calls == 0
    assert provider.extractions == 1  # Review reuses the complete first parent batch.
    result = dynamic_case[0].get(url + "/assistant-result")
    assert result.status_code == 200, result.text
    assert result.json()["result"]["kind"] == operation
    if operation == "review":
        checks = dynamic_case[0].get(url + "/checks").json()["checks"]
        assert len(checks) == 2 and all(check["is_formal"] for check in checks)
        assert dynamic_case[0].get(url + "/export").status_code == 200
    else:
        assert result.json()["result"]["candidate"]["criteria"]
        assert dynamic_case[0].get(url + "/proposal").status_code == 200
        assert dynamic_case[0].get(url + "/checks").json()["checks"] == []


def test_retry_after_new_resume_instruction_does_not_revive_old_formal_operation(dynamic_case):
    receipt = test_assistant_workflow.send(dynamic_case, "整理买方清单")
    first = ScriptProvider(decision="propose_checklist", failure=ProviderError("rate_limit"))
    assert test_assistant_workflow.run(dynamic_case, receipt, first).status == "partial"
    url = f"/api/v1/agent-runs/{receipt['run_id']}"
    client = dynamic_case[0]
    retry = client.post(url + "/retry", headers={"Idempotency-Key": "proposal-wait"},
        json={"expected_revision": 3})
    assert retry.status_code == 202, retry.text
    waiting = ScriptProvider(decision="propose_checklist", proposal_rule="lte")
    assert test_assistant_workflow.run(dynamic_case, retry.json(), waiting).status == "waiting_user"
    resume = client.post(url + "/resume", headers={"Idempotency-Key": "new-instruction"},
        json={"expected_revision": 3, "content": "先聊一下下一步"})
    assert resume.status_code == 202, resume.text
    invalid_chat = Decisions(("execute", {"actor": "admin"}))
    assert test_assistant_workflow.run(dynamic_case, resume.json(), invalid_chat).status == "partial"
    retry = client.post(url + "/retry", headers={"Idempotency-Key": "chat-retry"},
        json={"expected_revision": 3})
    assert retry.status_code == 202, retry.text
    provider = ChatOnOuterRouting(decision="propose_checklist")
    report = test_assistant_workflow.run(dynamic_case, retry.json(), provider)
    assert report.status == "completed", report
    assert provider.outer_calls == 1
    assert provider.extractions == 0
    result = client.get(url + "/assistant-result")
    assert result.status_code == 200, result.text
    assert result.json()["result"]["kind"] == "chat"
    assert client.get(url + "/checks").json()["checks"] == []
    assert client.get(url + "/export").status_code == 409


def test_retry_preserves_formal_operation_registered_after_resume(dynamic_case):
    receipt = test_assistant_workflow.send(dynamic_case, "整理买方清单")
    waiting = ScriptProvider(decision="propose_checklist", proposal_rule="lte")
    assert test_assistant_workflow.run(dynamic_case, receipt, waiting).status == "waiting_user"
    url = f"/api/v1/agent-runs/{receipt['run_id']}"
    resume = dynamic_case[0].post(url + "/resume", headers={"Idempotency-Key": "formal-resume"},
        json={"expected_revision": 3, "content": "重新整理清单"})
    assert resume.status_code == 202, resume.text
    failed = ScriptProvider(decision="propose_checklist", failure=ProviderError("rate_limit"))
    assert test_assistant_workflow.run(dynamic_case, resume.json(), failed).status == "partial"
    retry = dynamic_case[0].post(url + "/retry", headers={"Idempotency-Key": "resumed-formal-retry"},
        json={"expected_revision": 3})
    assert retry.status_code == 202, retry.text
    provider = ChatOnOuterRouting(decision="propose_checklist")
    assert test_assistant_workflow.run(dynamic_case, retry.json(), provider).status == "completed"
    assert provider.outer_calls == 0
    assert provider.extractions == 1
    result = dynamic_case[0].get(url + "/assistant-result")
    assert result.status_code == 200, result.text
    assert result.json()["result"]["kind"] == "proposal"


def test_register_formal_rejects_unknown_operation(dynamic_case):
    test_dynamic_workflow.confirm(dynamic_case)
    receipt = test_assistant_workflow.send(dynamic_case, "正式审查")
    claim = RunRepository(dynamic_case[1]).claim(receipt["run_id"], receipt["attempt_id"])
    with pytest.raises(RepositoryError, match="invalid_request"):
        AssistantRepository(dynamic_case[1]).register_formal(claim, "unknown")


def test_model_trace_cannot_register_formal_intent():
    from backend.app.agent.diagnostics import safe_trace_events

    assert safe_trace_events([{"kind": "assistant_formal_registered",
        "summary": {"version": 1, "operation": "review"}}]) == []


def test_first_attempt_never_restores_a_formal_operation(dynamic_case):
    test_dynamic_workflow.confirm(dynamic_case)
    receipt = test_assistant_workflow.send(dynamic_case, "正式审查")
    claim = RunRepository(dynamic_case[1]).claim(receipt["run_id"], receipt["attempt_id"])
    store = AssistantRepository(dynamic_case[1])
    store.register_formal(claim, "review")
    assert store.retry_operation(claim) is None


@pytest.mark.parametrize("operation", ["review", "proposal"])
@pytest.mark.parametrize("legacy_without_marker", [False, True])
def test_retry_recovers_registered_formal_operation_before_any_result_was_saved(
        dynamic_case, operation, legacy_without_marker):
    if operation == "review":
        test_dynamic_workflow.confirm(dynamic_case)
    receipt = test_assistant_workflow.send(dynamic_case, "继续原操作")
    engine = dynamic_case[1]
    runs = RunRepository(engine)
    claim = runs.claim(receipt["run_id"], receipt["attempt_id"])
    AssistantRepository(engine).register_formal(claim, operation)
    with engine.begin() as connection:
        summary = connection.scalar(select(run_events.c.summary_json).where(
            run_events.c.run_id == claim.run_id, run_events.c.kind == "assistant_formal_registered"))
        assert summary == {"version": 1, "operation": operation}
        if legacy_without_marker:
            connection.execute(delete(run_events).where(run_events.c.run_id == claim.run_id,
                run_events.c.kind == "assistant_formal_registered"))
    # A lost worker can leave a trusted binding without an assistant payload.
    runs.interrupt(claim.run_id, claim.active_attempt_id, claim.fence)
    url = f"/api/v1/agent-runs/{receipt['run_id']}"
    retry = dynamic_case[0].post(url + "/retry", headers={"Idempotency-Key": "worker-lost-retry"},
        json={"expected_revision": 3})
    assert retry.status_code == 202, retry.text
    provider = ChatOnOuterRouting(decision="review_checklist" if operation == "review" else "propose_checklist")
    assert test_assistant_workflow.run(dynamic_case, retry.json(), provider).status == "completed"
    assert provider.outer_calls == 0
    result = dynamic_case[0].get(url + "/assistant-result")
    assert result.status_code == 200, result.text
    assert result.json()["result"]["kind"] == operation


@pytest.mark.parametrize("tamper, error", [
    ("attempt", "stale_attempt"),
    ("fence", "stale_attempt"),
    ("snapshot", "stale_attempt"),
    ("revision", "snapshot_changed"),
    ("checklist", "checklist_changed"),
    ("definition", "checklist_changed"),
    ("item_scope", "item_binding_source_changed"),
    ("permission", "source_access_changed"),
    ("marker_operation", "dynamic_scope"),
    ("marker_workflow", "dynamic_scope"),
    ("marker_version", "dynamic_scope"),
])
def test_formal_retry_revalidates_authority(dynamic_case, tamper, error):
    test_dynamic_workflow.confirm(dynamic_case)
    receipt = test_assistant_workflow.send(dynamic_case, "正式审查")
    assert test_assistant_workflow.run(dynamic_case, receipt, FailLastBatch()).status == "partial"
    url = f"/api/v1/agent-runs/{receipt['run_id']}"
    retry = dynamic_case[0].post(url + "/retry", headers={"Idempotency-Key": "guard-retry"},
        json={"expected_revision": 3})
    assert retry.status_code == 202, retry.text
    engine = dynamic_case[1]
    claim = RunRepository(engine).claim(receipt["run_id"], retry.json()["attempt_id"])
    if tamper == "attempt":
        claim = replace(claim, active_attempt_id=receipt["attempt_id"])
    elif tamper == "fence":
        claim = replace(claim, fence=claim.fence - 1)
    elif tamper == "snapshot":
        claim = replace(claim, snapshot_id="different-snapshot")
    else:
        with engine.begin() as connection:
            if tamper == "revision":
                connection.execute(update(cases).where(cases.c.id == dynamic_case[2]).values(revision=4))
            elif tamper == "checklist":
                connection.execute(update(assistant_runs).where(assistant_runs.c.run_id == claim.run_id)
                    .values(checklist_revision=999))
            elif tamper == "definition":
                connection.execute(update(dynamic_runs).where(dynamic_runs.c.run_id == claim.run_id)
                    .values(definition_digest="0" * 64))
            elif tamper == "item_scope":
                connection.execute(update(dynamic_runs).where(dynamic_runs.c.run_id == claim.run_id)
                    .values(item_binding_digest="0" * 64))
            elif tamper.startswith("marker_"):
                summary = {"version": 1, "operation": "review"}
                if tamper == "marker_operation":
                    summary["operation"] = "unknown"
                elif tamper == "marker_workflow":
                    summary["operation"] = "proposal"
                else:
                    summary["version"] = True
                connection.execute(update(run_events).where(run_events.c.run_id == claim.run_id,
                    run_events.c.kind == "assistant_formal_registered").values(summary_json=summary))
            else:
                created = connection.scalar(select(cases.c.created_at).where(cases.c.id == dynamic_case[2]))
                connection.execute(insert(projects).values(id="private-project", name="private", created_at=created))
                connection.execute(update(cases).where(cases.c.id == dynamic_case[2])
                    .values(project_id="private-project"))
    with pytest.raises(RepositoryError, match=error):
        AssistantRepository(engine).retry_operation(claim)
    with engine.connect() as connection:
        assert connection.scalar(select(agent_runs.c.status).where(agent_runs.c.id == claim.run_id)) == "running"
