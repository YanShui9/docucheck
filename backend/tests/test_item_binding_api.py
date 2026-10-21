# ruff: noqa: F811 -- Shared pytest fixtures.
"""Immutable receipts, current scopes, frozen identities and authorization."""

from copy import deepcopy

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import insert, select, update

from backend.app.api.auth import Principal
from backend.app.api.main import create_app
from backend.app.db.checklist_repository import ChecklistRepository
from backend.app.db.models import cases, document_versions, evidence_blocks, suppliers
from backend.app.db.workspace import WorkspaceRepository
from backend.tests.test_api_workspace_authorization import DemoAuthenticator
from backend.tests.test_checklists import ACTOR
from backend.tests.test_index_repository import NOW, indexed_db  # noqa: F401
from backend.tests.test_item_bindings import entry, sample

URL = "/api/v1/cases/case/snapshots/snap/item-bindings"


@pytest.fixture
def binding_db(indexed_db):
    engine, path = indexed_db
    spec, repo = sample()
    with engine.begin() as conn:
        conn.execute(
            insert(suppliers).values(
                id="sup", case_id="case", handle="A", name="供应商", created_at=NOW
            )
        )
        conn.execute(
            update(document_versions).values(
                role="quote",
                supplier_id="sup",
                model_name="苹果手机",
                original_name="报价.xlsx",
                media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                coverage_json={
                    "parser_version_full": "parser",
                    "total_sheets": 1,
                    "processed_sheets": ["报价"],
                },
            )
        )
        for b in repo.blocks:
            conn.execute(
                insert(evidence_blocks).values(
                    id=b.evidence_id,
                    document_version_id="doc",
                    ordinal=b.ordinal,
                    source_text=b.text,
                    sheet=b.sheet,
                    cell=b.cell,
                    created_at=NOW,
                )
            )
    checklist = ChecklistRepository(engine)
    checklist.put(
        "case",
        "snap",
        spec.model_dump(mode="json"),
        actor=ACTOR,
        expected_revision=1,
        expected_checklist_revision=0,
        idempotency_key="draft",
        reason="明确要求",
    )
    confirmed = checklist.confirm(
        "case",
        "snap",
        ["rice-0"],
        actor=ACTOR,
        expected_revision=1,
        expected_checklist_revision=1,
        confirmed=True,
        idempotency_key="confirmed",
        reason="核对",
    )
    body = {
        "entries": [entry(repo)],
        "checklist_version_id": confirmed["confirmed_version"]["id"],
        "expected_revision": 1,
        "expected_checklist_revision": 2,
        "expected_binding_revision": 0,
        "confirmed": True,
        "reason": "核对资料对应",
    }
    return engine, path, body


def put(engine, body, key="binding", actor=ACTOR):
    from backend.app.db.item_binding_repository import ItemBindingRepository

    body = deepcopy(body)
    entries, reason = body.pop("entries"), body.pop("reason")
    return ItemBindingRepository(engine).put(
        "case", "snap", entries, actor=actor, idempotency_key=key, reason=reason, **body
    )


def test_versions_receipts_and_frozen_identity(binding_db):
    from backend.app.db.item_binding_repository import (
        ItemBindingRepository,
        freeze_current,
        load_frozen,
    )

    engine, _, body = binding_db
    first = put(engine, body)
    assert first["binding_revision"] == 1 and put(engine, body) == first
    checklist = ChecklistRepository(engine).version(
        "case", body["checklist_version_id"]
    )["definition"]
    with engine.begin() as conn:
        frozen = freeze_current(
            conn, "case", "snap", body["checklist_version_id"], checklist
        )
        assert frozen["version_id"] == first["current_version"]["id"]
        assert (
            load_frozen(
                conn,
                "case",
                "snap",
                body["checklist_version_id"],
                checklist,
                frozen["version_id"],
                frozen["digest"],
            )
            == frozen
        )
    with pytest.raises(ValueError, match="idempotency_conflict"):
        put(engine, body | {"reason": "different"})
    put(engine, body | {"entries": [], "expected_binding_revision": 1}, key="clear")
    assert (
        ItemBindingRepository(engine).version("case", frozen["version_id"])["payload"]
        == frozen["payload"]
    )
    with engine.begin() as conn:
        assert (
            load_frozen(
                conn,
                "case",
                "snap",
                body["checklist_version_id"],
                checklist,
                frozen["version_id"],
                frozen["digest"],
            )
            == frozen
        )
        empty = freeze_current(conn, "case", "snap", None, None)
        assert empty["version_id"] is None and empty["digest"] != frozen["digest"]
    with engine.begin() as conn:
        conn.execute(
            update(evidence_blocks)
            .where(evidence_blocks.c.cell == "A2")
            .values(source_text="数量100台")
        )
    with (
        engine.begin() as conn,
        pytest.raises(ValueError, match="item_binding_source_changed"),
    ):
        load_frozen(
            conn,
            "case",
            "snap",
            body["checklist_version_id"],
            checklist,
            frozen["version_id"],
            frozen["digest"],
        )


