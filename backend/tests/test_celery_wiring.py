"""The Linux worker registers the named Run task and consumes one long job."""


def test_celery_run_task_is_registered(monkeypatch):
    monkeypatch.setenv("DOCUCHECK_CELERY_BROKER_URL", "redis://127.0.0.1:6379/0")
    from backend.app.celery_app import app

    assert "docucheck.execute_run" in app.tasks
    assert app.conf.task_acks_late is True
    assert app.conf.worker_prefetch_multiplier == 1
