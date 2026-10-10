"""Opt-in, disposable Case races under real MySQL REPEATABLE READ."""

import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy import insert, select, update

from backend.app.db.checklist_repository import ChecklistRepository
from backend.app.db.item_binding_repository import (
    ItemBindingError,
    ItemBindingRepository,
    freeze_current,
    load_frozen,
)
from backend.app.db.models import cases, document_versions, evidence_blocks, suppliers
from backend.app.evidence.repository import EvidenceRepository
from backend.tests import test_checklists_mysql
from backend.tests.test_checklists import ACTOR
from backend.tests.test_item_bindings import entry, sample

mysql_checklist = test_checklists_mysql.mysql_checklist
pytestmark = pytest.mark.skipif(
    os.getenv("DOCUCHECK_RUN_MYSQL_TESTS") != "1", reason="explicit MySQL opt-in"
)


@pytest.mark.parametrize("workflow", ["auto", "assistant"])
def test_mysql_run_freezes_binding_and_waiting_resume_keeps_it(mysql_checklist, workflow):
    from backend.app.db.assistant_repository import AssistantRepository
    from backend.app.db.dynamic_repository import DynamicRepository
    from backend.app.db.models import assistant_runs, dynamic_runs, outbox
    from backend.app.db.run_repository import RunRepository, SubmitRun
    from backend.app.runtime.lifecycle import RunStatus

    engine, case_id, snapshot_id, conversation_id = mysql_checklist
    body = prepare(mysql_checklist)
    first = ItemBindingRepository(engine).put(case_id, snapshot_id, idempotency_key="bind", **body)["current_version"]

    class Isolated(RunRepository):
        def _dispatch(self, connection, run_id, attempt_id, timestamp):
            super()._dispatch(connection, run_id, attempt_id, timestamp)
            connection.execute(update(outbox).where(outbox.c.run_id == run_id).values(event_type="test.binding." + case_id[:16]))

    runs = Isolated(engine)
    run = runs.submit(SubmitRun(case_id, conversation_id, snapshot_id, 1, "审查", "standard", "run", workflow=workflow))
    table = assistant_runs if workflow == "assistant" else dynamic_runs
    with engine.connect() as connection:
        row = connection.execute(select(table).where(table.c.run_id == run.run_id)).mappings().one()
    assert row["item_binding_version_id"] == first["id"] and row["item_binding_digest"] == first["digest"]
    claim = runs.claim(run.run_id, run.attempt_id)
    runs.finish(run.run_id, claim.active_attempt_id, claim.fence, snapshot_id, RunStatus.WAITING_USER, answer_text="等待", memory_json={})
    ItemBindingRepository(engine).put(case_id, snapshot_id, idempotency_key="clear",
        **(body | {"entries": [], "expected_binding_revision": 1}))
    resumed = runs.resume(run.run_id, snapshot_id, 1, "resume", "继续")
    claim = runs.claim(run.run_id, resumed.attempt_id)
    binding = (AssistantRepository(engine).register_formal(claim, "review") if workflow == "assistant"
               else DynamicRepository(engine).binding(claim))
    assert binding["item_binding_version_id"] == first["id"]
    assert binding["item_bindings"]["digest"] == first["digest"]
    assert not DynamicRepository(engine).current(run.run_id)
    runs.cancel(run.run_id)


