"""Opt-in MySQL dynamic binding, immutable progress and fencing checks."""

import os
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from threading import Barrier, Event, local

import pytest
from sqlalchemy import event, select, update

from backend.app.db.checklist_repository import ChecklistRepository
from backend.app.db.dynamic_repository import DynamicRepository
from backend.app.db.models import dynamic_batches, outbox
from backend.app.db.run_repository import RepositoryError, RunRepository, SubmitRun
from backend.app.evidence.repository import digest
from backend.app.runtime.lifecycle import RunStatus
from backend.tests import test_checklists_mysql
from backend.tests.test_checklists import ACTOR, definition

mysql_checklist = test_checklists_mysql.mysql_checklist

pytestmark = pytest.mark.skipif(
    os.getenv("DOCUCHECK_RUN_MYSQL_TESTS") != "1", reason="explicit MySQL opt-in"
)


def submitted(fixture):
    engine, case_id, snapshot_id, conversation_id = fixture
    service = ChecklistRepository(engine)
    service.put(
        case_id,
        snapshot_id,
        definition(1),
        actor=ACTOR,
        expected_revision=1,
        expected_checklist_revision=0,
        idempotency_key="draft",
        reason="采购方要求",
    )
    confirmed = service.confirm(
        case_id,
        snapshot_id,
        ["rice-0"],
        actor=ACTOR,
        expected_revision=1,
        expected_checklist_revision=1,
        confirmed=True,
        idempotency_key="confirm",
        reason="确认",
    )

    class Isolated(RunRepository):
        def _dispatch(self, connection, run_id, attempt_id, timestamp):
            super()._dispatch(connection, run_id, attempt_id, timestamp)
            connection.execute(
                update(outbox)
                .where(outbox.c.run_id == run_id)
                .values(event_type="test.dynamic." + case_id[:16])
            )

    runs = Isolated(engine)
    receipt = runs.submit(
        SubmitRun(
            case_id, conversation_id, snapshot_id, 1, "审查", "standard", "review"
        )
    )
    return runs, receipt, confirmed


def test_mysql_dynamic_binding_progress_survive_attempt_retry(mysql_checklist):
    engine, _, snapshot_id, _ = mysql_checklist
    runs, receipt, version = submitted(mysql_checklist)
    store = DynamicRepository(engine)
    first = runs.claim(receipt.run_id, receipt.attempt_id)
    binding = store.binding(first)
    assert binding["checklist_version_id"] == version["confirmed_version"]["id"]
    batch, fingerprint = digest("batch"), digest("source")
    store.save_batch(first, batch, fingerprint, {"facts": []})
    runs.finish(
        first.run_id,
        first.active_attempt_id,
        first.fence,
        snapshot_id,
        RunStatus.PARTIAL,
    )
    retry = runs.retry(first.run_id, snapshot_id, 1, "retry")
    second = runs.claim(retry.run_id, retry.attempt_id)
    assert store.load_batch(second, batch, fingerprint) == {"facts": []}
    with pytest.raises(RepositoryError, match="stale_attempt"):
        store.save_batch(first, batch, fingerprint, {"facts": []})
    assert (
        store.binding(second)["checklist_version_id"] == binding["checklist_version_id"]
    )
    runs.cancel(second.run_id)
    ChecklistRepository(engine).put(
        mysql_checklist[1],
        snapshot_id,
        definition(1),
        actor=ACTOR,
        expected_revision=1,
        expected_checklist_revision=2,
        idempotency_key="edit",
        reason="更新要求",
    )
    with (
        engine.connect() as connection,
        pytest.raises(RepositoryError, match="checklist_changed"),
    ):
        store.validate_binding(connection, binding)


def test_mysql_binding_reads_newly_confirmed_version_after_old_read_view(
    mysql_checklist,
):
    engine, case_id, snapshot_id, conversation_id = mysql_checklist
    ready, confirmed = Event(), Event()

    class Paused(RunRepository):
        replays = 0

        def _replay(self, connection, *args):
            result = super()._replay(connection, *args)
            self.replays += 1
            if self.replays == 2:
                ready.set()
                assert confirmed.wait(timeout=15)
            return result

        def _dispatch(self, connection, run_id, attempt_id, timestamp):
            super()._dispatch(connection, run_id, attempt_id, timestamp)
            connection.execute(
                update(outbox)
                .where(outbox.c.run_id == run_id)
                .values(event_type="test.dynamic." + case_id[:16])
            )

    request = SubmitRun(
        case_id,
        conversation_id,
        snapshot_id,
        1,
        "审查",
        "standard",
        "concurrent-confirm",
    )
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(Paused(engine).submit, request)
        try:
            assert ready.wait(timeout=10)
            service = ChecklistRepository(engine)
            service.put(
                case_id,
                snapshot_id,
                definition(1),
                actor=ACTOR,
                expected_revision=1,
                expected_checklist_revision=0,
                idempotency_key="draft",
                reason="要求",
            )
            version = service.confirm(
                case_id,
                snapshot_id,
                ["rice-0"],
                actor=ACTOR,
                expected_revision=1,
                expected_checklist_revision=1,
                confirmed=True,
                idempotency_key="confirm",
                reason="确认",
            )
        finally:
            confirmed.set()
        receipt = future.result(timeout=15)
    claim = RunRepository(engine).claim(receipt.run_id, receipt.attempt_id)
    assert (
        DynamicRepository(engine).binding(claim)["checklist_version_id"]
        == version["confirmed_version"]["id"]
    )


