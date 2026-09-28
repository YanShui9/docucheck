"""M3 run transitions protect the current attempt from late results."""

import pytest

from backend.app.runtime.lifecycle import (
    RunStatus,
    RunTransitionError,
    cancel,
    claim,
    finish,
    interrupt,
    new_run,
    resume,
    retry,
)


def _claimed():
    return claim(new_run("run-1", "snapshot-1", 1, "attempt-1"), "attempt-1")


def test_run_can_be_claimed_once_and_finished_by_current_attempt():
    running = _claimed()

    assert running.status == RunStatus.RUNNING
    assert running.fence == 1
    assert finish(running, "attempt-1", running.fence, "snapshot-1",
                  RunStatus.COMPLETED).status == RunStatus.COMPLETED
    with pytest.raises(RunTransitionError, match="invalid_transition"):
        claim(running, "attempt-1")


def test_cancel_prevents_late_model_result_from_publishing():
    running = _claimed()
    cancelled = cancel(running)

    assert cancelled.status == RunStatus.CANCELLED
    with pytest.raises(RunTransitionError, match="stale_attempt"):
        finish(cancelled, "attempt-1", running.fence, "snapshot-1",
               RunStatus.COMPLETED)


def test_retry_creates_new_attempt_and_rejects_old_result():
    running = _claimed()
    interrupted = interrupt(running, "attempt-1", running.fence)
    queued = retry(interrupted, "attempt-2", "snapshot-1", 1)

    assert queued.status == RunStatus.QUEUED
    assert queued.active_attempt_id == "attempt-2"
    with pytest.raises(RunTransitionError, match="stale_attempt"):
        finish(queued, "attempt-1", running.fence, "snapshot-1",
               RunStatus.COMPLETED)
    next_running = claim(queued, "attempt-2")
    assert next_running.fence > running.fence


def test_clarification_resume_keeps_run_and_snapshot_but_replaces_attempt():
    running = _claimed()
    waiting = finish(running, "attempt-1", running.fence, "snapshot-1",
                     RunStatus.WAITING_USER)
    queued = resume(waiting, "attempt-2", "snapshot-1", 1)

    assert queued.run_id == running.run_id
    assert queued.snapshot_id == running.snapshot_id
    assert queued.active_attempt_id == "attempt-2"
    with pytest.raises(RunTransitionError, match="stale_attempt"):
        finish(queued, "attempt-1", running.fence, "snapshot-1",
               RunStatus.COMPLETED)


def test_retry_and_resume_reject_changed_input_revision():
    running = _claimed()
    interrupted = interrupt(running, "attempt-1", running.fence)
    waiting = finish(running, "attempt-1", running.fence, "snapshot-1",
                     RunStatus.WAITING_USER)

    with pytest.raises(RunTransitionError, match="snapshot_changed"):
        retry(interrupted, "attempt-2", "snapshot-2", 2)
    with pytest.raises(RunTransitionError, match="snapshot_changed"):
        resume(waiting, "attempt-2", "snapshot-1", 2)


def test_stale_fence_and_snapshot_cannot_finish_current_attempt():
    running = _claimed()

    with pytest.raises(RunTransitionError, match="stale_attempt"):
        finish(running, "attempt-1", running.fence - 1, "snapshot-1",
               RunStatus.COMPLETED)
    with pytest.raises(RunTransitionError, match="stale_attempt"):
        finish(running, "attempt-1", running.fence, "snapshot-2",
               RunStatus.COMPLETED)
