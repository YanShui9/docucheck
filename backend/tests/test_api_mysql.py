"""Opt-in HTTP integration check using the migrated local MySQL database."""

import os
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, delete, insert, select, update

from backend.app.api.main import create_app
from backend.app.db.connection import database_url
from backend.app.db.models import (
    agent_runs,
    cases,
    conversations,
    idempotency_records,
    input_snapshots,
    messages,
    outbox,
    run_attempts,
    run_events,
)


def _clean(engine, case_id: str, conversation_id: str | None,
           snapshot_id: str | None) -> None:
    with engine.begin() as connection:
        run_ids = connection.scalars(select(agent_runs.c.id).where(
            agent_runs.c.case_id == case_id)).all()
        if run_ids:
            for table in (run_events, outbox, run_attempts):
                connection.execute(delete(table).where(table.c.run_id.in_(run_ids)))
            connection.execute(delete(agent_runs).where(agent_runs.c.id.in_(run_ids)))
        connection.execute(delete(idempotency_records).where(
            idempotency_records.c.operation_scope == f"case:{case_id}:message"))
        if conversation_id:
            connection.execute(delete(messages).where(
                messages.c.conversation_id == conversation_id))
            connection.execute(delete(conversations).where(
                conversations.c.id == conversation_id))
        if snapshot_id:
            connection.execute(delete(input_snapshots).where(
                input_snapshots.c.id == snapshot_id))
        connection.execute(delete(cases).where(cases.c.id == case_id))


def test_mysql_http_submission_replay_and_run_visibility():
    if os.getenv("DOCUCHECK_RUN_MYSQL_TESTS") != "1":
        pytest.skip("set DOCUCHECK_RUN_MYSQL_TESTS=1 for live MySQL API check")
    engine = create_engine(database_url(), pool_pre_ping=True, hide_parameters=True)
    case_id = conversation_id = snapshot_id = None
    try:
        with TestClient(create_app(engine)) as client:
            assert client.get("/health/ready").status_code == 200
            case = client.post("/api/v1/cases")
            assert case.status_code == 201
            case_id = case.json()["id"]
            conversation = client.post(
                f"/api/v1/cases/{case_id}/conversations",
            )
            assert conversation.status_code == 201
            conversation_id = conversation.json()["id"]
            snapshot_id, now = uuid4().hex, datetime.now(UTC).replace(tzinfo=None)
            with engine.begin() as connection:
                connection.execute(insert(input_snapshots).values(
                    id=snapshot_id, case_id=case_id, revision=1,
                    created_at=now,
                ))
                connection.execute(update(cases).where(cases.c.id == case_id)
                                   .values(revision=1, updated_at=now))

            url = f"/api/v1/conversations/{conversation_id}/messages"
            headers = {"Idempotency-Key": "mysql-api-1"}
            body = {"content": "检查供应商 A", "mode": "standard",
                    "expected_revision": 1}
            first = client.post(url, headers=headers, json=body)
            assert first.status_code == 202
            second = client.post(url, headers=headers, json=body)
            assert second.status_code == 202
            assert second.json() == first.json()
            run_id = first.json()["run_id"]
            run = client.get(f"/api/v1/agent-runs/{run_id}")
            assert run.status_code == 200
            assert run.json()["status"] == "queued"
            events = client.get(f"/api/v1/agent-runs/{run_id}/events")
            assert events.status_code == 200
            assert [item["seq"] for item in events.json()["events"]] == [1]
            cancelled = client.post(f"/api/v1/agent-runs/{run_id}/cancel")
            assert cancelled.status_code == 200
            assert cancelled.json()["status"] == "cancelled"
    finally:
        try:
            if case_id:
                _clean(engine, case_id, conversation_id, snapshot_id)
        finally:
            engine.dispose()