def test_api_roles_strict_confirmation_and_case_deletion(binding_db):
    engine, _, body = binding_db
    principal = Principal("http://localhost:8080/realms/demo", "alice")
    project = WorkspaceRepository(engine).create_project(principal, "采购")
    with engine.begin() as conn:
        conn.execute(update(cases).values(project_id=project["id"]))
    client = TestClient(create_app(engine, authenticator=DemoAuthenticator()))
    headers = {"Authorization": "Bearer alice", "Idempotency-Key": "binding"}
    assert client.get(URL).status_code == 401
    openapi = client.get("/openapi.json").json()
    response_schema = openapi["paths"][
        "/api/v1/cases/{case_id}/snapshots/{snapshot_id}/item-bindings"
    ]["get"]["responses"]["200"]["content"]["application/json"]["schema"]
    assert response_schema.get("$ref", "").endswith("/ItemBindingViewOut")
    assert (
        client.put(
            URL, headers=headers | {"Authorization": "Bearer bob"}, json=body
        ).status_code
        == 404
    )
    for bad in (False, "true", 1):
        assert (
            client.put(URL, headers=headers, json=body | {"confirmed": bad}).status_code
            == 422
        )
    result = client.put(URL, headers=headers, json=body)
    assert result.status_code == 200, result.text
    version = result.json()["current_version"]["id"]
    assert (
        client.post(
            f"/api/v1/projects/{project['id']}/members",
            headers=headers,
            json={"subject": "bob", "role": "viewer"},
        ).status_code
        == 201
    )
    viewer = headers | {"Authorization": "Bearer bob"}
    assert client.get(URL, headers=viewer).status_code == 200
    assert client.put(URL, headers=viewer, json=body).status_code == 404
    assert (
        client.get(
            f"/api/v1/cases/case/item-binding-versions/{version}", headers=headers
        ).status_code
        == 200
    )
    with engine.begin() as conn:
        conn.execute(update(cases).values(deletion_requested_at=NOW))
    assert client.get(URL, headers=headers).status_code in {404, 409, 410}


def test_preconditions_empty_freezing_and_cleanup(binding_db, tmp_path):
    from backend.app.db.case_cleanup import CaseCleanup
    from backend.app.db.case_deletion import CaseDeletion
    from backend.app.db.item_binding_repository import freeze_current
    from backend.app.db.models import (
        item_binding_heads,
        item_binding_operations,
        item_binding_versions,
    )

    engine, _, body = binding_db
    for changes in (
        {"expected_binding_revision": 1},
        {"expected_checklist_revision": 1},
        {"checklist_version_id": "missing"},
        {"expected_revision": 2},
    ):
        with pytest.raises(ValueError):
            put(engine, body | changes)
    with engine.begin() as conn:
        no_version = freeze_current(
            conn,
            "case",
            "snap",
            body["checklist_version_id"],
            ChecklistRepository(engine).version("case", body["checklist_version_id"])[
                "definition"
            ],
        )
        assert (
            no_version["version_id"] is None and no_version["payload"]["entries"] == []
        )
    put(engine, body)
    with engine.begin() as conn:
        conn.execute(
            update(document_versions).values(
                storage_key="uploads/aa/" + "a" * 32 + ".xlsx"
            )
        )
    CaseDeletion(engine).request("case", 1)
    cleaner = CaseCleanup(engine, tmp_path)
    assert (
        cleaner.process(cleaner.claim(case_id="case", now=NOW), now=NOW)["status"]
        == "deleted"
    )
    with engine.connect() as conn:
        for table in (
            item_binding_heads,
            item_binding_versions,
            item_binding_operations,
        ):
            assert conn.execute(select(table)).first() is None


