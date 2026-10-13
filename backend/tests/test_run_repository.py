"""Persistence checks for the M3 Run transaction boundary."""

from datetime import UTC, datetime, timedelta
from time import monotonic, sleep

import pytest
from sqlalchemy import create_engine, event, func, insert, select

from backend.app.db.models import (
    agent_runs,
    cases,
    conversations,
    idempotency_records,
    input_snapshots,
    messages,
    metadata,
    outbox,
    run_attempts,
    run_events,
)
from backend.app.db.run_repository import RepositoryError, RunRepository, SubmitRun
from backend.app.runtime.lifecycle import RunStatus

NOW = datetime(2026, 9, 28, tzinfo=UTC)


@pytest.fixture
def repository(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'runs.db'}")

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, record):
        connection.execute("PRAGMA foreign_keys=ON")

    metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(insert(cases), [
            {"id": "case-a", "revision": 1, "created_at": NOW, "updated_at": NOW},
            {"id": "case-b", "revision": 1, "created_at": NOW, "updated_at": NOW},
        ])
        connection.execute(insert(input_snapshots), [
            {"id": "snapshot-a", "case_id": "case-a", "revision": 1, "created_at": NOW},
            {"id": "snapshot-b", "case_id": "case-b", "revision": 1, "created_at": NOW},
        ])
        connection.execute(insert(conversations), [
            {"id": "conv-a", "case_id": "case-a", "created_at": NOW},
            {"id": "conv-b", "case_id": "case-b", "created_at": NOW},
        ])
    yield RunRepository(engine)
    engine.dispose()


def _request(**changes):
    return {"case_id": "case-a", "conversation_id": "conv-a",
            "snapshot_id": "snapshot-a", "expected_revision": 1,
            "content": "完整审查供应商 A", "mode": "thorough",
            "idempotency_key": "request-1"} | changes


def _submit(repository, **changes):
    return repository.submit(SubmitRun(**_request(**changes)))


def _count(connection, table):
    return connection.scalar(select(func.count()).select_from(table))


def test_finish_records_only_allowlisted_diagnostics_in_owned_transaction(repository):
    receipt = _submit(repository)
    claim = repository.claim(receipt.run_id, receipt.attempt_id)
    repository.finish(receipt.run_id, receipt.attempt_id, claim.fence, claim.snapshot_id,
                      RunStatus.FAILED, error_code="invalid_output", trace_events=[{
                          "kind": "model", "operation": "extract", "sequence": 1,
                          "status": "failed", "error": "invalid_output",
                          "failure_detail": "tool_schema", "outcome_unknown": True,
                          "usage": {"input_tokens": 10, "output_tokens": True,
                                    "total_tokens": -1, "other": "SECRET"},
                          "request_id": "SECRET", "headers": {"Authorization": "SECRET"},
                          "prompt": "SECRET", "arguments": {"secret": "SECRET"},
                      }])
    with repository.engine.connect() as connection:
        rows = connection.execute(select(run_events).where(
            run_events.c.run_id == receipt.run_id).order_by(run_events.c.seq)).mappings().all()
    diagnostic = next(row for row in rows if row["kind"] == "agent_model")
    assert diagnostic["summary_json"] == {
        "operation": "extract", "sequence": 1, "status": "failed",
        "error": "invalid_output", "failure_detail": "tool_schema",
        "outcome_unknown": True, "usage": {"input_tokens": 10},
    }
    assert diagnostic["attempt_id"] == receipt.attempt_id
    assert rows[-1]["kind"] == "finished"


def test_stale_worker_cannot_append_diagnostics_after_cancel(repository):
    receipt = _submit(repository)
    claim = repository.claim(receipt.run_id, receipt.attempt_id)
    repository.cancel(receipt.run_id)
    with repository.engine.connect() as connection:
        before = _count(connection, run_events)
    with pytest.raises(RepositoryError, match="stale_attempt"):
        repository.finish(receipt.run_id, receipt.attempt_id, claim.fence, claim.snapshot_id,
                          RunStatus.FAILED, trace_events=[{
                              "kind": "tool", "tool": "extract_facts", "sequence": 1,
                              "status": "failed", "error": "invalid_tool_result",
                          }])
    with repository.engine.connect() as connection:
        assert _count(connection, run_events) == before


