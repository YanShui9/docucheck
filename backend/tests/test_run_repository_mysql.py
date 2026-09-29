"""Opt-in MySQL race checks against the user's migrated development database."""

import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier
from uuid import uuid4

import pytest
from dotenv import dotenv_values
from sqlalchemy import create_engine, delete, insert, select, update
from sqlalchemy.engine import URL

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
from backend.app.db.outbox_repository import OutboxRepository
from backend.app.db.run_repository import RepositoryError, RunRepository, SubmitRun
from backend.app.runtime.lifecycle import RunState, RunStatus

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def mysql_repository():
    if os.getenv("DOCUCHECK_RUN_MYSQL_TESTS") != "1":
        pytest.skip("set DOCUCHECK_RUN_MYSQL_TESTS=1 for live MySQL race checks")
    password = (os.getenv("DOCUCHECK_MYSQL_PASSWORD")
                or dotenv_values(ROOT / ".env").get("DOCUCHECK_MYSQL_PASSWORD"))
    if not password:
        pytest.fail("database_password_missing")
    url = URL.create("mysql+pymysql", username="docucheck", password=password,
                     host="127.0.0.1", port=3307, database="docucheck")
    engine = create_engine(url, pool_size=4, pool_pre_ping=True,
                           hide_parameters=True)
    case_id, snapshot_id, conversation_id = _ids = (
        uuid4().hex, uuid4().hex, uuid4().hex,
    )
    now = datetime.now(UTC).replace(tzinfo=None)
    created = False
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
        created = True
        yield RunRepository(engine), _ids
    finally:
        try:
            if created:
                with engine.begin() as connection:
                    run_ids = connection.scalars(select(agent_runs.c.id).where(
                        agent_runs.c.case_id == case_id)).all()
                    if run_ids:
                        attempt_ids = connection.scalars(select(run_attempts.c.id).where(
                            run_attempts.c.run_id.in_(run_ids))).all()
                        if attempt_ids:
                            connection.execute(delete(idempotency_records).where(
                                idempotency_records.c.resource_type == "attempt",
                                idempotency_records.c.resource_id.in_(attempt_ids)))
                        for table in (run_events, outbox, run_attempts):
                            connection.execute(delete(table).where(
                                table.c.run_id.in_(run_ids)))
                        connection.execute(delete(agent_runs).where(
                            agent_runs.c.id.in_(run_ids)))
                    connection.execute(delete(idempotency_records).where(
                        idempotency_records.c.operation_scope ==
                        f"case:{case_id}:message"))
                    conversation_ids = connection.scalars(select(
                        conversations.c.id).where(
                            conversations.c.case_id == case_id)).all()
                    connection.execute(delete(messages).where(
                        messages.c.conversation_id.in_(conversation_ids)))
                    connection.execute(delete(conversations).where(
                        conversations.c.id.in_(conversation_ids)))
                    connection.execute(delete(input_snapshots).where(
                        input_snapshots.c.id == snapshot_id))
                    connection.execute(delete(cases).where(cases.c.id == case_id))
        finally:
            engine.dispose()


def _race(left, right):
    barrier = Barrier(2)

    def call(function):
        barrier.wait(timeout=5)
        try:
            return function()
        except RepositoryError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(call, left)
        second = pool.submit(call, right)
        return first.result(timeout=15), second.result(timeout=15)


