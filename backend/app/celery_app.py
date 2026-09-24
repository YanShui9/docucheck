import os

from celery import Celery


app = Celery("docucheck", broker=os.environ["DOCUCHECK_CELERY_BROKER_URL"])
app.conf.update(task_ignore_result=True)
