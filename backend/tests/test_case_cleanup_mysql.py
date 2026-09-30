"""Opt-in MySQL cleanup of synthetic Cases sharing one original file."""

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, delete, insert, select

from backend.app.db.case_cleanup import CaseCleanup
from backend.app.db.case_deletion import CaseDeletion
from backend.app.db.connection import database_url
from backend.app.db.models import (
    agent_runs,
    case_cleanup_files,
    cases,
    conversations,
    document_versions,
    input_snapshots,
    messages,
    outbox,
    run_attempts,
    run_events,
)


def test_mysql_cleanup_retains_shared_original_until_last_case(tmp_path: Path):
    if os.getenv("DOCUCHECK_RUN_MYSQL_TESTS") != "1":
        pytest.skip("set DOCUCHECK_RUN_MYSQL_TESTS=1 for live MySQL cleanup")
    engine = create_engine(database_url(), pool_pre_ping=True,
                           hide_parameters=True)
    case_a, case_b = uuid4().hex, uuid4().hex
    snapshot_a, snapshot_b = uuid4().hex, uuid4().hex
    document_a, document_b = uuid4().hex, uuid4().hex
    conversation_id, message_id = uuid4().hex, uuid4().hex
    run_id, attempt_id, outbox_id = uuid4().hex, uuid4().hex, uuid4().hex
    object_id = uuid4().hex
    key = f"uploads/{object_id[:2]}/{object_id}.pdf"
    data_root = tmp_path / "data"
    path = data_root / key
    path.parent.mkdir(parents=True)
    content = b"synthetic cleanup original"
    path.write_bytes(content)
    now = datetime.now(UTC).replace(tzinfo=None)
    try:
        with engine.begin() as connection:
            for case_id, snapshot_id, document_id in (
                    (case_a, snapshot_a, document_a),
                    (case_b, snapshot_b, document_b)):
                connection.execute(insert(cases).values(
                    id=case_id, revision=1, created_at=now, updated_at=now))
                connection.execute(insert(input_snapshots).values(
                    id=snapshot_id, case_id=case_id, revision=1, created_at=now))
                connection.execute(insert(document_versions).values(
                    id=document_id, case_id=case_id, snapshot_id=snapshot_id,
                    role="requirement", original_name="synthetic.pdf",
                    storage_key=key, source_sha256=sha256(content).hexdigest(),
                    media_type="application/pdf", parse_status="pending",
                    created_at=now,
                ))
            connection.execute(insert(conversations).values(
                id=conversation_id, case_id=case_a, created_at=now))
            connection.execute(insert(messages).values(
                id=message_id, conversation_id=conversation_id,
                role="user", content="审查", created_at=now))
            connection.execute(insert(agent_runs).values(
                id=run_id, case_id=case_a, conversation_id=conversation_id,
                message_id=message_id, snapshot_id=snapshot_a, revision=1,
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
        deletion = CaseDeletion(engine)
        cleaner = CaseCleanup(engine, data_root)
        assert deletion.request(case_a, 1)["status"] == "deleting"
        barrier = Barrier(2)

        def claim(_):
            barrier.wait()
            return cleaner.claim(case_id=case_a)

        with ThreadPoolExecutor(max_workers=2) as pool:
            claims = list(pool.map(claim, range(2)))
        winners = [claim for claim in claims if claim is not None]
        assert len(winners) == 1
        first = cleaner.process(winners[0])
        assert first["status"] == "deleted"
        assert first["files_retained"] == 1
        assert path.is_file()
        with engine.connect() as connection:
            assert connection.scalar(select(document_versions.c.id).where(
                document_versions.c.id == document_b)) == document_b
            assert connection.scalar(select(agent_runs.c.id).where(
                agent_runs.c.id == run_id)) is None
        assert deletion.request(case_b, 1)["status"] == "deleting"
        second = cleaner.process_once(case_id=case_b)
        assert second["status"] == "deleted"
        assert second["files_removed"] == 1
        assert not path.exists()
    finally:
        with engine.begin() as connection:
            connection.execute(delete(case_cleanup_files).where(
                case_cleanup_files.c.case_id.in_((case_a, case_b))))
            connection.execute(delete(outbox).where(outbox.c.run_id == run_id))
            connection.execute(delete(run_events).where(run_events.c.run_id == run_id))
            connection.execute(delete(run_attempts).where(
                run_attempts.c.run_id == run_id))
            connection.execute(delete(agent_runs).where(agent_runs.c.id == run_id))
            connection.execute(delete(messages).where(messages.c.id == message_id))
            connection.execute(delete(conversations).where(
                conversations.c.id == conversation_id))
            connection.execute(delete(document_versions).where(
                document_versions.c.id.in_((document_a, document_b))))
            connection.execute(delete(input_snapshots).where(
                input_snapshots.c.id.in_((snapshot_a, snapshot_b))))
            connection.execute(delete(cases).where(
                cases.c.id.in_((case_a, case_b))))
        path.unlink(missing_ok=True)
        engine.dispose()
