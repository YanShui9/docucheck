"""Celery entry point for persisted Agent Runs."""

from sqlalchemy import create_engine

from .celery_app import app
from .db.connection import database_url
from .db.worker_runner import execute_queued_run
from .providers import configured_provider


@app.task(name="docucheck.execute_run")
def execute_run_task(run_id: str, attempt_id: str) -> dict:
    engine = create_engine(database_url(), pool_pre_ping=True,
                           hide_parameters=True)
    try:
        report = execute_queued_run(engine, run_id, attempt_id,
                                    configured_provider)
        return {"run_id": run_id, "attempt_id": attempt_id,
                "status": report.status, "error_code": report.error_code,
                "model_calls": report.model_calls}
    except Exception:  # noqa: BLE001 - avoid leaking source/provider details in worker logs
        raise RuntimeError("worker_execution_failed") from None
    finally:
        engine.dispose()
