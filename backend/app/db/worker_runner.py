"""Guard a broker-delivered Run before creating or calling a model provider."""

from collections.abc import Callable

from sqlalchemy import Engine

from ..providers import Provider, ProviderError
from ..runtime.lifecycle import RunStatus
from .run_executor import ExecutionReport, execute_run
from .run_repository import RepositoryError, RunRepository


def _ignored(run_id: str, attempt_id: str,
             code: str = "stale_attempt") -> ExecutionReport:
    return ExecutionReport(run_id, attempt_id, "ignored", "", code, 0)


def execute_queued_run(engine: Engine, run_id: str, attempt_id: str,
                       provider_factory: Callable[[], Provider]) -> ExecutionReport:
    runs = RunRepository(engine)
    try:
        state = runs.get(run_id)
    except RepositoryError as exc:
        if exc.code == "run_not_found":
            return _ignored(run_id, attempt_id, exc.code)
        raise
    if state.status != RunStatus.QUEUED or state.active_attempt_id != attempt_id:
        return _ignored(run_id, attempt_id)
    try:
        provider = provider_factory()
    except Exception as exc:  # noqa: BLE001 - setup details must not reach Run or logs
        code = exc.code if isinstance(exc, ProviderError) else "worker_setup_failed"
        try:
            claimed = runs.claim(run_id, attempt_id)
        except RepositoryError as claim_error:
            if claim_error.code in {"stale_attempt", "snapshot_changed"}:
                return _ignored(run_id, attempt_id, claim_error.code)
            raise
        answer = "模型服务准备失败，请检查配置后重试。"
        try:
            runs.finish(run_id, attempt_id, claimed.fence, claimed.snapshot_id,
                        RunStatus.FAILED, answer_text=answer,
                        error_code=code)
        except RepositoryError as finish_error:
            if finish_error.code in {"stale_attempt", "snapshot_changed"}:
                return _ignored(run_id, attempt_id, finish_error.code)
            raise
        return ExecutionReport(run_id, attempt_id, "failed", answer,
                               code, 0)
    try:
        return execute_run(engine, run_id, attempt_id, provider)
    except RepositoryError as exc:
        if exc.code in {"stale_attempt", "snapshot_changed"}:
            return _ignored(run_id, attempt_id, exc.code)
        raise
