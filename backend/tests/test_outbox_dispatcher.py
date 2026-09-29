"""Outbox delivery remains safe when broker sends or acknowledgements repeat."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, event, insert, select, update

from backend.app.db.models import (
    cases,
    conversations,
    document_versions,
    input_snapshots,
    metadata,
    outbox,
)
from backend.app.db.outbox_dispatcher import dispatch_once
from backend.app.db.outbox_repository import OutboxRepository
from backend.app.db.run_repository import RunRepository, SubmitRun


@pytest.fixture
def queued(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'dispatch.db'}")

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, record):
        connection.execute("PRAGMA foreign_keys=ON")

    metadata.create_all(engine)
    now = datetime.now(UTC).replace(tzinfo=None)
    with engine.begin() as connection:
        connection.execute(insert(cases).values(
            id="case-a", revision=1, created_at=now, updated_at=now,
        ))
        connection.execute(insert(input_snapshots).values(
            id="snapshot-a", case_id="case-a", revision=1, created_at=now,
        ))
        connection.execute(insert(conversations).values(
            id="conversation-a", case_id="case-a", created_at=now,
        ))
    receipt = RunRepository(engine).submit(SubmitRun(
        case_id="case-a", conversation_id="conversation-a",
        snapshot_id="snapshot-a", expected_revision=1,
        content="审查供应商 A", mode="standard", idempotency_key="message-1",
    ))
    with engine.connect() as connection:
        due = connection.scalar(select(outbox.c.available_at))
    yield engine, receipt, due + timedelta(seconds=1)
    engine.dispose()


def test_publishes_only_current_queued_attempt_and_acks(queued):
    engine, receipt, now = queued
    sent = []
    summary = dispatch_once(engine, lambda *args: sent.append(args), now=now)
    assert summary.claimed == 1
    assert summary.published == 1
    assert sent == [(receipt.run_id, receipt.attempt_id,
                     f"run:{receipt.run_id}:attempt:{receipt.attempt_id}:dispatch")]
    with engine.connect() as connection:
        assert connection.scalar(select(outbox.c.status)) == "delivered"
    assert dispatch_once(engine, lambda *args: sent.append(args),
                         now=now + timedelta(seconds=61)).claimed == 0
    assert len(sent) == 1


def test_waits_for_uploaded_document_parsing_before_dispatch(queued):
    engine, receipt, now = queued
    with engine.begin() as connection:
        connection.execute(insert(document_versions).values(
            id="document-a", case_id="case-a", snapshot_id="snapshot-a",
            role="requirement", original_name="requirements.pdf",
            storage_key="uploads/do/document-a.pdf", source_sha256="a" * 64,
            media_type="application/pdf", parse_status="pending",
            coverage_json={"preflight": {"pdf_pages": 1}}, created_at=now,
        ))
    sent = []
    waiting = dispatch_once(engine, lambda *args: sent.append(args), now=now)
    assert waiting.retried == 1 and waiting.published == 0
    assert sent == []
    with engine.begin() as connection:
        connection.execute(update(document_versions).where(
            document_versions.c.id == "document-a").values(parse_status="complete"))
    ready = dispatch_once(engine, lambda *args: sent.append(args),
                          now=now + timedelta(seconds=31))
    assert ready.published == 1
    assert sent[0][:2] == (receipt.run_id, receipt.attempt_id)


def test_broker_failure_releases_with_delay_and_retries(queued):
    engine, receipt, now = queued

    def fail(*args):
        raise OSError("broker unavailable")

    failed = dispatch_once(engine, fail, now=now)
    assert failed.claimed == 1 and failed.retried == 1
    with engine.connect() as connection:
        row = connection.execute(select(outbox)).mappings().one()
    assert row["status"] == "pending"
    assert row["delivery_attempts"] == 1
    assert row["available_at"] > now
    assert dispatch_once(engine, fail, now=now + timedelta(seconds=1)).claimed == 0
    sent = []
    recovered = dispatch_once(engine, lambda *args: sent.append(args),
                              now=row["available_at"])
    assert recovered.published == 1
    assert sent[0][:2] == (receipt.run_id, receipt.attempt_id)


def test_finished_run_is_skipped_without_broker_send(queued):
    engine, receipt, now = queued
    runs = RunRepository(engine)
    claimed = runs.claim(receipt.run_id, receipt.attempt_id)
    from backend.app.runtime.lifecycle import RunStatus

    runs.finish(receipt.run_id, receipt.attempt_id, claimed.fence,
                claimed.snapshot_id, RunStatus.COMPLETED, answer_text="已完成")
    summary = dispatch_once(engine, lambda *args: pytest.fail("obsolete send"),
                            now=now)
    assert summary.skipped == 1
    with engine.connect() as connection:
        assert connection.scalar(select(outbox.c.status)) == "delivered"


def test_invalid_payload_never_publishes_and_remains_retryable(queued):
    engine, _, now = queued
    with engine.begin() as connection:
        connection.execute(update(outbox).values(payload_json={"run_id": "wrong"}))
    summary = dispatch_once(engine, lambda *args: pytest.fail("invalid send"),
                            now=now)
    assert summary.retried == 1
    with engine.connect() as connection:
        assert connection.scalar(select(outbox.c.status)) == "pending"


def test_publish_then_lost_ack_can_repeat_but_run_claim_cannot(queued):
    engine, receipt, now = queued
    outbox_repo = OutboxRepository(engine)
    first = outbox_repo.claim(now=now, lease_seconds=30)[0]
    # Broker accepted first publish, then dispatcher died before DB ack.
    sent = [(first.run_id, first.attempt_id, first.event_key)]
    repeated = dispatch_once(engine, lambda *args: sent.append(args),
                             now=now + timedelta(seconds=31))
    assert repeated.published == 1 and len(sent) == 2
    assert sent[0] == sent[1]
    runs = RunRepository(engine)
    runs.claim(receipt.run_id, receipt.attempt_id)
    from backend.app.db.run_repository import RepositoryError

    with pytest.raises(RepositoryError, match="stale_attempt"):
        runs.claim(receipt.run_id, receipt.attempt_id)


def test_maintenance_republishes_lost_queue_message_then_stops_after_claim(queued):
    from backend.app.dispatch_outbox import maintenance_once

    engine, receipt, now = queued
    sent = []
    first = dispatch_once(engine, lambda *args: sent.append(args), now=now)
    assert first.published == 1
    summary = maintenance_once(engine, now=now + timedelta(seconds=121))
    assert summary.requeued == 1 and summary.recovered == 0
    second = dispatch_once(engine, lambda *args: sent.append(args),
                           now=now + timedelta(seconds=121))
    assert second.published == 1 and len(sent) == 2 and sent[0] == sent[1]
    RunRepository(engine).claim(receipt.run_id, receipt.attempt_id)
    later = maintenance_once(engine, now=now + timedelta(hours=2))
    assert later.requeued == 0


def test_maintenance_marks_lost_running_attempt_unknown_without_publishing(queued):
    from backend.app.db.models import agent_runs, run_attempts
    from backend.app.dispatch_outbox import maintenance_once

    engine, receipt, now = queued
    running = RunRepository(engine).claim(receipt.run_id, receipt.attempt_id)
    with engine.begin() as connection:
        connection.execute(update(agent_runs).where(
            agent_runs.c.id == receipt.run_id).values(
                updated_at=now - timedelta(minutes=3)))
    summary = maintenance_once(engine, now=now)
    assert summary.recovered == 1 and summary.requeued == 0
    assert dispatch_once(engine, lambda *args: pytest.fail("unexpected publish"),
                         now=now).published == 0
    with engine.connect() as connection:
        assert connection.scalar(select(run_attempts.c.outcome_unknown)) is True
    from backend.app.runtime.lifecycle import RunStatus

    assert RunRepository(engine).get(receipt.run_id).status == RunStatus.INTERRUPTED
    with pytest.raises(Exception, match="stale_attempt"):
        RunRepository(engine).finish(receipt.run_id, receipt.attempt_id,
                                     running.fence, running.snapshot_id,
                                     RunStatus.COMPLETED, "late")


def test_dispatcher_cli_once_uses_injected_publisher(queued, capsys):
    from backend.app.dispatch_outbox import main

    engine, receipt, _ = queued
    sent = []
    code = main(["--once"], engine=engine,
                publish=lambda *args: sent.append(args))
    assert code == 0
    assert sent[0][:2] == (receipt.run_id, receipt.attempt_id)
    assert '"published": 1' in capsys.readouterr().out


def test_celery_task_executes_and_ignores_redelivery_without_network(queued,
                                                                    monkeypatch):
    engine, receipt, _ = queued
    monkeypatch.setenv("DOCUCHECK_CELERY_BROKER_URL", "redis://127.0.0.1:6379/0")
    monkeypatch.setenv("DOCUCHECK_DATABASE_URL", str(engine.url))
    monkeypatch.setenv("DOCUCHECK_MODEL_PROVIDER", "openai-compatible")
    monkeypatch.setenv("DOCUCHECK_MODEL_ID", "offline-test")
    monkeypatch.setenv("DOCUCHECK_MODEL_BASE_URL", "http://127.0.0.1:9999")
    monkeypatch.setenv("DOCUCHECK_MODEL_API_KEY", "offline-only")
    monkeypatch.setenv("DOCUCHECK_MODEL_CONTEXT_WINDOW", "32768")
    from backend.app.celery_app import app

    task = app.tasks["docucheck.execute_run"]
    first = task.apply(args=(receipt.run_id, receipt.attempt_id), throw=True).get()
    second = task.apply(args=(receipt.run_id, receipt.attempt_id), throw=True).get()
    assert first["status"] == "partial"
    assert first["model_calls"] == 0
    assert second["status"] == "ignored"