def prepare(fixture):
    from datetime import UTC, datetime

    engine, case_id, snapshot_id, _ = fixture
    spec, original = sample()
    doc_id, supplier_id = uuid4().hex, uuid4().hex
    document = original.documents[0]
    parsed = replace(
        document.parsed,
        document_id=doc_id,
        blocks=tuple(replace(b, document_id=doc_id) for b in document.parsed.blocks),
    )
    repo = EvidenceRepository(
        case_id, 1, [replace(document, document_id=doc_id, parsed=parsed)]
    )
    now = datetime.now(UTC).replace(tzinfo=None)
    with engine.begin() as connection:
        connection.execute(
            insert(suppliers).values(
                id=supplier_id,
                case_id=case_id,
                handle="A",
                name="合成供应商",
                created_at=now,
            )
        )
        connection.execute(
            insert(document_versions).values(
                id=doc_id,
                case_id=case_id,
                snapshot_id=snapshot_id,
                role="quote",
                supplier_id=supplier_id,
                model_name="苹果手机",
                original_name="报价.xlsx",
                storage_key="uploads/aa/" + uuid4().hex + ".xlsx",
                source_sha256="a" * 64,
                media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                parser_version="parser",
                parse_status="complete",
                coverage_json={
                    "parser_version_full": "parser",
                    "total_sheets": 1,
                    "processed_sheets": ["报价"],
                },
                created_at=now,
            )
        )
        for block in repo.blocks:
            connection.execute(
                insert(evidence_blocks).values(
                    id=block.evidence_id,
                    document_version_id=doc_id,
                    ordinal=block.ordinal,
                    source_text=block.text,
                    sheet=block.sheet,
                    cell=block.cell,
                    created_at=now,
                )
            )
    checklists = ChecklistRepository(engine)
    checklists.put(
        case_id,
        snapshot_id,
        spec.model_dump(mode="json"),
        actor=ACTOR,
        expected_revision=1,
        expected_checklist_revision=0,
        idempotency_key="draft",
        reason="合成要求",
    )
    confirmed = checklists.confirm(
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
    binding = entry(repo) | {"document_id": doc_id}
    return {
        "entries": [binding],
        "actor": ACTOR,
        "checklist_version_id": confirmed["confirmed_version"]["id"],
        "expected_revision": 1,
        "expected_checklist_revision": 2,
        "expected_binding_revision": 0,
        "confirmed": True,
        "reason": "合成关联",
    }


def test_mysql_concurrent_binding_revision_has_one_winner(mysql_checklist):
    engine, case_id, snapshot_id, _ = mysql_checklist
    body, barrier = prepare(mysql_checklist), Barrier(2)

    def save(key):
        barrier.wait(timeout=10)
        try:
            return ItemBindingRepository(engine).put(
                case_id, snapshot_id, idempotency_key=key, **body
            )["binding_revision"]
        except ItemBindingError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        result = list(pool.map(save, ["first", "second"]))
    assert sorted(map(str, result)) == ["1", "item_binding_revision_conflict"]


def test_mysql_concurrent_same_key_replays_exact_receipt(mysql_checklist):
    engine, case_id, snapshot_id, _ = mysql_checklist
    body, barrier = prepare(mysql_checklist), Barrier(2)

    def save(_):
        barrier.wait(timeout=10)
        return ItemBindingRepository(engine).put(
            case_id, snapshot_id, idempotency_key="same", **body
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(save, range(2)))
    assert results[0] == results[1] and results[0]["binding_revision"] == 1


def test_mysql_freeze_current_ignores_old_read_view(mysql_checklist):
    engine, case_id, snapshot_id, _ = mysql_checklist
    body = prepare(mysql_checklist)
    definition = ChecklistRepository(engine).version(
        case_id, body["checklist_version_id"]
    )["definition"]
    with engine.connect() as old:
        assert old.scalar(select(cases.c.id).where(cases.c.id == case_id)) == case_id
        receipt = ItemBindingRepository(engine).put(
            case_id, snapshot_id, idempotency_key="new-head", **body
        )
        frozen = freeze_current(
            old, case_id, snapshot_id, body["checklist_version_id"], definition
        )
        assert frozen["version_id"] == receipt["current_version"]["id"]
        old.rollback()
        assert (
            old.scalar(
                select(cases.c.deletion_requested_at).where(cases.c.id == case_id)
            )
            is None
        )
        from datetime import UTC, datetime

        with engine.begin() as latest:
            latest.execute(
                update(cases)
                .where(cases.c.id == case_id)
                .values(deletion_requested_at=datetime.now(UTC).replace(tzinfo=None))
            )
        try:
            with pytest.raises(ItemBindingError, match="case_deleting"):
                freeze_current(
                    old, case_id, snapshot_id, body["checklist_version_id"], definition
                )
            old.rollback()
            with pytest.raises(ItemBindingError, match="case_deleting"):
                load_frozen(
                    old,
                    case_id,
                    snapshot_id,
                    body["checklist_version_id"],
                    definition,
                    frozen["version_id"],
                    frozen["digest"],
                    current_read=True,
                )
        finally:
            old.rollback()
            with engine.begin() as latest:
                latest.execute(
                    update(cases)
                    .where(cases.c.id == case_id)
                    .values(deletion_requested_at=None)
                )
