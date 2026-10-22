"""Opt-in isolated MySQL precision and handoff transaction checks; no model calls."""

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select, text, update

from backend.app.api.auth import LocalAuthenticator
from backend.app.api.main import create_app
from backend.app.db.case_cleanup import CaseCleanup
from backend.app.db.case_deletion import CaseDeletion
from backend.app.db.connection import database_url
from backend.app.db.models import (
    agent_runs,
    conversation_summaries,
    messages,
    outbox,
    run_events,
    task_memory,
)
from backend.app.db.run_repository import RunRepository
from backend.app.db.task_handoff_repository import TaskHandoffRepository
from backend.app.runtime.lifecycle import RunStatus

pytestmark = pytest.mark.skipif(
    os.getenv("DOCUCHECK_RUN_MYSQL_TESTS") != "1", reason="explicit MySQL opt-in"
)


@pytest.fixture
def mysql_handoff(tmp_path, monkeypatch):
    engine = create_engine(database_url(), pool_pre_ping=True, hide_parameters=True)
    case_id = None
    dispatch = RunRepository._dispatch

    def isolated(self, connection, run_id, attempt_id, timestamp):
        dispatch(self, connection, run_id, attempt_id, timestamp)
        connection.execute(update(outbox).where(outbox.c.run_id == run_id).values(event_type="test.b2"))

    monkeypatch.setattr(RunRepository, "_dispatch", isolated)
    monkeypatch.setenv("DOCUCHECK_DATA_DIR", str(tmp_path / "data"))
    try:
        with TestClient(create_app(engine, LocalAuthenticator())) as client:
            case_id = client.post("/api/v1/cases").json()["id"]

            def send(question, *, finish=True):
                conv = client.post(f"/api/v1/cases/{case_id}/conversations").json()["id"]
                receipt = client.post(f"/api/v1/conversations/{conv}/messages",
                    headers={"Idempotency-Key": uuid4().hex}, json={"content": question,
                        "operation": "assistant", "expected_revision": 0})
                assert receipt.status_code == 202, receipt.text
                receipt = receipt.json()
                runs = RunRepository(engine)
                claim = runs.claim(receipt["run_id"], receipt["attempt_id"])
                if finish:
                    runs.finish(claim.run_id, claim.active_attempt_id, claim.fence, claim.snapshot_id,
                        RunStatus.COMPLETED, answer_text="历史讨论。", memory_json={"version": 5})
                return claim

            yield engine, send
    finally:
        if case_id:
            CaseDeletion(engine).request(case_id, 0)
            cleaner = CaseCleanup(engine, tmp_path / "data")
            claim = cleaner.claim(case_id=case_id)
            if claim:
                assert cleaner.process(claim)["status"] == "deleted"
        engine.dispose()


def test_mysql_rounded_message_time_does_not_hide_prior_precise_phase(mysql_handoff):
    engine, send = mysql_handoff
    old = send("较早的采购目标")
    current = send("继续", finish=False)
    base = datetime(2026, 10, 22, 12, tzinfo=UTC).replace(tzinfo=None)
    with engine.begin() as connection:
        assert "TIME_TRUNCATE_FRACTIONAL" not in connection.scalar(text("SELECT @@sql_mode"))
        for run in (old, current):
            message_id = connection.scalar(select(agent_runs.c.message_id).where(agent_runs.c.id == run.run_id))
            connection.execute(update(messages).where(messages.c.id == message_id).values(created_at=base + timedelta(microseconds=700000)))
            connection.execute(update(agent_runs).where(agent_runs.c.id == run.run_id).values(created_at=base + timedelta(microseconds=700000)))
            assert connection.scalar(select(messages.c.created_at).where(messages.c.id == message_id)) == base + timedelta(seconds=1)
        connection.execute(update(task_memory).where(task_memory.c.source_run_id == old.run_id).values(created_at=base + timedelta(microseconds=750000)))
        connection.execute(update(run_events).where(run_events.c.run_id == current.run_id, run_events.c.kind == "queued").values(
            summary_json={"task_handoff_cutoff_at": (base + timedelta(microseconds=800000)).isoformat(timespec="microseconds")}))
    service = TaskHandoffRepository(engine)
    view = service.prepare(current)
    assert view["context"]["goal"]["text"] == "较早的采购目标"
    service.validate(current, view)


def test_mysql_legacy_rounded_root_does_not_adopt_later_phase(mysql_handoff):
    engine, send = mysql_handoff
    old = send("不能继承的稍晚历史")
    current = send("旧记录", finish=False)
    base = datetime(2026, 10, 22, 12, tzinfo=UTC).replace(tzinfo=None)
    with engine.begin() as connection:
        message_id = connection.scalar(select(agent_runs.c.message_id).where(agent_runs.c.id == current.run_id))
        connection.execute(update(messages).where(messages.c.id == message_id).values(created_at=base + timedelta(microseconds=700000)))
        connection.execute(update(run_events).where(run_events.c.run_id == current.run_id, run_events.c.kind == "queued").values(summary_json={}))
        connection.execute(update(task_memory).where(task_memory.c.source_run_id == old.run_id).values(created_at=base + timedelta(microseconds=900000)))
    assert TaskHandoffRepository(engine).prepare(current)["context"] is None


def test_mysql_concurrent_prepare_persists_one_immutable_record(mysql_handoff):
    engine, send = mysql_handoff
    send("历史任务目标")
    current = send("继续", finish=False)
    barrier = Barrier(2)

    def prepare():
        barrier.wait(timeout=10)
        return TaskHandoffRepository(engine).prepare(current)

    with ThreadPoolExecutor(max_workers=2) as pool:
        pending = [pool.submit(prepare) for _ in range(2)]
        views = [item.result(timeout=30) for item in pending]
    assert views[0] == views[1]
    with engine.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(conversation_summaries).where(
            conversation_summaries.c.summary_json["source_run_id"].as_string() == current.run_id)) == 1