def test_mysql_batch_conflicting_writes_have_single_winner(mysql_checklist):
    engine = mysql_checklist[0]
    runs, receipt, _ = submitted(mysql_checklist)
    claim = runs.claim(receipt.run_id, receipt.attempt_id)
    barrier = Barrier(2)

    def write(n):
        barrier.wait(timeout=10)
        try:
            DynamicRepository(engine).save_batch(
                claim, digest("batch"), digest("source"), {"facts": [], "test": n}
            )
            return "saved"
        except RepositoryError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(write, [1, 2])) == ["batch_source_changed", "saved"]
    with engine.connect() as connection:
        assert (
            len(
                list(
                    connection.scalars(
                        select(dynamic_batches.c.batch_id).where(
                            dynamic_batches.c.run_id == claim.run_id
                        )
                    )
                )
            )
            == 1
        )


@pytest.mark.parametrize("same_output", [False, True])
def test_mysql_batch_writes_refresh_old_read_view(mysql_checklist, same_output):
    """Both transactions see an empty table before serializing on the Case."""
    engine = mysql_checklist[0]
    with engine.connect() as connection:
        assert connection.get_isolation_level() == "REPEATABLE READ"
    runs, receipt, _ = submitted(mysql_checklist)
    claim = runs.claim(receipt.run_id, receipt.attempt_id)
    barrier = Barrier(2)

    class OldReadView(DynamicRepository):
        def _guard(self, connection, current):
            assert connection.scalar(select(dynamic_batches.c.batch_id).where(
                dynamic_batches.c.run_id == current.run_id
            )) is None
            barrier.wait(timeout=10)
            return super()._guard(connection, current)

    def write(n):
        try:
            OldReadView(engine).save_batch(
                claim, digest("batch"), digest("source"),
                {"facts": [], "test": 1 if same_output else n},
            )
            return "saved"
        except RepositoryError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        actual = sorted(pool.map(write, [1, 2]))
    assert actual == (["saved", "saved"] if same_output else ["batch_source_changed", "saved"])
    with engine.connect() as connection:
        rows = connection.execute(select(dynamic_batches).where(
            dynamic_batches.c.run_id == claim.run_id
        )).mappings().all()
    assert len(rows) == 1
    assert rows[0]["output_digest"] == digest(rows[0]["output_json"])


def test_mysql_load_batch_refreshes_old_read_view(mysql_checklist):
    engine = mysql_checklist[0]
    with engine.connect() as connection:
        assert connection.get_isolation_level() == "REPEATABLE READ"
    runs, receipt, _ = submitted(mysql_checklist)
    claim = runs.claim(receipt.run_id, receipt.attempt_id)
    read_started, committed = Event(), Event()
    batch_id, source = digest("batch"), digest("source")
    output = {"facts": [], "test": "committed"}

    class OldReadView(DynamicRepository):
        def _guard(self, connection, current):
            assert connection.scalar(select(dynamic_batches.c.batch_id).where(
                dynamic_batches.c.run_id == current.run_id
            )) is None
            read_started.set()
            assert committed.wait(timeout=10)
            return super()._guard(connection, current)

    with ThreadPoolExecutor(max_workers=1) as pool:
        loaded = pool.submit(OldReadView(engine).load_batch, claim, batch_id, source)
        try:
            assert read_started.wait(timeout=10)
            DynamicRepository(engine).save_batch(claim, batch_id, source, output)
        finally:
            committed.set()
        assert loaded.result(timeout=10) == output


def test_mysql_independent_cases_save_first_batches_without_deadlock_failure(
    mysql_checklist, tmp_path,
):
    engine = mysql_checklist[0]
    with engine.connect() as connection:
        assert connection.get_isolation_level() == "REPEATABLE READ"
        if connection.scalar(select(dynamic_batches.c.batch_id)) is not None:
            pytest.skip("gap-lock reproduction requires an empty isolated schema; keep existing batches")
    make_other = contextmanager(test_checklists_mysql.mysql_checklist.__wrapped__)
    with make_other(tmp_path / "other") as other:
        claims = []
        for fixture in (mysql_checklist, other):
            runs, receipt, _ = submitted(fixture)
            claims.append(runs.claim(receipt.run_id, receipt.attempt_id))
        barrier, thread = Barrier(2), local()

        def before_insert(connection, cursor, statement, parameters, context, many):
            if statement.startswith("INSERT INTO dynamic_batches") and not getattr(thread, "seen", False):
                thread.seen = True
                barrier.wait(timeout=10)

        for current in (engine, other[0]):
            event.listen(current, "before_cursor_execute", before_insert)
        try:
            def save(pair):
                fixture, claim = pair
                DynamicRepository(fixture[0]).save_batch(
                    claim, digest("batch"), digest("source"), {"facts": []},
                )
                return "saved"

            with ThreadPoolExecutor(max_workers=2) as pool:
                assert list(pool.map(save, zip((mysql_checklist, other), claims))) == ["saved", "saved"]
            for fixture, claim in zip((mysql_checklist, other), claims):
                assert DynamicRepository(fixture[0]).load_batch(
                    claim, digest("batch"), digest("source"),
                ) == {"facts": []}
        finally:
            for current in (engine, other[0]):
                event.remove(current, "before_cursor_execute", before_insert)
