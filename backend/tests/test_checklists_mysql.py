"""Opt-in MySQL checklist races; isolated fixtures and no model/queue dispatch."""

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Barrier, Event
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, delete, insert, select, update

from backend.app.db.case_cleanup import CaseCleanup
from backend.app.db.case_deletion import CaseDeletion
from backend.app.db.checklist_repository import ChecklistError, ChecklistRepository
from backend.app.db.connection import database_url
from backend.app.db.models import (
    agent_runs,
    cases,
    checklist_heads,
    checklist_operations,
    checklist_versions,
    conversations,
    input_snapshots,
    outbox,
)
from backend.app.db.run_repository import RepositoryError, RunRepository, SubmitRun
from backend.tests.test_checklists import ACTOR, definition

pytestmark = pytest.mark.skipif(
    os.getenv("DOCUCHECK_RUN_MYSQL_TESTS") != "1", reason="explicit MySQL opt-in"
)


@pytest.fixture
def mysql_checklist(tmp_path):
    engine = create_engine(
        database_url(), pool_size=4, pool_pre_ping=True, hide_parameters=True
    )
    case_id, snapshot_id, conversation_id = [uuid4().hex for _ in range(3)]
    now = datetime.now(UTC).replace(tzinfo=None)
    created = False
    try:
        with engine.begin() as conn:
            conn.execute(
                insert(cases).values(
                    id=case_id, revision=1, created_at=now, updated_at=now
                )
            )
            conn.execute(
                insert(input_snapshots).values(
                    id=snapshot_id, case_id=case_id, revision=1, created_at=now
                )
            )
            conn.execute(
                insert(conversations).values(
                    id=conversation_id, case_id=case_id, created_at=now
                )
            )
        created = True
        yield engine, case_id, snapshot_id, conversation_id
    finally:
        try:
            if created:
                CaseDeletion(engine).request(case_id, 1)
                cleaner = CaseCleanup(engine, tmp_path / "data")
                claim = cleaner.claim(case_id=case_id)
                if claim:
                    assert cleaner.process(claim)["status"] == "deleted"
                with engine.begin() as conn:
                    for table in (
                        checklist_heads,
                        checklist_versions,
                        checklist_operations,
                    ):
                        assert (
                            conn.execute(
                                select(table).where(table.c.case_id == case_id)
                            ).first()
                            is None
                        )
                    conn.execute(delete(cases).where(cases.c.id == case_id))
        finally:
            engine.dispose()


def save(engine, case_id, snapshot_id, key="save"):
    return ChecklistRepository(engine).put(
        case_id,
        snapshot_id,
        definition(2),
        actor=ACTOR,
        expected_revision=1,
        expected_checklist_revision=0,
        idempotency_key=key,
        reason="本次采购要求",
    )


def test_mysql_concurrent_save_has_one_winner_and_versions_survive_reopen(
    mysql_checklist,
):
    engine, case_id, snapshot_id, _ = mysql_checklist
    barrier = Barrier(2)

    def attempt(key):
        barrier.wait(timeout=10)
        try:
            return save(engine, case_id, snapshot_id, key)["checklist_revision"]
        except ChecklistError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        result = list(pool.map(attempt, ["first", "second"]))
    assert sorted(map(str, result)) == ["1", "checklist_revision_conflict"]
    service = ChecklistRepository(engine)
    first = service.confirm(
        case_id,
        snapshot_id,
        ["rice-0"],
        actor=ACTOR,
        expected_revision=1,
        expected_checklist_revision=1,
        confirmed=True,
        idempotency_key="confirm-first",
        reason="部分确认",
    )
    reopened = create_engine(database_url(), pool_pre_ping=True, hide_parameters=True)
    try:
        service = ChecklistRepository(reopened)
        assert (
            service.get(case_id, snapshot_id)["confirmed_version"]["id"]
            == first["confirmed_version"]["id"]
        )
        second = service.confirm(
            case_id,
            snapshot_id,
            ["rice-1"],
            actor=ACTOR,
            expected_revision=1,
            expected_checklist_revision=2,
            confirmed=True,
            idempotency_key="confirm-second",
            reason="确认剩余",
        )
        assert second["state"] == "confirmed"
        assert len(second["confirmed_version"]["definition"]["criteria"]) == 2
        assert (
            len(
                service.version(case_id, first["confirmed_version"]["id"])[
                    "definition"
                ]["criteria"]
            )
            == 1
        )
    finally:
        reopened.dispose()


@pytest.mark.parametrize("operation", ["submit", "retry"])
def test_mysql_old_runner_cannot_miss_checklist_committed_after_read_view(
    mysql_checklist, operation
):
    engine, case_id, snapshot_id, conversation_id = mysql_checklist
    read_done, checklist_committed = Event(), Event()

    class IsolatedRunner(RunRepository):
        def _dispatch(self, connection, run_id, attempt_id, timestamp):
            super()._dispatch(connection, run_id, attempt_id, timestamp)
            connection.execute(
                update(outbox)
                .where(outbox.c.run_id == run_id)
                .values(event_type="test.checklist." + case_id[:16])
            )

    def request(key):
        return SubmitRun(
            case_id=case_id,
            snapshot_id=snapshot_id,
            conversation_id=conversation_id,
            expected_revision=1,
            content="请核对采购要求",
            idempotency_key=key,
            mode="thorough",
            workflow="legacy",
        )

    if operation == "retry":
        receipt = IsolatedRunner(engine).submit(request("initial"))
        with engine.begin() as conn:
            conn.execute(
                update(agent_runs)
                .where(agent_runs.c.id == receipt.run_id)
                .values(status="failed")
            )

    class PausedRunner(IsolatedRunner):
        replays = 0

        def pause(self):
            read_done.set()
            assert checklist_committed.wait(timeout=10)

        def _replay(self, connection, *args):
            result = super()._replay(connection, *args)
            self.replays += 1
            if operation == "submit" and self.replays == 2:
                self.pause()  # The in-transaction ordinary SELECT has opened a read view.
            return result

        def _read_run(self, connection, *args):
            result = super()._read_run(connection, *args)
            if operation == "retry":
                self.pause()
            return result

    def execute():
        runner = PausedRunner(engine)
        try:
            return (
                runner.submit(request("race"))
                if operation == "submit"
                else runner.retry(receipt.run_id, snapshot_id, 1, "retry")
            )
        except RepositoryError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(execute)
        try:
            assert read_done.wait(timeout=10)
            assert save(engine, case_id, snapshot_id)["state"] == "draft"
        finally:
            checklist_committed.set()
        assert future.result(timeout=15) == "checklist_execution_pending"