def test_mysql_idempotency_claim_and_terminal_races(mysql_repository):
    repository, (case_id, snapshot_id, conversation_id) = mysql_repository
    request = SubmitRun(case_id=case_id, conversation_id=conversation_id,
                        snapshot_id=snapshot_id, expected_revision=1,
                        content="检查供应商 A", mode="standard",
                        idempotency_key="one-user-request")

    first, second = _race(lambda: repository.submit(request),
                          lambda: repository.submit(request))
    assert first == second
    with repository.engine.connect() as connection:
        assert len(connection.scalars(select(agent_runs.c.id).where(
            agent_runs.c.case_id == case_id)).all()) == 1

    claims = _race(lambda: repository.claim(first.run_id, first.attempt_id),
                   lambda: repository.claim(first.run_id, first.attempt_id))
    assert sum(isinstance(result, RunState) for result in claims) == 1
    assert "stale_attempt" in claims
    running = next(result for result in claims if isinstance(result, RunState))

    terminal = _race(
        lambda: repository.finish(first.run_id, first.attempt_id,
                                  running.fence, snapshot_id,
                                  RunStatus.COMPLETED, "ok"),
        lambda: repository.cancel(first.run_id),
    )
    assert sum(isinstance(result, RunState) for result in terminal) == 1
    assert repository.get(first.run_id).status in {
        RunStatus.COMPLETED, RunStatus.CANCELLED,
    }
    with repository.engine.connect() as connection:
        row = connection.execute(select(agent_runs.c.status,
                                        agent_runs.c.answer_text).where(
            agent_runs.c.id == first.run_id)).one()
        assert (row.answer_text == "ok") == (row.status == "completed")

    retry_run = repository.submit(replace(request, idempotency_key="retry-case"))
    retry_running = repository.claim(retry_run.run_id, retry_run.attempt_id)
    repository.interrupt(retry_run.run_id, retry_run.attempt_id,
                         retry_running.fence)
    retries = _race(
        lambda: repository.retry(retry_run.run_id, snapshot_id, 1, "retry-1"),
        lambda: repository.retry(retry_run.run_id, snapshot_id, 1, "retry-1"),
    )
    assert retries[0] == retries[1]
    assert repository.get(retry_run.run_id).active_attempt_id == retries[0].attempt_id
    with repository.engine.connect() as connection:
        assert len(connection.scalars(select(run_attempts.c.id).where(
            run_attempts.c.run_id == retry_run.run_id)).all()) == 2


def test_mysql_outbox_claim_skips_locked_row(mysql_repository):
    repository, (case_id, snapshot_id, conversation_id) = mysql_repository
    second_conversation_id = uuid4().hex
    with repository.engine.begin() as connection:
        connection.execute(insert(conversations).values(
            id=second_conversation_id, case_id=case_id,
            created_at=datetime.now(UTC).replace(tzinfo=None)))
    first = repository.submit(SubmitRun(
        case_id=case_id, conversation_id=conversation_id,
        snapshot_id=snapshot_id, expected_revision=1,
        content="审查 A", mode="standard", idempotency_key="outbox-first",
    ))
    second = repository.submit(SubmitRun(
        case_id=case_id, conversation_id=second_conversation_id,
        snapshot_id=snapshot_id, expected_revision=1,
        content="审查 B", mode="standard", idempotency_key="outbox-second",
    ))
    event_type = f"test.{uuid4().hex[:16]}"
    with repository.engine.begin() as connection:
        connection.execute(update(outbox).where(
            outbox.c.run_id.in_((first.run_id, second.run_id)),
        ).values(event_type=event_type))
    now = datetime.now(UTC).replace(tzinfo=None) + timedelta(seconds=1)
    with repository.engine.connect() as locked_connection, locked_connection.begin():
        locked = locked_connection.scalar(select(outbox.c.id).where(
            outbox.c.run_id == first.run_id).with_for_update())
        claims = OutboxRepository(repository.engine).claim(
            limit=2, event_type=event_type, now=now)
        assert len(claims) == 1
        assert claims[0].run_id == second.run_id
        assert claims[0].id != locked
    remaining = OutboxRepository(repository.engine).claim(
        limit=2, event_type=event_type, now=now)
    assert len(remaining) == 1
    assert remaining[0].run_id == first.run_id


def test_mysql_serializes_distinct_questions_in_one_conversation(mysql_repository):
    repository, (case_id, snapshot_id, conversation_id) = mysql_repository

    def submit(suffix):
        return repository.submit(SubmitRun(
            case_id=case_id, conversation_id=conversation_id,
            snapshot_id=snapshot_id, expected_revision=1,
            content=f"审查 {suffix}", mode="standard",
            idempotency_key=f"question-{suffix}",
        ))

    results = _race(lambda: submit("A"), lambda: submit("B"))
    assert sum(result == "conversation_busy" for result in results) == 1
    assert sum(hasattr(result, "run_id") for result in results) == 1
