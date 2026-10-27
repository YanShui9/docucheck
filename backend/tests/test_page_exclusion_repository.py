# ruff: noqa: F811 -- Shared fixtures.
"""Explicit page decisions remain immutable across saves, Run replay and exports."""

from copy import deepcopy

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import insert, select, update

from backend.app.api.main import create_app
from backend.app.db.checklist_repository import ChecklistRepository
from backend.app.db.item_binding_repository import (
    ItemBindingRepository,
    freeze_current,
    load_frozen,
)
from backend.app.db.models import (
    document_versions,
    evidence_blocks,
    item_binding_versions,
    suppliers,
)
from backend.tests.test_checklists import ACTOR
from backend.tests.test_index_repository import NOW, indexed_db  # noqa: F401
from backend.tests.test_item_binding_api import URL, put
from backend.tests.test_page_exclusions import sample


@pytest.fixture
def page_binding_db(indexed_db):
    engine, _ = indexed_db
    definition, repo, entries, exclusions = sample()
    doc = repo.documents[0]
    with engine.begin() as conn:
        conn.execute(insert(suppliers).values(id="sup", case_id="case", handle="A", name="供应商", created_at=NOW))
        conn.execute(update(document_versions).values(
            id=doc.document_id, role="spec", supplier_id="sup", model_name=doc.model,
            original_name="供应商规格.pdf", coverage_json={
                "parser_version_full": "parser", "total_pages": 2, "processed_pages": [0, 1], "coverage_issues": []}))
        for block in repo.blocks:
            conn.execute(insert(evidence_blocks).values(
                id=block.evidence_id, document_version_id=doc.document_id, ordinal=block.ordinal,
                source_text=block.text, page_idx=block.page_idx, created_at=NOW))
    checklists = ChecklistRepository(engine)
    checklists.put("case", "snap", definition.model_dump(mode="json"), actor=ACTOR,
                   expected_revision=1, expected_checklist_revision=0, idempotency_key="draft", reason="采购型号")
    confirmed = checklists.confirm("case", "snap", ["capacity"], actor=ACTOR,
                                   expected_revision=1, expected_checklist_revision=1, confirmed=True,
                                   idempotency_key="confirm", reason="核对型号")
    body = {"entries": entries, "exclusions": exclusions, "exclusions_confirmed": True,
            "checklist_version_id": confirmed["confirmed_version"]["id"], "expected_revision": 1,
            "expected_checklist_revision": 2, "expected_binding_revision": 0, "confirmed": True,
            "reason": "确认关联及其他型号排除页"}
    return engine, body


def test_explicit_decision_survives_sql_and_typed_api_readback(page_binding_db):
    engine, body = page_binding_db
    first = put(engine, body)
    version = first["current_version"]
    assert version["payload"]["schema_version"] == 2
    exclusion = version["payload"]["exclusions"][0]
    assert exclusion["supplier"] == "A" and len(exclusion["evidence_refs"]) == 4
    assert put(engine, body) == first
    definition = ChecklistRepository(engine).version("case", body["checklist_version_id"])["definition"]
    with engine.begin() as conn:
        frozen = freeze_current(conn, "case", "snap", body["checklist_version_id"], definition)
    assert frozen["payload"] == version["payload"]
    response = TestClient(create_app(engine)).get(URL)
    assert response.status_code == 200
    assert response.json()["current_version"]["payload"] == version["payload"]


@pytest.mark.parametrize("flag", [None, False, "true", 1])
def test_exclusion_requires_its_own_strict_confirmation(page_binding_db, flag):
    engine, body = page_binding_db
    body["exclusions_confirmed"] = flag
    with pytest.raises(ValueError, match="item_exclusion_confirmation_required"):
        put(engine, body)
    with engine.connect() as conn:
        assert conn.scalar(select(item_binding_versions.c.id)) is None


def test_legacy_client_cannot_silently_remove_existing_exclusions(page_binding_db):
    engine, body = page_binding_db
    put(engine, body)
    old_client = {k: v for k, v in body.items() if k not in {"exclusions", "exclusions_confirmed"}}
    old_client["expected_binding_revision"] = 1
    with pytest.raises(ValueError, match="item_exclusion_confirmation_required"):
        put(engine, old_client, key="legacy")
    no_confirmation = body | {"expected_binding_revision": 1, "exclusions": [], "exclusions_confirmed": None}
    with pytest.raises(ValueError, match="item_exclusion_confirmation_required"):
        put(engine, no_confirmation, key="missing-confirmation")