def test_submit_is_atomic_and_same_payload_reuses_first_run(repository):
    first = _submit(repository)
    second = _submit(repository)
    assert second == first
    with repository.engine.connect() as connection:
        for table in (messages, agent_runs, run_attempts, outbox,
                      run_events, idempotency_records):
            assert _count(connection, table) == 1
        assert connection.scalar(select(agent_runs.c.status)) == "queued"
        assert connection.scalar(select(outbox.c.event_key)) == (
            f"run:{first.run_id}:attempt:{first.attempt_id}:dispatch"
        )


def test_same_key_different_payload_conflicts_without_second_message(repository):
    _submit(repository)
    with pytest.raises(RepositoryError, match="idempotency_conflict"):
        _submit(repository, content="改成审查供应商 B")
    with repository.engine.connect() as connection:
        assert _count(connection, messages) == 1


@pytest.mark.parametrize("changes,code", [
    ({"expected_revision": 2}, "revision_conflict"),
    ({"conversation_id": "conv-b"}, "scope_mismatch"),
    ({"snapshot_id": "snapshot-b"}, "scope_mismatch"),
])
def test_invalid_scope_or_revision_rolls_back_everything(repository, changes, code):
    with pytest.raises(RepositoryError, match=code):
        _submit(repository, **changes)
    with repository.engine.connect() as connection:
        for table in (messages, agent_runs, run_attempts, outbox,
                      run_events, idempotency_records):
            assert _count(connection, table) == 0


def test_claim_once_finish_rejects_stale_fence_and_snapshot(repository):
    submitted = _submit(repository)
    running = repository.claim(submitted.run_id, submitted.attempt_id)
    assert running.status == RunStatus.RUNNING
    assert running.fence == 1
    with pytest.raises(RepositoryError, match="stale_attempt"):
        repository.claim(submitted.run_id, submitted.attempt_id)
    with pytest.raises(RepositoryError, match="stale_attempt"):
        repository.finish(submitted.run_id, submitted.attempt_id, 0,
                          "snapshot-a", RunStatus.COMPLETED, "bad")
    with pytest.raises(RepositoryError, match="stale_attempt"):
        repository.finish(submitted.run_id, submitted.attempt_id, 1,
                          "snapshot-b", RunStatus.COMPLETED, "bad")
    done = repository.finish(submitted.run_id, submitted.attempt_id, 1,
                             "snapshot-a", RunStatus.COMPLETED, "ok")
    assert done.status == RunStatus.COMPLETED
    with repository.engine.connect() as connection:
        assert connection.scalar(select(agent_runs.c.answer_text)) == "ok"
        assert _count(connection, run_events) == 3


def test_cancel_blocks_late_answer(repository):
    submitted = _submit(repository)
    running = repository.claim(submitted.run_id, submitted.attempt_id)
    cancelled = repository.cancel(submitted.run_id)
    assert cancelled.status == RunStatus.CANCELLED
    assert cancelled.fence == running.fence
    with repository.engine.connect() as connection:
        attempt = connection.execute(select(run_attempts).where(
            run_attempts.c.id == submitted.attempt_id)).mappings().one()
        cancelled_at = connection.scalar(select(agent_runs.c.updated_at))
    assert attempt["outcome_unknown"] is True
    assert repository.recover_stale(now=cancelled_at + timedelta(minutes=3)) == 0
    with pytest.raises(RepositoryError, match="stale_attempt"):
        repository.finish(submitted.run_id, submitted.attempt_id, running.fence,
                          "snapshot-a", RunStatus.COMPLETED, "late")
    with repository.engine.connect() as connection:
        assert connection.scalar(select(agent_runs.c.answer_text)) is None
        assert connection.scalar(select(run_attempts.c.outcome_unknown)) is True


def test_cancel_queued_attempt_keeps_known_outcome(repository):
    submitted = _submit(repository)
    cancelled = repository.cancel(submitted.run_id)
    assert cancelled.status == RunStatus.CANCELLED and cancelled.fence == 0
    with repository.engine.connect() as connection:
        assert connection.scalar(select(run_attempts.c.outcome_unknown)) is False


