"""Opt-in MySQL race for Case deletion and Run claim; no model calls."""

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, delete, insert, select

from backend.app.db.case_deletion import CaseDeletion
from backend.app.db.connection import database_url
from backend.app.db.models import (
    agent_runs,
    cases,
    conversations,
    document_versions,
    input_snapshots,
    messages,
    outbox,
    run_attempts,
    run_events,
)
from backend.app.db.run_repository import RepositoryError, RunRepository


def test_mysql_delete_races_with_claim_and_is_idempotent():
    if os.getenv("DOCUCHECK_RUN_MYSQL_TESTS") != "1":
        pytest.skip("set DOCUCHECK_RUN_MYSQL_TESTS=1 for live MySQL deletion race")
    engine = create_engine(database_url(), pool_size=4, pool_pre_ping=True,
                           hide_parameters=True)
    case_id, snapshot_id, doc_id = uuid4().hex, uuid4().hex, uuid4().hex
    conversation_id, message_id = uuid4().hex, uuid4().hex
    run_id, attempt_id, outbox_id = uuid4().hex, uuid4().hex, uuid4().hex
    now = datetime.now(UTC).replace(tzinfo=None)
    created = False
    try:
        with engine.begin() as connection:
            connection.execute(insert(cases).values(
                id=case_id, revision=1, created_at=now, updated_at=now))
            connection.execute(insert(input_snapshots).values(
                id=snapshot_id, case_id=case_id, revision=1, created_at=now))
            connection.execute(insert(document_versions).values(
                id=doc_id, case_id=case_id, snapshot_id=snapshot_id,
                role="requirement", original_name="synthetic.pdf",
                storage_key=f"uploads/{doc_id[:2]}/{doc_id}.pdf",
                source_sha256="a" * 64, media_type="application/pdf",
                parse_status="pending", created_at=now,
            ))
            connection.execute(insert(conversations).values(
                id=conversation_id, case_id=case_id, created_at=now))
            connection.execute(insert(messages).values(
                id=message_id, conversation_id=conversation_id,
                role="user", content="审查", created_at=now))
            connection.execute(insert(agent_runs).values(
                id=run_id, case_id=case_id, conversation_id=conversation_id,
                message_id=message_id, snapshot_id=snapshot_id, revision=1,
                mode="standard", status="queued", active_attempt_id=attempt_id,
                fence=0, created_at=now, updated_at=now,
            ))
            connection.execute(insert(run_attempts).values(
                id=attempt_id, run_id=run_id, attempt_no=1, status="queued",
                outcome_unknown=False, created_at=now,
            ))
            connection.execute(insert(outbox).values(
                id=outbox_id, event_key=f"run:{run_id}:attempt:{attempt_id}:dispatch",
                run_id=run_id, attempt_id=attempt_id, event_type="run.dispatch",
                payload_json={"run_id": run_id, "attempt_id": attempt_id},
                status="pending", delivery_attempts=0, available_at=now,
                created_at=now,
            ))
        created = True
        barrier = Barrier(3)

        def request_delete():
            barrier.wait()
            return CaseDeletion(engine).request(case_id, 1)

        def claim():
            barrier.wait()
            try:
                return RunRepository(engine).claim(run_id, attempt_id).status.value
            except RepositoryError as exc:
                return exc.code

        with ThreadPoolExecutor(max_workers=3) as pool:
            first = pool.submit(request_delete)
            second = pool.submit(request_delete)
            claimed = pool.submit(claim)
            receipts = (first.result(), second.result())
            claim_result = claimed.result()
        assert receipts[0] == receipts[1]
        assert receipts[0]["status"] == "deleting"
        assert claim_result in {"running", "case_deleting"}
        with engine.connect() as connection:
            assert connection.scalar(select(cases.c.deletion_requested_at).where(
                cases.c.id == case_id)) is not None
            assert connection.scalar(select(agent_runs.c.status).where(
                agent_runs.c.id == run_id)) == "cancelled"
            assert connection.scalar(select(document_versions.c.parse_status).where(
                document_versions.c.id == doc_id)) == "cancelled"
            assert connection.scalar(select(outbox.c.status).where(
                outbox.c.id == outbox_id)) == "cancelled"
            assert len(connection.execute(select(run_events).where(
                run_events.c.run_id == run_id,
                run_events.c.kind == "case_delete_requested",
            )).all()) == 1
    finally:
        if created:
            with engine.begin() as connection:
                connection.execute(delete(outbox).where(outbox.c.id == outbox_id))
                connection.execute(delete(run_events).where(
                    run_events.c.run_id == run_id))
                connection.execute(delete(run_attempts).where(
                    run_attempts.c.run_id == run_id))
                connection.execute(delete(agent_runs).where(agent_runs.c.id == run_id))
                connection.execute(delete(messages).where(messages.c.id == message_id))
                connection.execute(delete(conversations).where(
                    conversations.c.id == conversation_id))
                connection.execute(delete(document_versions).where(
                    document_versions.c.id == doc_id))
                connection.execute(delete(input_snapshots).where(
                    input_snapshots.c.id == snapshot_id))
                connection.execute(delete(cases).where(cases.c.id == case_id))
        engine.dispose()
