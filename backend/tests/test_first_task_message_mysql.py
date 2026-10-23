"""Explicit opt-in only: precision and frozen lookup under real InnoDB locks."""

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier, Event

import pytest
from sqlalchemy import func, select, update

from backend.app.db.first_task_message_repository import FirstTaskMessageRepository
from backend.app.db.models import (
    agent_runs,
    cases,
    conversation_summaries,
    messages,
    run_events,
)
from backend.tests import test_task_handoff_mysql

mysql_handoff = test_task_handoff_mysql.mysql_handoff
pytestmark = pytest.mark.skipif(os.getenv("DOCUCHECK_RUN_MYSQL_TESTS") != "1", reason="explicit MySQL opt-in")


def test_mysql_same_rounded_second_uses_precise_cutoffs(mysql_handoff):
    engine, send = mysql_handoff
    old = send("最早原话")
    later = send("稍晚原话")
    current = send("查询", finish=False)
    base = datetime(2026, 10, 23, 12, tzinfo=UTC).replace(tzinfo=None)
    with engine.begin() as connection:
        for run, offset in [(old, 600000), (later, 700000), (current, 800000)]:
            message_id = connection.scalar(select(agent_runs.c.message_id).where(agent_runs.c.id == run.run_id))
            connection.execute(update(messages).where(messages.c.id == message_id).values(created_at=base + timedelta(microseconds=700000)))
            assert connection.scalar(select(messages.c.created_at).where(messages.c.id == message_id)) == base + timedelta(seconds=1)
            connection.execute(update(run_events).where(run_events.c.run_id == run.run_id, run_events.c.kind == "queued").values(
                summary_json={"task_handoff_cutoff_at": (base + timedelta(microseconds=offset)).isoformat(timespec="microseconds")}))
    service = FirstTaskMessageRepository(engine)
    assert service.read(current, service.prepare(current))["text"] == "最早原话"


def test_mysql_concurrent_lookup_freezes_one_record(mysql_handoff):
    engine, send = mysql_handoff
    send("不可变原话")
    current = send("查询", finish=False)
    barrier = Barrier(2)

    def prepare():
        barrier.wait(timeout=10)
        return FirstTaskMessageRepository(engine).prepare(current)

    with ThreadPoolExecutor(max_workers=2) as pool:
        views = [job.result(timeout=30) for job in [pool.submit(prepare), pool.submit(prepare)]]
    assert views[0] == views[1]
    with engine.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(conversation_summaries).where(
            conversation_summaries.c.summary_json["kind"].as_string() == "first_task_message_v1",
            conversation_summaries.c.summary_json["source_run_id"].as_string() == current.run_id)) == 1


def test_mysql_read_waits_for_case_lock_and_observes_changed_source(mysql_handoff):
    engine, send = mysql_handoff
    old = send("旧原话")
    current = send("查询", finish=False)
    service = FirstTaskMessageRepository(engine)
    view = service.prepare(current)
    started = Event()

    def read():
        started.set()
        return service.read(current, view)

    with ThreadPoolExecutor(max_workers=1) as pool:
        with engine.begin() as connection:
            case_id = connection.scalar(select(agent_runs.c.case_id).where(agent_runs.c.id == current.run_id))
            connection.execute(select(cases.c.id).where(cases.c.id == case_id).with_for_update()).one()
            message_id = connection.scalar(select(agent_runs.c.message_id).where(agent_runs.c.id == old.run_id))
            connection.execute(update(messages).where(messages.c.id == message_id).values(content="已变更"))
            future = pool.submit(read)
            assert started.wait(timeout=5)
        with pytest.raises(ValueError):
            future.result(timeout=30)