@pytest.mark.parametrize("outcome_unknown", [False, True])
def test_cancel_waiting_attempt_preserves_outcome(repository, outcome_unknown):
    submitted = _submit(repository)
    running = repository.claim(submitted.run_id, submitted.attempt_id)
    repository.finish(submitted.run_id, submitted.attempt_id, running.fence,
                      running.snapshot_id, RunStatus.WAITING_USER,
                      outcome_unknown=outcome_unknown)
    repository.cancel(submitted.run_id)
    with repository.engine.connect() as connection:
        assert connection.scalar(select(run_attempts.c.outcome_unknown)) is outcome_unknown


@pytest.mark.parametrize("worker_lost", [False, True])
def test_cancel_interrupted_attempt_preserves_outcome(repository, worker_lost):
    submitted = _submit(repository)
    running = repository.claim(submitted.run_id, submitted.attempt_id)
    if worker_lost:
        with repository.engine.connect() as connection:
            claimed_at = connection.scalar(select(agent_runs.c.updated_at))
        assert repository.recover_stale(now=claimed_at + timedelta(minutes=3)) == 1
    else:
        repository.interrupt(submitted.run_id, submitted.attempt_id, running.fence)
    interrupted = repository.get(submitted.run_id)
    cancelled = repository.cancel(submitted.run_id)
    assert cancelled.fence == interrupted.fence
    with repository.engine.connect() as connection:
        assert connection.scalar(select(run_attempts.c.outcome_unknown)) is worker_lost


def test_retry_and_resume_replace_attempt_and_reject_old_result(repository):
    first = _submit(repository)
    running = repository.claim(first.run_id, first.attempt_id)
    repository.interrupt(first.run_id, first.attempt_id, running.fence)
    retried = repository.retry(first.run_id, "snapshot-a", 1, "retry-1")
    queued = repository.get(first.run_id)
    assert queued.status == RunStatus.QUEUED
    assert retried.attempt_id == queued.active_attempt_id
    assert queued.active_attempt_id != first.attempt_id
    with pytest.raises(RepositoryError, match="stale_attempt"):
        repository.finish(first.run_id, first.attempt_id, running.fence,
                          "snapshot-a", RunStatus.COMPLETED, "late")
    next_running = repository.claim(first.run_id, queued.active_attempt_id)
    assert next_running.fence == 2
    waiting = repository.finish(first.run_id, queued.active_attempt_id, 2,
                                "snapshot-a", RunStatus.WAITING_USER)
    assert waiting.status == RunStatus.WAITING_USER
    resumed_receipt = repository.resume(first.run_id, "snapshot-a", 1,
                                        "resume-1", "供应商 A")
    resumed = repository.get(first.run_id)
    assert resumed.status == RunStatus.QUEUED
    assert resumed_receipt.attempt_id == resumed.active_attempt_id
    assert resumed.active_attempt_id not in {first.attempt_id,
                                             queued.active_attempt_id}
    with repository.engine.connect() as connection:
        assert _count(connection, run_attempts) == 3
        assert _count(connection, outbox) == 3


def test_retry_rejects_changed_current_case_revision(repository):
    first = _submit(repository)
    running = repository.claim(first.run_id, first.attempt_id)
    repository.interrupt(first.run_id, first.attempt_id, running.fence)
    with repository.engine.begin() as connection:
        connection.execute(cases.update().where(cases.c.id == "case-a")
                           .values(revision=2))
    with pytest.raises(RepositoryError, match="snapshot_changed"):
        repository.retry(first.run_id, "snapshot-a", 1, "retry-1")
    with repository.engine.connect() as connection:
        assert _count(connection, run_attempts) == 1


def test_retry_idempotency_replays_original_attempt_and_conflicts_on_payload(repository):
    first = _submit(repository)
    running = repository.claim(first.run_id, first.attempt_id)
    repository.interrupt(first.run_id, first.attempt_id, running.fence)
    retried = repository.retry(first.run_id, "snapshot-a", 1, "retry-1")
    repository.claim(first.run_id, retried.attempt_id)
    assert repository.retry(first.run_id, "snapshot-a", 1, "retry-1") == retried
    with pytest.raises(RepositoryError, match="idempotency_conflict"):
        repository.retry(first.run_id, "snapshot-b", 1, "retry-1")
    with repository.engine.connect() as connection:
        assert _count(connection, run_attempts) == 2
        assert _count(connection, outbox) == 2


