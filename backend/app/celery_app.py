import os

from celery import Celery

app = Celery("docucheck", broker=os.environ["DOCUCHECK_CELERY_BROKER_URL"])
app.conf.update(
    task_ignore_result=True,
    task_acks_late=True,
    task_default_queue="runs",
    task_serializer="json",
    accept_content=["json"],
    worker_prefetch_multiplier=1,
    broker_transport_options={"visibility_timeout": 3600},
    broker_connection_retry_on_startup=True,
)

from . import worker_tasks  # noqa: F401 - register task after app exists
