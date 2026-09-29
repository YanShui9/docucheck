"""Opt-in MySQL fault-state checks; no broker publish or model call."""

import os
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from dotenv import load_dotenv
from sqlalchemy import create_engine, delete, insert, select

from backend.app.db.connection import ROOT, database_url
from backend.app.db.models import (
    agent_runs,
    cases,
    conversations,
    input_snapshots,
    messages,
    outbox,
    run_attempts,
    run_events,
)
from backend.app.db.outbox_repository import OutboxRepository
from backend.app.db.run_repository import RepositoryError, RunRepository
from backend.app.runtime.lifecycle import RunStatus


@pytest.mark.skipif(os.getenv("DOCUCHECK_RUN_MYSQL_TESTS") != "1",
                    reason="explicit MySQL opt-in")
def test_mysql_recovery_fences_lost_worker_and_requeues_lost_broker_message(tmp_path):
    load_dotenv(ROOT / ".env", override=False)
    engine = create_engine(database_url(), pool_pre_ping=True,
                           hide_parameters=True)
    now = datetime.now(UTC).replace(tzinfo=None)
    future = now + timedelta(days=1)
    ids = [{key: uuid4().hex for key in (
        "case", "snapshot", "conversation", "message", "run", "attempt", "outbox",
    )} for _ in range(3)]
    try:
        with engine.begin() as connection:
            for index, item in enumerate(ids):
                running = index == 0
                connection.execute(insert(cases).values(
                    id=item["case"], revision=1, created_at=now, updated_at=now))
                connection.execute(insert(input_snapshots).values(
                    id=item["snapshot"], case_id=item["case"], revision=1,
                    created_at=now))
                connection.execute(insert(conversations).values(
                    id=item["conversation"], case_id=item["case"], created_at=now))
                connection.execute(insert(messages).values(
                    id=item["message"], conversation_id=item["conversation"],
                    role="user", content="离线故障测试", created_at=now))
                connection.execute(insert(agent_runs).values(
                    id=item["run"], case_id=item["case"],
                    conversation_id=item["conversation"],
                    message_id=item["message"], snapshot_id=item["snapshot"],
                    revision=1, mode="standard", active_attempt_id=item["attempt"],
                    status="running" if running else "queued",
                    fence=1 if running else 0,
                    created_at=now,
                    updated_at=now - timedelta(minutes=3) if running else now))
                connection.execute(insert(run_attempts).values(
                    id=item["attempt"], run_id=item["run"], attempt_no=1,
                    status="running" if running else "queued",
                    outcome_unknown=False, created_at=now,
                    started_at=now - timedelta(minutes=3) if running else None))
                connection.execute(insert(outbox).values(
                    id=item["outbox"],
                    event_key=f"run:{item['run']}:attempt:{item['attempt']}:dispatch",
                    run_id=item["run"], attempt_id=item["attempt"],
                    event_type="run.dispatch",
                    payload_json={"run_id": item["run"],
                                  "attempt_id": item["attempt"]},
                    status="delivered", delivery_attempts=1,
                    available_at=(future - timedelta(seconds=1) if index < 2
                                  else future + timedelta(days=1)),
                    delivered_at=now, created_at=now))

        runs, events = RunRepository(engine), OutboxRepository(engine)
        assert runs.recover_stale(now=now) == 1
        lost = runs.get(ids[0]["run"])
        assert lost.status == RunStatus.INTERRUPTED and lost.fence == 2
        with engine.connect() as connection:
            unknown = connection.scalar(select(run_attempts.c.outcome_unknown).where(
                run_attempts.c.id == ids[0]["attempt"]))
        assert unknown is True
        with pytest.raises(RepositoryError, match="stale_attempt"):
            runs.finish(ids[0]["run"], ids[0]["attempt"], 1,
                        ids[0]["snapshot"], RunStatus.COMPLETED, "late")

        assert events.requeue_unclaimed(now=future) == 1
        with engine.connect() as connection:
            row = connection.execute(select(outbox).where(
                outbox.c.id == ids[1]["outbox"])).mappings().one()
        assert row["status"] == "pending", row
        assert row["available_at"] <= future, row
        claims = events.claim(now=future)
        assert len(claims) == 1 and claims[0].run_id == ids[1]["run"], row
        assert claims[0].delivery_attempts == 2
        assert events.mark_delivered(claims[0].id, 2, now=future)
        other = runs.claim(ids[1]["run"], ids[1]["attempt"])
        assert events.requeue_unclaimed(now=future + timedelta(hours=2)) == 0
        runs.finish(ids[1]["run"], ids[1]["attempt"], other.fence,
                    other.snapshot_id, RunStatus.PARTIAL, "test")

        ready = tmp_path / "child-claimed"
        child_code = (
            "import sys,time\n"
            "from pathlib import Path\n"
            "from dotenv import load_dotenv\n"
            "from sqlalchemy import create_engine\n"
            "from backend.app.db.connection import ROOT,database_url\n"
            "from backend.app.db.run_heartbeat import run_heartbeat\n"
            "from backend.app.db.run_repository import RunRepository\n"
            "load_dotenv(ROOT / '.env', override=False)\n"
            "engine=create_engine(database_url(),pool_pre_ping=True,hide_parameters=True)\n"
            "runs=RunRepository(engine)\n"
            "claimed=runs.claim(sys.argv[1],sys.argv[2])\n"
            "with run_heartbeat(runs,claimed,interval_seconds=0.1):\n"
            "    Path(sys.argv[3]).write_text('claimed')\n"
            "    time.sleep(60)\n"
        )
        child = subprocess.Popen(
            [sys.executable, "-c", child_code, ids[2]["run"],
             ids[2]["attempt"], str(ready)],
            cwd=ROOT, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            deadline = time.monotonic() + 10
            while not ready.exists() and time.monotonic() < deadline:
                assert child.poll() is None, "child exited before claim"
                time.sleep(0.05)
            assert ready.exists(), "child did not claim Run"
            with engine.connect() as connection:
                claimed_at = connection.scalar(select(agent_runs.c.updated_at).where(
                    agent_runs.c.id == ids[2]["run"]))
            while time.monotonic() < deadline:
                with engine.connect() as connection:
                    heartbeat_at = connection.scalar(select(
                        agent_runs.c.updated_at).where(
                            agent_runs.c.id == ids[2]["run"]))
                if heartbeat_at > claimed_at:
                    break
                time.sleep(0.05)
            else:
                pytest.fail("child did not heartbeat")
            child.kill()
            child.wait(timeout=5)
            assert runs.recover_stale(
                now=heartbeat_at + timedelta(seconds=121)) == 1
            assert runs.get(ids[2]["run"]).status == RunStatus.INTERRUPTED
        finally:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=5)
    finally:
        with engine.begin() as connection:
            for item in ids:
                connection.execute(delete(run_events).where(
                    run_events.c.run_id == item["run"]))
                connection.execute(delete(outbox).where(
                    outbox.c.id == item["outbox"]))
                connection.execute(delete(run_attempts).where(
                    run_attempts.c.id == item["attempt"]))
                connection.execute(delete(agent_runs).where(
                    agent_runs.c.id == item["run"]))
                connection.execute(delete(messages).where(
                    messages.c.id == item["message"]))
                connection.execute(delete(conversations).where(
                    conversations.c.id == item["conversation"]))
                connection.execute(delete(input_snapshots).where(
                    input_snapshots.c.id == item["snapshot"]))
                connection.execute(delete(cases).where(
                    cases.c.id == item["case"]))
        engine.dispose()