def test_resume_idempotency_replays_original_attempt(repository):
    first = _submit(repository)
    running = repository.claim(first.run_id, first.attempt_id)
    repository.finish(first.run_id, first.attempt_id, running.fence,
                      "snapshot-a", RunStatus.WAITING_USER)
    resumed = repository.resume(first.run_id, "snapshot-a", 1,
                                "resume-1", "供应商 A")
    assert repository.resume(first.run_id, "snapshot-a", 1,
                             "resume-1", "供应商 A") == resumed
    with pytest.raises(RepositoryError, match="idempotency_conflict"):
        repository.resume(first.run_id, "snapshot-a", 1,
                          "resume-1", "供应商 B")
    with repository.engine.connect() as connection:
        assert _count(connection, run_attempts) == 2
        assert _count(connection, messages) == 2
        response_id = connection.scalar(select(run_attempts.c.resume_message_id).where(
            run_attempts.c.id == resumed.attempt_id))
        assert connection.scalar(select(messages.c.content).where(
            messages.c.id == response_id)) == "供应商 A"


def test_conversation_accepts_only_one_active_run(repository):
    first = _submit(repository)
    with pytest.raises(RepositoryError, match="conversation_busy"):
        _submit(repository, idempotency_key="question-2")
    running = repository.claim(first.run_id, first.attempt_id)
    with pytest.raises(RepositoryError, match="conversation_busy"):
        _submit(repository, idempotency_key="question-3")
    repository.finish(first.run_id, first.attempt_id, running.fence,
                      running.snapshot_id, RunStatus.WAITING_USER)
    with pytest.raises(RepositoryError, match="conversation_busy"):
        _submit(repository, idempotency_key="question-4")
    resumed = repository.resume(first.run_id, running.snapshot_id, 1,
                                "clarify-1", "供应商 A")
    claimed = repository.claim(first.run_id, resumed.attempt_id)
    repository.finish(first.run_id, resumed.attempt_id, claimed.fence,
                      claimed.snapshot_id, RunStatus.COMPLETED, "已完成")
    second = _submit(repository, idempotency_key="question-5")
    assert second.run_id != first.run_id


def test_retry_cannot_queue_beside_another_active_run(repository):
    first = _submit(repository)
    running = repository.claim(first.run_id, first.attempt_id)
    repository.finish(first.run_id, first.attempt_id, running.fence,
                      "snapshot-a", RunStatus.FAILED, error_code="model_failed")
    second = _submit(repository, idempotency_key="question-2")

    with pytest.raises(RepositoryError, match="conversation_busy"):
        repository.retry(first.run_id, "snapshot-a", 1, "retry-1")

    assert repository.get(second.run_id).status == RunStatus.QUEUED
    assert repository.get(first.run_id).status == RunStatus.FAILED


def test_old_queued_run_becomes_interrupted_after_snapshot_revision_changes(repository):
    submitted = _submit(repository)
    with repository.engine.begin() as connection:
        connection.execute(cases.update().where(cases.c.id == "case-a")
                           .values(revision=2))

    assert repository.recover_stale() == 1
    assert repository.get(submitted.run_id).status == RunStatus.INTERRUPTED
    with repository.engine.connect() as connection:
        run = connection.execute(select(agent_runs).where(
            agent_runs.c.id == submitted.run_id)).mappings().one()
        attempt = connection.execute(select(run_attempts).where(
            run_attempts.c.id == submitted.attempt_id)).mappings().one()
    assert run["error_code"] == "snapshot_changed"
    assert attempt["status"] == "interrupted"


def test_running_attempt_is_no_longer_current_after_case_revision_changes(repository):
    submitted = _submit(repository)
    running = repository.claim(submitted.run_id, submitted.attempt_id)
    assert repository.is_current(submitted.run_id, submitted.attempt_id,
                                 running.fence)
    with repository.engine.begin() as connection:
        connection.execute(cases.update().where(cases.c.id == "case-a")
                           .values(revision=2))
    assert not repository.is_current(submitted.run_id, submitted.attempt_id,
                                     running.fence)


