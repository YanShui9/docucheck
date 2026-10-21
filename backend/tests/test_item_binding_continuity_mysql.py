"""Opt-in Case-serialized continuation races in disposable MySQL fixtures."""

import os
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from threading import Barrier

import pytest
from sqlalchemy import delete, func, select, update

from backend.app.db.checklist_repository import ChecklistError, ChecklistRepository
from backend.app.db.item_binding_repository import (
    ItemBindingError,
    ItemBindingRepository,
    freeze_current,
)
from backend.app.db.models import cases, item_binding_heads, item_binding_versions
from backend.tests import test_checklists_mysql
from backend.tests.test_checklists import ACTOR
from backend.tests.test_item_bindings_mysql import prepare

mysql_checklist = test_checklists_mysql.mysql_checklist
pytestmark = pytest.mark.skipif(
    os.getenv("DOCUCHECK_RUN_MYSQL_TESTS") != "1", reason="explicit MySQL opt-in"
)


def prepare_edit(fixture):
    engine, case_id, snapshot_id, _ = fixture
    body = prepare(fixture)
    original = ItemBindingRepository(engine).put(case_id, snapshot_id, idempotency_key="bind", **body)["current_version"]
    service = ChecklistRepository(engine)
    changed = deepcopy(service.version(case_id, body["checklist_version_id"])["definition"])
    changed["criteria"][0]["rule"]["value"] = "50"
    service.put(case_id, snapshot_id, changed, actor=ACTOR, expected_revision=1,
                expected_checklist_revision=2, idempotency_key="new-draft", reason="变更阈值")
    return original, changed


def confirm(fixture, key):
    engine, case_id, snapshot_id, _ = fixture
    return ChecklistRepository(engine).confirm(case_id, snapshot_id, ["rice-0"], actor=ACTOR,
        expected_revision=1, expected_checklist_revision=3, idempotency_key=key,
        confirmed=True, reason="确认新阈值")


def restore_stale(fixture, original):
    engine, case_id, _, _ = fixture
    with engine.begin() as conn:
        conn.execute(update(item_binding_heads).where(item_binding_heads.c.case_id == case_id).values(
            version_id=original["id"], revision=1))
        conn.execute(delete(item_binding_versions).where(
            item_binding_versions.c.case_id == case_id, item_binding_versions.c.version > 1))


def version_count(fixture):
    engine, case_id, _, _ = fixture
    with engine.connect() as conn:
        return conn.scalar(select(func.count()).select_from(item_binding_versions).where(
            item_binding_versions.c.case_id == case_id))


def test_mysql_concurrent_confirmation_advances_binding_once_and_replays(mysql_checklist):
    engine, case_id, snapshot_id, _ = mysql_checklist
    original, _ = prepare_edit(mysql_checklist)
    barrier = Barrier(2)

    def apply(key):
        barrier.wait(timeout=10)
        try:
            return key, confirm(mysql_checklist, key)
        except ChecklistError as exc:
            return key, exc.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(apply, ["first", "second"]))
    winners = [(key, value) for key, value in results if isinstance(value, dict)]
    assert len(winners) == 1
    assert [value for _, value in results if isinstance(value, str)] == ["checklist_revision_conflict"]
    key, result = winners[0]
    assert confirm(mysql_checklist, key) == result
    current = ItemBindingRepository(engine).get(case_id, snapshot_id)["current_version"]
    assert current["payload"]["continuation"]["version_id"] == original["id"]
    assert version_count(mysql_checklist) == 2


def test_mysql_continuation_uses_current_reads_after_old_rr_view(mysql_checklist):
    engine, case_id, snapshot_id, _ = mysql_checklist
    original, changed = prepare_edit(mysql_checklist)
    with engine.connect() as old:
        assert old.scalar(select(cases.c.id).where(cases.c.id == case_id)) == case_id
        result = confirm(mysql_checklist, "new-confirm")
        restore_stale(mysql_checklist, original)
        frozen = freeze_current(old, case_id, snapshot_id, result["confirmed_version"]["id"], changed,
                                allow_continuation=True)
        assert frozen["version_id"] is not None
        assert frozen["payload"]["entries"] == original["payload"]["entries"]
        old.commit()
    assert version_count(mysql_checklist) == 2


def test_mysql_two_recoveries_share_one_new_immutable_version(mysql_checklist):
    engine, case_id, snapshot_id, _ = mysql_checklist
    original, changed = prepare_edit(mysql_checklist)
    result = confirm(mysql_checklist, "new-confirm")
    restore_stale(mysql_checklist, original)
    barrier = Barrier(2)

    def recover(_):
        barrier.wait(timeout=10)
        with engine.begin() as conn:
            return freeze_current(conn, case_id, snapshot_id, result["confirmed_version"]["id"], changed,
                                  allow_continuation=True)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(recover, range(2)))
    assert results[0] == results[1] and results[0]["version_id"] is not None
    assert version_count(mysql_checklist) == 2


def test_mysql_explicit_clear_and_recovery_cannot_restore_old_scope_after_clear(mysql_checklist):
    engine, case_id, snapshot_id, _ = mysql_checklist
    original, changed = prepare_edit(mysql_checklist)
    result = confirm(mysql_checklist, "new-confirm")
    restore_stale(mysql_checklist, original)
    checklist_id = result["confirmed_version"]["id"]
    barrier = Barrier(2)

    def act(operation):
        barrier.wait(timeout=10)
        if operation == "clear":
            try:
                return ItemBindingRepository(engine).put(case_id, snapshot_id, [], actor=ACTOR,
                    checklist_version_id=checklist_id, expected_revision=1, expected_checklist_revision=4,
                    expected_binding_revision=1, confirmed=True, idempotency_key="clear", reason="明确清空")
            except ItemBindingError as exc:
                return exc.code
        with engine.begin() as conn:
            return freeze_current(conn, case_id, snapshot_id, checklist_id, changed, allow_continuation=True)

    with ThreadPoolExecutor(max_workers=2) as pool:
        cleared, frozen = list(pool.map(act, ["clear", "recover"]))
    current = ItemBindingRepository(engine).get(case_id, snapshot_id)["current_version"]
    if isinstance(cleared, dict):
        assert current["payload"]["entries"] == frozen["payload"]["entries"] == []
        assert "continuation" not in current["payload"]
    else:
        assert cleared == "item_binding_revision_conflict"
        assert current["payload"]["continuation"]["version_id"] == original["id"]
    assert version_count(mysql_checklist) == 2
