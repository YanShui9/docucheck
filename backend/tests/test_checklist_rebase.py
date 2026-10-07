# ruff: noqa: F811 -- Shared fixtures resolved by name.
from copy import deepcopy

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import insert, select, update

from backend.app.api.auth import Principal
from backend.app.api.main import create_app
from backend.app.db.checklist_repository import ChecklistError
from backend.app.db.models import (
    cases,
    checklist_heads,
    checklist_operations,
    document_versions,
    evidence_blocks,
    input_snapshots,
)
from backend.app.db.workspace import WorkspaceRepository
from backend.tests.test_api_workspace_authorization import DemoAuthenticator
from backend.tests.test_checklists import confirm, definition, put, service
from backend.tests.test_index_repository import NOW, indexed_db  # noqa: F401


def versioned_requirement(engine, *, joint=False):
    with engine.begin() as conn:
        conn.execute(insert(evidence_blocks).values(
            id="old-a", document_version_id="doc", ordinal=0,
            source_text="大米 采购数量 至少" if joint else "大米 采购数量 至少 20 袋",
            sheet="需求", cell="A1", created_at=NOW,
        ))
        if joint:
            conn.execute(insert(evidence_blocks).values(
                id="old-b", document_version_id="doc", ordinal=1,
                source_text="20 袋", sheet="需求", cell="B1", created_at=NOW,
            ))
    body = definition(1)
    body["criteria"][0]["name"] = "采购数量"
    body["criteria"][0]["rule"]["value"] = "20"
    body["criteria"][0]["source"] = {
        "kind": "document_extracted",
        "context": {"evidence_refs": ["old-a", "old-b"] if joint else ["old-a"],
                    "quote": "大米 采购数量 至少 20 袋"},
    }
    put(engine, body)
    version = confirm(engine, ["rice-0"])["confirmed_version"]
    with engine.begin() as conn:
        conn.execute(update(cases).values(revision=2))
        conn.execute(insert(input_snapshots).values(
            id="snap2", case_id="case", revision=2, created_at=NOW))
        doc = dict(conn.execute(select(document_versions).where(
            document_versions.c.id == "doc")).mappings().one())
        conn.execute(insert(document_versions).values({**doc, "id": "doc2", "snapshot_id": "snap2"}))
        rows = conn.execute(select(evidence_blocks).where(
            evidence_blocks.c.document_version_id == "doc")).mappings().all()
        for row in rows:
            conn.execute(insert(evidence_blocks).values({**row,
                "id": row["id"].replace("old-", "new-"), "document_version_id": "doc2"}))
    return version


@pytest.mark.parametrize("joint", [False, True])
def test_rebase_maps_unchanged_sources_without_saving_and_requires_explicit_confirmation(indexed_db, joint):
    engine, _ = indexed_db
    version = versioned_requirement(engine, joint=joint)
    with engine.connect() as conn:
        head = dict(conn.execute(select(checklist_heads)).mappings().one())
        operations = len(conn.execute(select(checklist_operations)).all())
    preview = service(engine).preview_rebase("case", "snap2", version["id"], expected_revision=2)
    refs = preview["definition"]["criteria"][0]["source"]["context"]["evidence_refs"]
    assert refs == (["new-a", "new-b"] if joint else ["new-a"])
    assert preview["snapshot_id"] == "snap2" and preview["revision"] == 2
    with engine.connect() as conn:
        assert dict(conn.execute(select(checklist_heads)).mappings().one()) == head
        assert len(conn.execute(select(checklist_operations)).all()) == operations
    saved = put(engine, preview["definition"], snap="snap2", input_revision=2,
                revision=2, key="rebase-save")
    assert saved["execution_available"] is False
    result = service(engine).confirm("case", "snap2", ["rice-0"], actor="a" * 64,
        expected_revision=2, expected_checklist_revision=3, confirmed=True,
        idempotency_key="rebase-confirm", reason="已核对新版原文")
    assert result["execution_available"] is True
    assert service(engine).version("case", version["id"])["definition"] == version["definition"]


@pytest.mark.parametrize("change", ["hash", "text", "parser", "location", "pending", "duplicate", "supplier"])
def test_rebase_refuses_changed_missing_ambiguous_or_supplier_evidence(indexed_db, change):
    engine, _ = indexed_db
    version = versioned_requirement(engine)
    with engine.begin() as conn:
        if change in {"hash", "parser", "pending", "supplier"}:
            values = {"hash": {"source_sha256": "b" * 64},
                      "parser": {"parser_version": "another-parser"},
                      "pending": {"parse_status": "pending"},
                      "supplier": {"role": "quote", "supplier_id": "supplier"}}[change]
            if change == "supplier":
                from backend.app.db.models import suppliers
                conn.execute(insert(suppliers).values(id="supplier", case_id="case",
                    handle="supplier", name="供应商", created_at=NOW))
            conn.execute(update(document_versions).where(document_versions.c.id == "doc2").values(**values))
        elif change == "duplicate":
            doc = dict(conn.execute(select(document_versions).where(document_versions.c.id == "doc2")).mappings().one())
            conn.execute(insert(document_versions).values({**doc, "id": "doc3"}))
            row = dict(conn.execute(select(evidence_blocks).where(evidence_blocks.c.id == "new-a")).mappings().one())
            conn.execute(insert(evidence_blocks).values({**row, "id": "duplicate-a", "document_version_id": "doc3"}))
        else:
            conn.execute(update(evidence_blocks).where(evidence_blocks.c.id == "new-a").values(
                **({"source_text": "采购数量 200 袋"} if change == "text" else {"cell": "A2"})))
    with pytest.raises(ChecklistError, match="checklist_rebase_unresolved"):
        service(engine).preview_rebase("case", "snap2", version["id"], expected_revision=2)


