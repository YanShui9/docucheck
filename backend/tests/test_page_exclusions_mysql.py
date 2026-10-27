"""Opt-in page decisions share the real MySQL Case lock and frozen replay."""

import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy import insert

from backend.app.db.checklist_repository import ChecklistRepository
from backend.app.db.item_binding_repository import (
    ItemBindingError,
    ItemBindingRepository,
    freeze_current,
    load_frozen,
)
from backend.app.db.models import document_versions, evidence_blocks, suppliers
from backend.app.evidence.repository import EvidenceRepository
from backend.tests import test_checklists_mysql
from backend.tests.test_checklists import ACTOR
from backend.tests.test_page_exclusions import sample

mysql_checklist = test_checklists_mysql.mysql_checklist
pytestmark = pytest.mark.skipif(os.getenv("DOCUCHECK_RUN_MYSQL_TESTS") != "1", reason="explicit MySQL opt-in")


def test_mysql_scope_race_and_historical_replay(mysql_checklist):
    engine, case_id, snapshot_id, _ = mysql_checklist
    definition, original, entries, exclusions = sample()
    doc_id, supplier_id = uuid4().hex, uuid4().hex
    old = original.documents[0]
    parsed = replace(old.parsed, document_id=doc_id,
                     blocks=tuple(replace(b, document_id=doc_id) for b in old.parsed.blocks))
    repo = EvidenceRepository(case_id, 1, [replace(old, document_id=doc_id, parsed=parsed)])
    ref_map = {before.evidence_id: after.evidence_id for before, after in zip(original.blocks, repo.blocks, strict=True)}
    for raw in [*entries, *exclusions]:
        raw["document_id"] = doc_id
        for key in ("anchor", "sku_anchor"):
            if key in raw:
                raw[key]["evidence_refs"] = [ref_map[ref] for ref in raw[key]["evidence_refs"]]
    now = datetime.now(UTC).replace(tzinfo=None)
    with engine.begin() as connection:
        connection.execute(insert(suppliers).values(id=supplier_id, case_id=case_id, handle="A", name="合成供应商", created_at=now))
        connection.execute(insert(document_versions).values(
            id=doc_id, case_id=case_id, snapshot_id=snapshot_id, role="spec", supplier_id=supplier_id,
            model_name="DEMO-P512", original_name="范围验证.pdf", storage_key="uploads/aa/" + uuid4().hex + ".pdf",
            source_sha256="a" * 64, media_type="application/pdf", parser_version="parser", parse_status="complete",
            coverage_json={"parser_version_full": "parser", "total_pages": 2,
                           "processed_pages": [0, 1], "coverage_issues": []}, created_at=now))
        for block in repo.blocks:
            connection.execute(insert(evidence_blocks).values(
                id=block.evidence_id, document_version_id=doc_id, ordinal=block.ordinal,
                source_text=block.text, page_idx=block.page_idx, created_at=now))
    checklist = ChecklistRepository(engine)
    checklist.put(case_id, snapshot_id, definition.model_dump(mode="json"), actor=ACTOR,
                  expected_revision=1, expected_checklist_revision=0, idempotency_key="draft", reason="目标型号")
    confirmed = checklist.confirm(case_id, snapshot_id, ["capacity"], actor=ACTOR, expected_revision=1,
                                  expected_checklist_revision=1, confirmed=True, idempotency_key="confirm", reason="明确确认")
    body = {"entries": entries, "exclusions": exclusions, "exclusions_confirmed": True, "actor": ACTOR,
            "checklist_version_id": confirmed["confirmed_version"]["id"], "expected_revision": 1,
            "expected_checklist_revision": 2, "expected_binding_revision": 0, "confirmed": True, "reason": "其他型号整页"}
    service = ItemBindingRepository(engine)
    first = service.put(case_id, snapshot_id, idempotency_key="initial", **body)["current_version"]
    with engine.begin() as connection:
        frozen = freeze_current(connection, case_id, snapshot_id, body["checklist_version_id"], definition)
    barrier = Barrier(2)

    def revoke(key):
        barrier.wait(timeout=10)
        try:
            return service.put(case_id, snapshot_id, idempotency_key=key,
                               **(body | {"expected_binding_revision": 1, "exclusions": []}))["binding_revision"]
        except ItemBindingError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        result = list(pool.map(revoke, ["first", "second"]))
    assert sorted(map(str, result)) == ["2", "item_binding_revision_conflict"]
    engine.dispose()
    with engine.begin() as connection:
        assert load_frozen(connection, case_id, snapshot_id, body["checklist_version_id"], definition,
                           frozen["version_id"], frozen["digest"]) == frozen
    assert service.version(case_id, first["id"]) == first
    assert service.get(case_id, snapshot_id)["current_version"]["payload"]["schema_version"] == 1
