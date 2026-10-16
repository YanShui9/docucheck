# ruff: noqa: F811 -- Imported pytest fixtures resolved by name.
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from backend.app.agent.tools import ToolExecutor
from backend.app.api.main import create_app
from backend.app.db.agent_snapshot import load_agent_snapshot
from backend.app.db.case_cleanup import CaseCleanup
from backend.app.db.ingestion import DocumentIngestion
from backend.app.db.memory_repository import MemoryRepository
from backend.app.db.models import (
    agent_runs,
    evidence_blocks,
    messages,
    run_attempts,
    task_memory,
)
from backend.app.db.run_executor import execute_run
from backend.app.db.run_repository import RepositoryError
from backend.tests.test_api_workspace_authorization import DemoAuthenticator
from backend.tests.test_index_repository import indexed_db  # noqa: F401
from backend.tests.test_persisted_agent_run import (  # noqa: F401
    SourceProvider,
    _call,
    _submit_question,
    uploaded_case,
)


def test_personal_memory_api_confirmation_versions_and_account_isolation(indexed_db):
    engine, _ = indexed_db
    client = TestClient(create_app(engine, authenticator=DemoAuthenticator()))
    alice, bob = {"Authorization": "Bearer alice"}, {"Authorization": "Bearer bob"}
    url = "/api/v1/me/memories"
    assert client.get(url).status_code == 401
    assert client.get(url, headers=alice).json() == {"revision": 0, "records": []}
    body = {"value": "concise", "expected_revision": 0, "confirmed": True}
    assert (
        client.put(url + "/answer_length", headers=alice, json=body).status_code == 200
    )
    assert (
        client.put(url + "/answer_length", headers=alice, json=body).status_code == 409
    )
    assert client.get(url, headers=bob).json()["records"] == []
    assert (
        client.put(url + "/supplier_fact", headers=alice, json=body).status_code == 422
    )
    assert (
        client.put(
            url + "/language",
            headers=bob,
            json={**body, "value": "zh-CN", "confirmed": False},
        ).status_code
        == 422
    )
    assert (
        client.delete(url + "/answer_length?expected_revision=1", headers=alice).json()[
            "revision"
        ]
        == 2
    )
    assert client.get(url, headers=alice).json()["records"] == []


def test_production_agent_persists_context_manifest_and_task_checkpoint(uploaded_case):
    client, engine, storage, case_id, uploaded = uploaded_case
    DocumentIngestion(engine, storage).process_document(
        case_id, uploaded["snapshot_id"], uploaded["document_id"]
    )
    url = "/api/v1/me/memories/answer_length"
    assert (
        client.put(
            url, json={"value": "concise", "expected_revision": 0, "confirmed": True}
        ).status_code
        == 200
    )
    with engine.connect() as conn:
        evidence_id = conn.scalar(
            select(evidence_blocks.c.id).where(evidence_blocks.c.cell == "B6")
        )
    submitted, conv = _submit_question(client, case_id)

    class Provider(SourceProvider):
        def decide(self, inputs, tools, **kwargs):
            assert "concise" in inputs[0].content
            assert any(
                t["function"]["name"] == "read_conversation_history" for t in tools
            )
            return super().decide(inputs, tools, **kwargs)

    report = execute_run(
        engine, submitted["run_id"], submitted["attempt_id"], Provider(evidence_id)
    )
    assert report.status == "completed"
    with engine.connect() as conn:
        run = conn.execute(select(agent_runs)).mappings().one()
        task = conn.execute(select(task_memory)).mappings().one()
        sequence = conn.scalar(select(messages.c.sequence))
    assert sequence == 1 and task["source_run_id"] == report.run_id
    assert run["context_manifest_json"]["preference_revision"] == 1
    assert "concise" not in str(run["context_manifest_json"])
    response = client.get(f"/api/v1/conversations/{conv}/memory")
    assert (
        response.status_code == 200
        and response.json()["task"]["source_run_id"] == report.run_id
    )
    repo = load_agent_snapshot(engine, case_id, uploaded["snapshot_id"])
    executor = ToolExecutor(
        repo,
        history_reader=lambda ids, **kw: MemoryRepository(engine).read_history(
            case_id, conv, uploaded["snapshot_id"], ids, **kw
        ),
    )
    result = executor.execute(
        "read_conversation_history",
        {"message_ids": [submitted["message_id"]], "part": "answer"},
        None,
    )
    assert result["evidence_refs"] == [] and result["data"][0]["is_historical"]
    with pytest.raises(ValueError, match="history_scope"):
        executor.execute(
            "read_conversation_history",
            {"message_ids": ["foreign"], "part": "answer"},
            None,
        )
    assert (
        client.delete(f"/api/v1/cases/{case_id}?expected_revision=1").status_code == 202
    )
    cleaner = CaseCleanup(engine, storage)
    claim = cleaner.claim(case_id=case_id)
    assert claim and cleaner.process(claim)["status"] == "deleted"
    with engine.connect() as conn:
        assert conn.execute(select(task_memory)).first() is None
    assert client.get("/api/v1/me/memories").json()["records"][0]["value"] == "concise"