def test_claim_rejects_run_after_case_revision_changes(repository):
    first = _submit(repository)
    with repository.engine.begin() as connection:
        connection.execute(cases.update().where(cases.c.id == "case-a")
                           .values(revision=2))
    with pytest.raises(RepositoryError, match="snapshot_changed"):
        repository.claim(first.run_id, first.attempt_id)
    assert repository.get(first.run_id).status == RunStatus.QUEUED


def test_finish_rejects_answer_after_case_revision_changes(repository):
    first = _submit(repository)
    running = repository.claim(first.run_id, first.attempt_id)
    with repository.engine.begin() as connection:
        connection.execute(cases.update().where(cases.c.id == "case-a")
                           .values(revision=2))
    with pytest.raises(RepositoryError, match="snapshot_changed"):
        repository.finish(first.run_id, first.attempt_id, running.fence,
                          "snapshot-a", RunStatus.COMPLETED, "stale")
    with repository.engine.connect() as connection:
        assert connection.scalar(select(agent_runs.c.answer_text)) is None


def test_heartbeat_keeps_run_live_then_stale_recovery_fences_late_answer(repository):
    submitted = _submit(repository)
    running = repository.claim(submitted.run_id, submitted.attempt_id)
    with repository.engine.connect() as connection:
        claimed_at = connection.scalar(select(agent_runs.c.updated_at))
    beat_at = claimed_at + timedelta(seconds=30)
    assert repository.heartbeat(submitted.run_id, submitted.attempt_id,
                                running.fence, now=beat_at)
    assert repository.recover_stale(
        now=beat_at + timedelta(seconds=119), stale_after_seconds=120) == 0
    assert repository.recover_stale(
        now=beat_at + timedelta(seconds=121), stale_after_seconds=120) == 1
    interrupted = repository.get(submitted.run_id)
    assert interrupted.status == RunStatus.INTERRUPTED
    assert interrupted.fence == running.fence + 1
    with repository.engine.connect() as connection:
        run_row = connection.execute(select(agent_runs).where(
            agent_runs.c.id == submitted.run_id)).mappings().one()
        attempt = connection.execute(select(run_attempts).where(
            run_attempts.c.id == submitted.attempt_id)).mappings().one()
    assert run_row["error_code"] == "worker_lost"
    assert run_row["answer_text"] is None
    assert attempt["outcome_unknown"] is True
    assert attempt["status"] == "interrupted"
    assert not repository.heartbeat(submitted.run_id, submitted.attempt_id,
                                    running.fence, now=beat_at + timedelta(seconds=122))
    with pytest.raises(RepositoryError, match="stale_attempt"):
        repository.finish(submitted.run_id, submitted.attempt_id, running.fence,
                          running.snapshot_id, RunStatus.COMPLETED, "late")
    retry = repository.retry(submitted.run_id, running.snapshot_id,
                             running.revision, "manual-after-loss")
    assert retry.attempt_id != submitted.attempt_id


def test_stale_sweep_ignores_finished_run(repository):
    submitted = _submit(repository)
    running = repository.claim(submitted.run_id, submitted.attempt_id)
    repository.finish(submitted.run_id, submitted.attempt_id, running.fence,
                      running.snapshot_id, RunStatus.COMPLETED, "done")
    assert repository.recover_stale(now=NOW + timedelta(days=30),
                                    stale_after_seconds=120) == 0
    assert repository.get(submitted.run_id).status == RunStatus.COMPLETED


def test_background_heartbeat_refreshes_running_run(repository):
    from backend.app.db.run_heartbeat import run_heartbeat

    submitted = _submit(repository)
    running = repository.claim(submitted.run_id, submitted.attempt_id)
    with repository.engine.connect() as connection:
        before = connection.scalar(select(agent_runs.c.updated_at))
    with run_heartbeat(repository, running, interval_seconds=0.02):
        deadline = monotonic() + 1
        while monotonic() < deadline:
            with repository.engine.connect() as connection:
                updated = connection.scalar(select(agent_runs.c.updated_at))
            if updated > before:
                break
            sleep(0.01)
        else:
            pytest.fail("running heartbeat did not update its lease")
    repository.finish(submitted.run_id, submitted.attempt_id, running.fence,
                      running.snapshot_id, RunStatus.COMPLETED, "done")