def test_binding_change_is_blocked_while_run_is_queued(binding_db):
    from backend.app.db.models import conversations
    from backend.app.db.run_repository import RunRepository, SubmitRun

    engine, _, body = binding_db
    with engine.begin() as connection:
        connection.execute(
            insert(conversations).values(id="conv", case_id="case", created_at=NOW)
        )
    runs = RunRepository(engine)
    receipt = runs.submit(
        SubmitRun("case", "conv", "snap", 1, "审查", "standard", "queued")
    )
    with pytest.raises(ValueError, match="case_busy"):
        put(engine, body)
    runs.cancel(receipt.run_id)
    assert put(engine, body)["binding_revision"] == 1


def test_changed_item_identity_marks_binding_stale_and_freezes_empty(binding_db):
    from backend.app.db.item_binding_repository import (
        ItemBindingRepository,
        freeze_current,
    )

    engine, _, body = binding_db
    put(engine, body)
    service = ChecklistRepository(engine)
    old = service.version("case", body["checklist_version_id"])["definition"]
    changed = deepcopy(old)
    changed["criteria"][0]["rule"]["value"] = "50"
    changed["items"][0]["specification"] = "型号：NEW-SKU"
    service.put(
        "case",
        "snap",
        changed,
        actor=ACTOR,
        expected_revision=1,
        expected_checklist_revision=2,
        idempotency_key="new-draft",
        reason="新要求",
    )
    saved = service.confirm(
        "case",
        "snap",
        ["rice-0"],
        actor=ACTOR,
        expected_revision=1,
        expected_checklist_revision=3,
        confirmed=True,
        idempotency_key="new-confirm",
        reason="确认新要求",
    )
    view = ItemBindingRepository(engine).get("case", "snap")
    assert view["state"] == "stale" and view["current_version"] is None
    with engine.begin() as connection:
        frozen = freeze_current(
            connection, "case", "snap", saved["confirmed_version"]["id"], changed
        )
    assert frozen["version_id"] is None and frozen["payload"]["entries"] == []


def test_empty_binding_cannot_freeze_a_nonexistent_snapshot(binding_db):
    from backend.app.db.item_binding_repository import freeze_current

    engine, _, _ = binding_db
    with engine.begin() as connection, pytest.raises(ValueError, match="not_found"):
        freeze_current(connection, "case", "invented", None, None)


def test_empty_context_must_match_persisted_checklist_and_snapshot(binding_db):
    from backend.app.db.item_binding_repository import (
        _empty,
        freeze_current,
        load_frozen,
    )
    from backend.app.evidence.repository import digest

    engine, _, body = binding_db
    definition = ChecklistRepository(engine).version(
        "case", body["checklist_version_id"]
    )["definition"]
    changed = deepcopy(definition)
    changed["criteria"][0]["rule"]["value"] = "99"
    for snapshot, checklist_id, spec in (
        ("invented", None, None),
        ("snap", "invented", definition),
        ("snap", body["checklist_version_id"], changed),
        ("snap", body["checklist_version_id"], None),
        ("snap", None, definition),
    ):
        with engine.begin() as conn, pytest.raises(ValueError):
            freeze_current(conn, "case", snapshot, checklist_id, spec)
        with (
            engine.begin() as conn,
            pytest.raises(ValueError, match="item_binding_source_changed"),
        ):
            load_frozen(
                conn,
                "case",
                snapshot,
                checklist_id,
                spec,
                None,
                digest(_empty("case", snapshot, checklist_id, spec)),
            )
    with engine.begin() as conn:
        empty = freeze_current(conn, "case", "snap", None, None)
        assert (
            load_frozen(conn, "case", "snap", None, None, None, empty["digest"])
            == empty
        )


def test_repository_preserves_capacity_error(binding_db):
    engine, _, body = binding_db
    with pytest.raises(ValueError, match="item_binding_capacity"):
        put(engine, body | {"entries": body["entries"] * 141})
