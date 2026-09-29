"""Opt-in MySQL acceptance for one uploaded-evidence Agent Run."""

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage
from sqlalchemy import create_engine, delete, select

from backend.app.api.main import create_app
from backend.app.db.connection import database_url
from backend.app.db.ingestion import DocumentIngestion
from backend.app.db.models import (
    agent_runs,
    cases,
    conversations,
    document_versions,
    evidence_blocks,
    idempotency_records,
    input_snapshots,
    messages,
    outbox,
    run_attempts,
    run_events,
    suppliers,
)
from backend.app.db.run_executor import execute_run

EXAMPLES = Path(__file__).resolve().parents[2] / "examples" / "dev-pack-001"


class SourceProvider:
    model_id = "scripted-mysql-provider"
    context_window = 32_768

    def __init__(self, evidence_id):
        self.evidence_id = evidence_id
        self.calls = 0

    def decide(self, messages, tools, **kwargs):
        self.calls += 1
        if self.calls == 1:
            name, args = "search_evidence", {"query": "已装内存", "supplier": "vendor-a"}
        elif self.calls == 2:
            name, args = "read_source", {"evidence_ids": [self.evidence_id]}
        else:
            name, args = "submit_answer", {"kind": "evidence",
                                           "evidence_ids": [self.evidence_id]}
        return AIMessage(content="", tool_calls=[{
            "id": "mysql-call", "name": name, "args": args,
        }], usage_metadata={"input_tokens": 100, "output_tokens": 20,
                           "total_tokens": 120})