@pytest.mark.parametrize("change_on", [1, 3])
def test_deleting_memory_during_run_blocks_next_call_or_final_write(
    uploaded_case, change_on
):
    client, engine, storage, case_id, uploaded = uploaded_case
    DocumentIngestion(engine, storage).process_document(
        case_id, uploaded["snapshot_id"], uploaded["document_id"]
    )
    assert (
        client.put(
            "/api/v1/me/memories/answer_length",
            json={"value": "concise", "expected_revision": 0, "confirmed": True},
        ).status_code
        == 200
    )
    with engine.connect() as conn:
        evidence_id = conn.scalar(
            select(evidence_blocks.c.id).where(evidence_blocks.c.cell == "B6")
        )
    submitted, _ = _submit_question(client, case_id)

    class Provider(SourceProvider):
        def decide(self, inputs, tools, **kwargs):
            response = super().decide(inputs, tools, **kwargs)
            if self.calls == change_on:
                assert (
                    client.delete(
                        "/api/v1/me/memories/answer_length?expected_revision=1"
                    ).status_code
                    == 200
                )
            return response

    provider = Provider(evidence_id)
    with pytest.raises(RepositoryError, match="memory_context_changed"):
        execute_run(engine, submitted["run_id"], submitted["attempt_id"], provider)
    assert provider.calls == change_on
    with engine.connect() as conn:
        row = conn.execute(select(agent_runs)).mappings().one()
        assert row["status"] == "interrupted" and row["answer_text"] is None
        usage = conn.scalar(select(run_attempts.c.usage_json))
        assert usage["model_calls"] == change_on
        assert conn.execute(select(task_memory)).first() is None
    assert client.get("/api/v1/me/memories").json()["records"] == []


def test_clarification_reply_survives_retry_completion_and_historical_read_is_audited(
    uploaded_case,
):
    client, engine, storage, case_id, uploaded = uploaded_case
    DocumentIngestion(engine, storage).process_document(
        case_id, uploaded["snapshot_id"], uploaded["document_id"]
    )
    with engine.connect() as conn:
        evidence_id = conn.scalar(
            select(evidence_blocks.c.id).where(evidence_blocks.c.cell == "B6")
        )
    submitted, conv = _submit_question(client, case_id)

    class Ask:
        context_window = 32768

        def decide(self, *args, **kwargs):
            return _call("request_clarification", {"question": "请补充本轮的限制。"})

    assert (
        execute_run(engine, submitted["run_id"], submitted["attempt_id"], Ask()).status
        == "waiting_user"
    )
    reply_text = "供应商 A，只关注报价中的内存原文"
    resumed = client.post(
        f"/api/v1/agent-runs/{submitted['run_id']}/resume",
        headers={"Idempotency-Key": "memory-resume"},
        json={"content": reply_text, "expected_revision": 1},
    ).json()

    class Interrupted:
        context_window = 32768

        def decide(self, inputs, *args, **kwargs):
            assert inputs[-1].content == reply_text
            raise RuntimeError("worker interrupted")

    with pytest.raises(RuntimeError, match="worker interrupted"):
        execute_run(engine, submitted["run_id"], resumed["attempt_id"], Interrupted())
    retry = client.post(
        f"/api/v1/agent-runs/{submitted['run_id']}/retry",
        headers={"Idempotency-Key": "memory-retry"},
        json={"expected_revision": 1},
    ).json()

    class Retry(SourceProvider):
        def decide(self, inputs, tools, **kwargs):
            if not self.calls:
                assert inputs[-1].content == reply_text
            return super().decide(inputs, tools, **kwargs)

    assert (
        execute_run(
            engine, submitted["run_id"], retry["attempt_id"], Retry(evidence_id)
        ).status
        == "completed"
    )
    with engine.connect() as conn:
        reply_id = conn.scalar(
            select(run_attempts.c.resume_message_id).where(
                run_attempts.c.id == resumed["attempt_id"]
            )
        )
    assert (
        MemoryRepository(engine).read_history(
            case_id, conv, uploaded["snapshot_id"], [reply_id], part="question"
        )[0]["text"]
        == reply_text
    )
    next_run = client.post(
        f"/api/v1/conversations/{conv}/messages",
        headers={"Idempotency-Key": "memory-followup"},
        json={"content": "继续读供应商 A 的内存报价原文", "expected_revision": 1},
    ).json()

    class Followup(SourceProvider):
        inspected = False

        def decide(self, inputs, tools, **kwargs):
            if not self.inspected:
                self.inspected = True
                # The complete original reply and its readback handle travel
                # together; message.id alone is not sent to the provider.
                import json

                historical = [json.loads(m.content.removeprefix("HISTORY_DATA=")) for m in inputs
                              if m.type == "human" and m.content.startswith("HISTORY_DATA=")]
                assert {"message_id": reply_id, "question": reply_text} in historical
                return _call(
                    "read_conversation_history",
                    {"message_ids": [reply_id], "part": "question"},
                )
            return super().decide(inputs, tools, **kwargs)

    assert (
        execute_run(
            engine, next_run["run_id"], next_run["attempt_id"], Followup(evidence_id)
        ).status
        == "completed"
    )
    with engine.connect() as conn:
        manifest = conn.scalar(
            select(agent_runs.c.context_manifest_json).where(
                agent_runs.c.id == next_run["run_id"]
            )
        )
    assert manifest["history_reads"][0]["message_id"] == reply_id
    assert manifest["history_reads"][0]["part"] == "question"
    assert reply_text not in str(manifest)
