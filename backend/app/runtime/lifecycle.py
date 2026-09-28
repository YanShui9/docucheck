"""Pure Run transitions; database writes must repeat these guards atomically."""

from dataclasses import dataclass, replace
from enum import Enum


class RunStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    WAITING_USER = "waiting_user"
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"


class RunTransitionError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class RunState:
    run_id: str
    snapshot_id: str
    revision: int
    status: RunStatus
    active_attempt_id: str
    fence: int


def new_run(run_id: str, snapshot_id: str, revision: int,
            attempt_id: str) -> RunState:
    if not run_id or not snapshot_id or not attempt_id or revision < 1:
        raise RunTransitionError("invalid_run")
    return RunState(run_id, snapshot_id, revision, RunStatus.QUEUED, attempt_id, 0)


def claim(state: RunState, attempt_id: str) -> RunState:
    if state.status != RunStatus.QUEUED:
        raise RunTransitionError("invalid_transition")
    if attempt_id != state.active_attempt_id:
        raise RunTransitionError("stale_attempt")
    return replace(state, status=RunStatus.RUNNING, fence=state.fence + 1)


def _require_current(state: RunState, attempt_id: str, fence: int,
                     snapshot_id: str | None = None) -> None:
    if (state.status != RunStatus.RUNNING or attempt_id != state.active_attempt_id
            or fence != state.fence or (snapshot_id is not None
                                        and snapshot_id != state.snapshot_id)):
        raise RunTransitionError("stale_attempt")


def finish(state: RunState, attempt_id: str, fence: int, snapshot_id: str,
           status: RunStatus) -> RunState:
    _require_current(state, attempt_id, fence, snapshot_id)
    if status not in {RunStatus.COMPLETED, RunStatus.PARTIAL,
                      RunStatus.FAILED, RunStatus.WAITING_USER}:
        raise RunTransitionError("invalid_transition")
    return replace(state, status=status)


def interrupt(state: RunState, attempt_id: str, fence: int) -> RunState:
    _require_current(state, attempt_id, fence)
    return replace(state, status=RunStatus.INTERRUPTED)


def cancel(state: RunState) -> RunState:
    if state.status not in {RunStatus.QUEUED, RunStatus.RUNNING,
                            RunStatus.WAITING_USER, RunStatus.INTERRUPTED}:
        raise RunTransitionError("invalid_transition")
    return replace(state, status=RunStatus.CANCELLED)


def _new_attempt(state: RunState, attempt_id: str, snapshot_id: str,
                 revision: int, allowed: set[RunStatus]) -> RunState:
    if state.status not in allowed or not attempt_id or attempt_id == state.active_attempt_id:
        raise RunTransitionError("invalid_transition")
    if snapshot_id != state.snapshot_id or revision != state.revision:
        raise RunTransitionError("snapshot_changed")
    return replace(state, status=RunStatus.QUEUED, active_attempt_id=attempt_id)


def retry(state: RunState, attempt_id: str, snapshot_id: str, revision: int) -> RunState:
    return _new_attempt(state, attempt_id, snapshot_id, revision,
                        {RunStatus.INTERRUPTED, RunStatus.FAILED, RunStatus.PARTIAL})


def resume(state: RunState, attempt_id: str, snapshot_id: str, revision: int) -> RunState:
    return _new_attempt(state, attempt_id, snapshot_id, revision,
                        {RunStatus.WAITING_USER})