def test_mysql_uploaded_evidence_completes_one_agent_run(tmp_path, monkeypatch):
    if os.getenv("DOCUCHECK_RUN_MYSQL_TESTS") != "1":
        pytest.skip("set DOCUCHECK_RUN_MYSQL_TESTS=1 for live MySQL Agent check")
    storage = tmp_path / "data"
    monkeypatch.setenv("DOCUCHECK_DATA_DIR", str(storage))
    engine = create_engine(database_url(), pool_pre_ping=True,
                           hide_parameters=True)
    case_id = None
    try:
        with TestClient(create_app(engine)) as client:
            case_id = client.post("/api/v1/cases").json()["id"]
            uploaded = client.post(
                f"/api/v1/cases/{case_id}/documents",
                headers={"Idempotency-Key": "mysql-agent-upload"},
                data={"role": "quote", "supplier_handle": "vendor-a",
                      "supplier_name": "Vendor A", "expected_revision": "0"},
                files={"file": ("vendor-a-quote.xlsx",
                                (EXAMPLES / "vendor-a-quote.xlsx").read_bytes(),
                                "application/octet-stream")},
            )
            assert uploaded.status_code == 201, uploaded.text
            receipt = uploaded.json()
            ingested = DocumentIngestion(engine, storage).process_document(
                case_id, receipt["snapshot_id"], receipt["document_id"])
            assert ingested["parse_status"] == "complete"
            with engine.connect() as connection:
                evidence_id = connection.scalar(select(evidence_blocks.c.id).where(
                    evidence_blocks.c.document_version_id == receipt["document_id"],
                    evidence_blocks.c.cell == "B6",
                ))
            conversation = client.post(f"/api/v1/cases/{case_id}/conversations")
            assert conversation.status_code == 201
            submitted = client.post(
                f"/api/v1/conversations/{conversation.json()['id']}/messages",
                headers={"Idempotency-Key": "mysql-agent-question"},
                json={"content": "请列出供应商 A 报价中的内存原文",
                      "mode": "standard", "expected_revision": 1},
            )
            assert submitted.status_code == 202, submitted.text
            run = submitted.json()
            provider = SourceProvider(evidence_id)
            result = execute_run(engine, run["run_id"], run["attempt_id"],
                                 provider)
            assert result.status == "completed"
            assert "B6" in result.answer_text and "16 GB" in result.answer_text
            assert client.get(f"/api/v1/agent-runs/{run['run_id']}").json()[
                "status"] == "completed"
            followup = client.post(
                f"/api/v1/conversations/{conversation.json()['id']}/messages",
                headers={"Idempotency-Key": "mysql-agent-followup"},
                json={"content": "刚才那条来源在哪里？", "mode": "standard",
                      "expected_revision": 1},
            )
            assert followup.status_code == 202, followup.text

            class FollowupProvider(SourceProvider):
                def decide(self, messages, tools, **kwargs):
                    if self.calls == 0:
                        contents = [str(item.content) for item in messages]
                        assert any("16 GB" in item for item in contents)
                        assert any("供应商 A" in item for item in contents)
                    return super().decide(messages, tools, **kwargs)

            next_run = followup.json()
            second = execute_run(engine, next_run["run_id"], next_run["attempt_id"],
                                 FollowupProvider(evidence_id))
            assert second.status == "completed" and "B6" in second.answer_text
            clarify = client.post(
                f"/api/v1/conversations/{conversation.json()['id']}/messages",
                headers={"Idempotency-Key": "mysql-agent-clarify"},
                json={"content": "请继续核对来源", "mode": "standard",
                      "expected_revision": 1},
            )
            assert clarify.status_code == 202, clarify.text

            class ClarifyProvider:
                model_id = "scripted-clarify"
                context_window = 32_768

                def decide(self, messages, tools, **kwargs):
                    return AIMessage(content="", tool_calls=[{
                        "id": "clarify-call", "name": "request_clarification",
                        "args": {"question": "请确认供应商。"},
                    }], usage_metadata={"input_tokens": 100, "output_tokens": 20,
                                       "total_tokens": 120})

            clarify_run = clarify.json()
            waiting = execute_run(engine, clarify_run["run_id"],
                                  clarify_run["attempt_id"], ClarifyProvider())
            assert waiting.status == "waiting_user"
            resumed = client.post(
                f"/api/v1/agent-runs/{clarify_run['run_id']}/resume",
                headers={"Idempotency-Key": "mysql-agent-resume"},
                json={"content": "供应商 A", "expected_revision": 1},
            )
            assert resumed.status_code == 202, resumed.text
            resumed_result = execute_run(engine, clarify_run["run_id"],
                                         resumed.json()["attempt_id"],
                                         SourceProvider(evidence_id))
            assert resumed_result.status == "completed"
            assert "16 GB" in resumed_result.answer_text
    finally:
        try:
            if case_id:
                with engine.begin() as connection:
                    ids = connection.scalars(select(agent_runs.c.id).where(
                        agent_runs.c.case_id == case_id)).all()
                    if ids:
                        attempt_ids = connection.scalars(select(run_attempts.c.id).where(
                            run_attempts.c.run_id.in_(ids))).all()
                        if attempt_ids:
                            connection.execute(delete(idempotency_records).where(
                                idempotency_records.c.resource_type == "attempt",
                                idempotency_records.c.resource_id.in_(attempt_ids)))
                        connection.execute(delete(run_events).where(
                            run_events.c.run_id.in_(ids)))
                        connection.execute(delete(outbox).where(
                            outbox.c.run_id.in_(ids)))
                        connection.execute(delete(run_attempts).where(
                            run_attempts.c.run_id.in_(ids)))
                        connection.execute(delete(agent_runs).where(
                            agent_runs.c.id.in_(ids)))
                    connection.execute(delete(messages).where(
                        messages.c.conversation_id.in_(select(conversations.c.id).where(
                            conversations.c.case_id == case_id))))
                    connection.execute(delete(conversations).where(
                        conversations.c.case_id == case_id))
                    connection.execute(delete(idempotency_records).where(
                        idempotency_records.c.operation_scope.in_([
                            f"case:{case_id}:upload", f"case:{case_id}:message",
                        ])))
                    document_ids = connection.scalars(select(document_versions.c.id).where(
                        document_versions.c.case_id == case_id)).all()
                    if document_ids:
                        connection.execute(delete(evidence_blocks).where(
                            evidence_blocks.c.document_version_id.in_(document_ids)))
                    connection.execute(delete(document_versions).where(
                        document_versions.c.case_id == case_id))
                    connection.execute(delete(input_snapshots).where(
                        input_snapshots.c.case_id == case_id))
                    connection.execute(delete(suppliers).where(
                        suppliers.c.case_id == case_id))
                    connection.execute(delete(cases).where(cases.c.id == case_id))
        finally:
            engine.dispose()
