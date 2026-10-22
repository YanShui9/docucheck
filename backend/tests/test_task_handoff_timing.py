"""Timestamp contracts simulate lock waits; these are not InnoDB concurrency tests."""

from datetime import timedelta

from sqlalchemy import event, select

from backend.app.db import run_repository as runs_module
from backend.app.db.models import run_events, task_memory
from backend.app.db.run_repository import RunRepository, _now
from backend.app.runtime.lifecycle import RunStatus
from backend.tests.test_document_ingestion import ingestion_case
from backend.tests.test_task_handoff_workflow import conversation, send

__all__ = ["ingestion_case"]


def test_submit_records_precise_boundary_after_case_lock_in_first_queued_event(ingestion_case, monkeypatch):
    client, engine, _, case = ingestion_case
    conv = conversation(client, case)
    before = _now()
    after = before + timedelta(seconds=2, microseconds=123)
    clock = [before]
    monkeypatch.setattr(runs_module, "_now", lambda: clock[0])
    def acquired(_conn, _cursor, statement, _parameters, _context, _many):
        if statement.lstrip().startswith("SELECT cases.revision, cases.deletion_requested_at"):
            clock[0] = after
    event.listen(engine, "after_cursor_execute", acquired)
    try:
        receipt = send(client, conv, "继续历史目标", "precise-cutoff")
    finally:
        event.remove(engine, "after_cursor_execute", acquired)
    with engine.connect() as connection:
        queued = connection.execute(select(run_events).where(run_events.c.run_id == receipt["run_id"],
            run_events.c.kind == "queued").order_by(run_events.c.seq)).mappings().first()
    assert queued["summary_json"]["task_handoff_cutoff_at"] == after.isoformat(timespec="microseconds")


def test_finished_phase_time_is_sampled_after_case_lock_wait(ingestion_case, monkeypatch):
    client, engine, _, case = ingestion_case
    receipt = send(client, conversation(client, case), "待完成历史", "phase")
    runs = RunRepository(engine)
    claim = runs.claim(receipt["run_id"], receipt["attempt_id"])
    before = _now()
    after = before + timedelta(seconds=2)
    clock = [before]
    monkeypatch.setattr(runs_module, "_now", lambda: clock[0])
    original = runs._require_current_case
    def lock_then_advance(connection, run_id):
        original(connection, run_id)
        clock[0] = after
    monkeypatch.setattr(runs, "_require_current_case", lock_then_advance)
    runs.finish(claim.run_id, claim.active_attempt_id, claim.fence, claim.snapshot_id,
        RunStatus.COMPLETED, answer_text="历史已完成", memory_json={"version": 5})
    with engine.connect() as connection:
        phase_time = connection.scalar(select(task_memory.c.created_at).where(task_memory.c.source_run_id == claim.run_id))
    assert phase_time == after