def test_cancelling_last_exclusion_appends_version_and_preserves_old_replay(page_binding_db):
    engine, body = page_binding_db
    first = put(engine, body)
    cancelled = put(engine, body | {"expected_binding_revision": 1, "exclusions": []}, key="cancel")
    assert cancelled["current_version"]["payload"]["schema_version"] == 1
    assert "exclusions" not in cancelled["current_version"]["payload"]
    assert first["current_version"]["id"] != cancelled["current_version"]["id"]
    assert put(engine, body) == first
    assert ItemBindingRepository(engine).version("case", first["current_version"]["id"]) == first["current_version"]
    with pytest.raises(ValueError, match="idempotency_conflict"):
        put(engine, body | {"exclusions": []})


def test_excluded_original_changes_invalidate_frozen_scope(page_binding_db):
    engine, body = page_binding_db
    first = put(engine, body)["current_version"]
    definition = ChecklistRepository(engine).version("case", body["checklist_version_id"])["definition"]
    with engine.begin() as conn:
        conn.execute(update(evidence_blocks).where(evidence_blocks.c.id == first["payload"]["exclusions"][0]["evidence_refs"][-1])
                     .values(source_text="来源已变化"))
        with pytest.raises(ValueError, match="item_binding_source_changed"):
            load_frozen(conn, "case", "snap", body["checklist_version_id"], definition, first["id"], first["digest"])


def test_rule_edit_requires_reconfirmation_instead_of_carrying_exclusions(page_binding_db):
    engine, body = page_binding_db
    first = put(engine, body)["current_version"]
    service = ChecklistRepository(engine)
    definition = deepcopy(service.version("case", body["checklist_version_id"])["definition"])
    definition["criteria"][0]["rule"]["value"] = "256"
    service.put("case", "snap", definition, actor=ACTOR, expected_revision=1,
                expected_checklist_revision=2, idempotency_key="new-draft", reason="调整阈值")
    confirmed = service.confirm("case", "snap", ["capacity"], actor=ACTOR, expected_revision=1,
                                expected_checklist_revision=3, confirmed=True, idempotency_key="new-confirm", reason="确认")
    view = ItemBindingRepository(engine).get("case", "snap")
    assert view["state"] == "stale" and view["historical_version"]["id"] == first["id"]
    with engine.begin() as conn:
        frozen = freeze_current(conn, "case", "snap", confirmed["confirmed_version"]["id"],
                                confirmed["confirmed_version"]["definition"], allow_continuation=True)
    assert frozen["version_id"] is None and not frozen["payload"]["entries"]


@pytest.mark.parametrize("flag", ["true", 1])
def test_api_exclusion_confirmation_does_not_coerce_boolean(page_binding_db, flag):
    engine, body = page_binding_db
    client = TestClient(create_app(engine))
    response = client.put(URL, headers={"Idempotency-Key": "strict"}, json=body | {"exclusions_confirmed": flag})
    assert response.status_code == 422
    assert client.get(URL).json()["current_version"] is None


def test_api_full_save_roundtrip_and_active_run_guard(page_binding_db):
    from backend.app.db.run_repository import RunRepository
    from backend.tests.test_item_binding_runs import submit

    engine, body = page_binding_db
    client = TestClient(create_app(engine))
    saved = client.put(URL, headers={"Idempotency-Key": "save"}, json=body)
    assert saved.status_code == 200, saved.text
    assert saved.json()["current_version"]["payload"]["exclusions"]
    assert client.put(URL, headers={"Idempotency-Key": "save"}, json=body).json() == saved.json()
    run = submit(engine, "queued")
    response = client.put(URL, headers={"Idempotency-Key": "revoke"}, json=body | {
        "expected_binding_revision": 1, "exclusions": []})
    assert response.status_code == 409 and response.json()["error"]["code"] == "case_busy"
    RunRepository(engine).cancel(run.run_id)
