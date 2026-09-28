"""Opt-in MySQL/Redis/Celery smoke test; its empty snapshot never calls a model."""

import os
import time
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from dotenv import load_dotenv
from sqlalchemy import create_engine, delete, insert, select

from backend.app.db.connection import ROOT, database_url
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
from backend.app.db.run_repository import RunRepository, SubmitRun


@pytest.mark.skipif(os.getenv("DOCUCHECK_RUN_WORKER_TESTS") != "1",
                    reason="explicit MySQL/Redis/Celery opt-in")
def test_committed_outbox_reaches_linux_worker_without_model_call():
    load_dotenv(ROOT / ".env", override=False)
    engine = create_engine(database_url(), pool_pre_ping=True,
                           hide_parameters=True)
    marker = uuid4().hex
    case_id, snapshot_id, conversation_id = (
        uuid4().hex, uuid4().hex, uuid4().hex)
    now = datetime.now(UTC).replace(tzinfo=None)
    receipt = None
    try:
        with engine.begin() as connection:
            connection.execute(insert(cases).values(
                id=case_id, revision=1, created_at=now, updated_at=now,
            ))
            connection.execute(insert(input_snapshots).values(
                id=snapshot_id, case_id=case_id, revision=1, created_at=now,
            ))
            connection.execute(insert(conversations).values(
                id=conversation_id, case_id=case_id, created_at=now,
            ))
        receipt = RunRepository(engine).submit(SubmitRun(
            case_id=case_id, conversation_id=conversation_id,
            snapshot_id=snapshot_id, expected_revision=1,
            content="检查临时空快照", mode="standard",
            idempotency_key=f"worker-smoke-{marker}",
        ))
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            with engine.connect() as connection:
                run = connection.execute(select(
                    agent_runs.c.status, agent_runs.c.error_code,
                ).where(agent_runs.c.id == receipt.run_id)).one()
                delivery = connection.scalar(select(outbox.c.status).where(
                    outbox.c.run_id == receipt.run_id,
                ))
            if run.status == "partial" and delivery == "delivered":
                break
            time.sleep(0.5)
        assert run.status == "partial", (run.status, run.error_code)
        assert run.error_code == "snapshot_not_ready"
        assert delivery == "delivered"
        with engine.connect() as connection:
            attempt = connection.execute(select(
                run_attempts.c.model_id, run_attempts.c.usage_json,
            ).where(run_attempts.c.id == receipt.attempt_id)).one()
        assert attempt.model_id is None and attempt.usage_json is None
    finally:
        with engine.begin() as connection:
            if receipt is not None:
                connection.execute(delete(run_events).where(
                    run_events.c.run_id == receipt.run_id))
                connection.execute(delete(outbox).where(
                    outbox.c.run_id == receipt.run_id))
                connection.execute(delete(run_attempts).where(
                    run_attempts.c.run_id == receipt.run_id))
                connection.execute(delete(agent_runs).where(
                    agent_runs.c.id == receipt.run_id))
                connection.execute(delete(messages).where(
                    messages.c.id == receipt.message_id))
                connection.execute(delete(idempotency_records).where(
                    idempotency_records.c.resource_id == receipt.run_id))
            connection.execute(delete(conversations).where(
                conversations.c.id == conversation_id))
            connection.execute(delete(input_snapshots).where(
                input_snapshots.c.id == snapshot_id))
            connection.execute(delete(cases).where(cases.c.id == case_id))
        engine.dispose()