def test_rebase_enforces_current_snapshot_and_case_ownership(indexed_db):
    engine, _ = indexed_db
    version = versioned_requirement(engine)
    with pytest.raises(ChecklistError, match="checklist_snapshot_changed"):
        service(engine).preview_rebase("case", "snap", version["id"], expected_revision=1)
    with pytest.raises(ChecklistError, match="not_found"):
        service(engine).preview_rebase("case", "snap2", "unknown-version", expected_revision=2)
    with engine.begin() as conn:
        conn.execute(insert(cases).values(id="other", revision=1, created_at=NOW, updated_at=NOW))
        conn.execute(insert(input_snapshots).values(id="other-snap", case_id="other", revision=1, created_at=NOW))
    with pytest.raises(ChecklistError, match="not_found"):
        service(engine).preview_rebase("other", "other-snap", version["id"], expected_revision=1)


def test_rebase_api_preserves_project_authorization(indexed_db):
    engine, _ = indexed_db
    version = versioned_requirement(engine)
    alice = Principal("http://localhost:8080/realms/demo", "alice")
    project = WorkspaceRepository(engine).create_project(alice, "采购")
    with engine.begin() as conn:
        conn.execute(update(cases).values(project_id=project["id"]))
    client = TestClient(create_app(engine, authenticator=DemoAuthenticator()))
    url = f'/api/v1/cases/case/snapshots/snap2/checklist/rebase-preview?version_id={version["id"]}&expected_revision=2'
    assert client.get(url).status_code == 401
    assert client.get(url, headers={"Authorization": "Bearer bob"}).status_code == 404
    result = client.get(url, headers={"Authorization": "Bearer alice"})
    assert result.status_code == 200, result.text
    assert result.json()["definition"]["criteria"][0]["source"]["context"]["evidence_refs"] == ["new-a"]


def test_rebase_user_requirements_stay_user_requirements_without_inventing_evidence(indexed_db):
    engine, _ = indexed_db
    body = definition(1)
    put(engine, body)
    version = confirm(engine, ["rice-0"])["confirmed_version"]
    with engine.begin() as conn:
        conn.execute(update(cases).values(revision=2))
        conn.execute(insert(input_snapshots).values(id="snap2", case_id="case", revision=2, created_at=NOW))
    preview = service(engine).preview_rebase("case", "snap2", version["id"], expected_revision=2)
    assert preview["definition"] == version["definition"]
    assert preview["definition"]["criteria"][0]["source"]["context"] is None


def test_rebase_preview_does_not_allow_stale_or_changed_sources_to_be_saved(indexed_db):
    engine, _ = indexed_db
    version = versioned_requirement(engine)
    preview = service(engine).preview_rebase("case", "snap2", version["id"], expected_revision=2)
    with engine.begin() as conn:
        conn.execute(update(evidence_blocks).where(evidence_blocks.c.id == "new-a").values(source_text="新的要求 200 袋"))
    with pytest.raises(ChecklistError, match="checklist_source_scope"):
        put(engine, preview["definition"], snap="snap2", input_revision=2, revision=2, key="changed-source-save")
    with engine.begin() as conn:
        conn.execute(update(cases).values(revision=3))
    with pytest.raises(ChecklistError, match="checklist_snapshot_changed"):
        put(engine, preview["definition"], snap="snap2", input_revision=2, revision=2, key="stale-preview-save")


def test_saving_full_visible_definition_retains_previously_confirmed_metrics(indexed_db):
    engine, _ = indexed_db
    put(engine, definition(2))
    partial = confirm(engine, ["rice-0"])
    full = deepcopy(partial["draft"])
    full["criteria"] = partial["confirmed_version"]["definition"]["criteria"] + full["criteria"]
    full["criteria"][1]["rule"]["value"] = "200"
    put(engine, full, revision=2, key="save-all-visible")
    result = confirm(engine, ["rice-0", "rice-1"], revision=3, key="confirm-all-visible")
    assert [c["criterion_id"] for c in result["confirmed_version"]["definition"]["criteria"]] == ["rice-0", "rice-1"]
    assert result["confirmed_version"]["definition"]["criteria"][1]["rule"]["value"] == "200"
