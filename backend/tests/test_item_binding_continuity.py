# ruff: noqa: F811 -- Imported pytest fixtures are resolved by name.
"""Rule edits retain only the exact, independently confirmed document scope."""

from copy import deepcopy
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, insert, select, update

from backend.app.api.item_bindings import ItemBindingVersionOut
from backend.app.api.main import create_app
from backend.app.db.checklist_repository import ChecklistRepository
from backend.app.db.item_binding_repository import (
    ItemBindingRepository,
    freeze_current,
    load_frozen,
    run_bindings_current,
)
from backend.app.db.models import (
    assistant_runs,
    cases,
    checklist_heads,
    checklist_versions,
    document_versions,
    dynamic_runs,
    evidence_blocks,
    item_binding_heads,
    item_binding_versions,
    suppliers,
)
from backend.app.evidence.repository import digest
from backend.tests.test_checklists import ACTOR
from backend.tests.test_index_repository import NOW, indexed_db  # noqa: F401
from backend.tests.test_item_binding_api import binding_db, put  # noqa: F401
from backend.tests.test_item_binding_runs import submit


def reconfirm(engine, definition, *, revision=2, key="new"):
    service = ChecklistRepository(engine)
    service.put(
        "case", "snap", definition, actor=ACTOR, expected_revision=1,
        expected_checklist_revision=revision, idempotency_key=f"{key}-draft", reason="修改审查要求",
    )
    return service.confirm(
        "case", "snap", [c["criterion_id"] for c in definition["criteria"]], actor=ACTOR,
        expected_revision=1, expected_checklist_revision=revision + 1, confirmed=True,
        idempotency_key=f"{key}-confirm", reason="确认审查要求",
    )


def changed_definition(engine, body):
    result = deepcopy(ChecklistRepository(engine).version("case", body["checklist_version_id"])["definition"])
    result["criteria"][0]["rule"]["value"] = "50"
    return result


def restore_legacy_stale_head(engine, original):
    """Recreate the persisted state produced before continuity existed."""
    with engine.begin() as conn:
        conn.execute(update(item_binding_heads).values(version_id=original["id"], revision=original["version"]))
        conn.execute(delete(item_binding_versions).where(item_binding_versions.c.version > original["version"]))


def count_versions(engine):
    with engine.connect() as conn:
        return conn.scalar(select(func.count()).select_from(item_binding_versions))


def test_confirm_rule_edit_creates_immutable_binding_with_original_confirmation(binding_db):
    engine, _, body = binding_db
    original_view = put(engine, body)
    original = original_view["current_version"]
    changed = changed_definition(engine, body)
    confirmation = reconfirm(engine, changed)
    view = ItemBindingRepository(engine).get("case", "snap")
    assert view["state"] == "confirmed"
    current = view["current_version"]
    assert current["id"] != original["id"] and view["binding_revision"] == 2
    assert current["checklist_version_id"] == confirmation["confirmed_version"]["id"]
    assert current["payload"]["entries"] == original["payload"]["entries"]
    assert current["payload"]["continuation"] == {"version_id": original["id"], "digest": original["digest"]}
    assert current["confirmed_by"] == original["confirmed_by"]
    assert current["reason"] == original["reason"]
    assert current["confirmed_at"] == original["confirmed_at"]
    ItemBindingVersionOut.model_validate(current)
    assert ItemBindingRepository(engine).version("case", original["id"]) == original
    assert put(engine, body) == original_view  # Original immutable receipt.
    assert reconfirm(engine, changed) == confirmation
    assert count_versions(engine) == 2
    with engine.begin() as conn:
        frozen = freeze_current(conn, "case", "snap", current["checklist_version_id"], changed)
        assert load_frozen(conn, "case", "snap", current["checklist_version_id"], changed,
                           current["id"], current["digest"]) == frozen


@pytest.mark.parametrize("workflow,table", [("auto", dynamic_runs), ("assistant", assistant_runs)])
def test_legacy_stale_binding_recovers_only_during_explicit_new_submission(binding_db, workflow, table):
    engine, _, body = binding_db
    original = put(engine, body)["current_version"]
    changed = changed_definition(engine, body)
    confirmation = reconfirm(engine, changed)
    restore_legacy_stale_head(engine, original)
    store = ItemBindingRepository(engine)
    assert store.get("case", "snap")["state"] == "stale"
    checklist_id = confirmation["confirmed_version"]["id"]
    with engine.begin() as conn:
        frozen = freeze_current(conn, "case", "snap", checklist_id, changed)
        assert frozen["version_id"] is None
        assert run_bindings_current(conn, {"case_id": "case", "snapshot_id": "snap", "checklist_version_id": checklist_id,
            "item_binding_version_id": None, "item_binding_digest": frozen["digest"]}, changed)
    assert count_versions(engine) == 1
    receipt = submit(engine, "recover", workflow)
    current = store.get("case", "snap")["current_version"]
    assert current is not None and current["payload"]["entries"] == original["payload"]["entries"]
    with engine.begin() as conn:
        row = conn.execute(select(table).where(table.c.run_id == receipt.run_id)).mappings().one()
        assert row["item_binding_version_id"] == current["id"]
        assert load_frozen(conn, "case", "snap", checklist_id, changed,
                           row["item_binding_version_id"], row["item_binding_digest"])["payload"] == current["payload"]
    assert count_versions(engine) == 2


@pytest.mark.parametrize("identity", ["name", "specification", "item_id", "new_item"])
def test_item_identity_changes_never_inherit_original_scope(binding_db, identity):
    engine, _, body = binding_db
    original = put(engine, body)["current_version"]
    changed = changed_definition(engine, body)
    if identity == "new_item":
        changed["items"].append({"item_id": "new", "name": "T恤", "specification": ""})
        criterion = deepcopy(changed["criteria"][0])
        criterion.update(criterion_id="new-qty", item_id="new")
        changed["criteria"].append(criterion)
    elif identity == "item_id":
        changed["items"][0]["item_id"] = "new-id"
        changed["criteria"][0]["item_id"] = "new-id"
    else:
        changed["items"][0][identity] += "修改"
    confirmation = reconfirm(engine, changed)
    assert ItemBindingRepository(engine).get("case", "snap")["state"] == "stale"
    with engine.begin() as conn:
        frozen = freeze_current(conn, "case", "snap", confirmation["confirmed_version"]["id"], changed,
                                allow_continuation=True)
    assert frozen["version_id"] is None and count_versions(engine) == 1
    assert ItemBindingRepository(engine).version("case", original["id"]) == original


def test_repeated_rule_edits_reference_one_original_version_without_chain(binding_db):
    engine, _, body = binding_db
    original = put(engine, body)["current_version"]
    changed = changed_definition(engine, body)
    for n in range(4):
        changed["criteria"][0]["rule"]["value"] = str(50 + n)
        reconfirm(engine, changed, revision=2 + 2 * n, key=f"edit-{n}")
        current = ItemBindingRepository(engine).get("case", "snap")["current_version"]
        assert current["payload"]["continuation"] == {"version_id": original["id"], "digest": original["digest"]}
    assert count_versions(engine) == 5


@pytest.mark.parametrize("source", ["scope_text", "anchor_text", "hash", "supplier", "parser", "role"])
def test_changed_original_source_is_never_carried_to_new_checklist(binding_db, source):
    engine, _, body = binding_db
    put(engine, body)
    changed = changed_definition(engine, body)
    with engine.begin() as conn:
        if source in {"scope_text", "anchor_text"}:
            cell = "A2" if source == "scope_text" else "A1"
            conn.execute(update(evidence_blocks).where(evidence_blocks.c.cell == cell).values(source_text="原文已改变"))
        elif source == "supplier":
            conn.execute(insert(suppliers).values(
                id="other-supplier", case_id="case", handle="B", name="另一供应商", created_at=NOW,
            ))
            conn.execute(update(document_versions).values(supplier_id="other-supplier"))
        else:
            changes = {"hash": {"source_sha256": "b" * 64},
                       "parser": {"coverage_json": {"parser_version_full": "changed"}}, "role": {"role": "spec"}}
            conn.execute(update(document_versions).values(**changes[source]))
    with pytest.raises(ValueError, match="item_binding_source_changed"):
        reconfirm(engine, changed)
    assert count_versions(engine) == 1


def test_changing_item_identity_then_restoring_it_still_requires_new_confirmation(binding_db):
    engine, _, body = binding_db
    put(engine, body)
    restored = changed_definition(engine, body)
    renamed = deepcopy(restored)
    renamed["items"][0]["name"] = "另一种手机"
    reconfirm(engine, renamed, key="rename")
    result = reconfirm(engine, restored, revision=4, key="restore-name")
    assert ItemBindingRepository(engine).get("case", "snap")["state"] == "stale"
    with engine.begin() as conn:
        assert freeze_current(conn, "case", "snap", result["confirmed_version"]["id"], restored,
                              allow_continuation=True)["version_id"] is None
    assert count_versions(engine) == 1


@pytest.mark.parametrize("tamper", ["chain", "entries", "origin_digest"])
def test_continuation_provenance_is_revalidated_without_following_chains(binding_db, tamper):
    engine, _, body = binding_db
    original = put(engine, body)["current_version"]
    changed = changed_definition(engine, body)
    reconfirm(engine, changed)
    middle = ItemBindingRepository(engine).get("case", "snap")["current_version"]
    changed["criteria"][0]["rule"]["value"] = "60"
    confirmation = reconfirm(engine, changed, revision=4, key="second")
    current = ItemBindingRepository(engine).get("case", "snap")["current_version"]
    payload = deepcopy(current["payload"])
    if tamper == "chain":
        payload["continuation"] = {"version_id": middle["id"], "digest": middle["digest"]}
    elif tamper == "entries":
        payload["entries"] = []
    else:
        payload["continuation"]["digest"] = "b" * 64
    with engine.begin() as conn:
        conn.execute(update(item_binding_versions).where(item_binding_versions.c.id == current["id"]).values(
            payload_json=payload, content_digest=digest(payload)))
    with engine.begin() as conn, pytest.raises(ValueError, match="item_binding_source_changed"):
        load_frozen(conn, "case", "snap", confirmation["confirmed_version"]["id"], changed,
                    current["id"], digest(payload))
    assert ItemBindingRepository(engine).version("case", original["id"]) == original


def test_api_preserves_payload_hash_for_old_and_continued_versions(binding_db):
    engine, _, body = binding_db
    original = put(engine, body)["current_version"]
    client = TestClient(create_app(engine))
    original_url = f"/api/v1/cases/case/item-binding-versions/{original['id']}"
    old_api = client.get(original_url).json()
    assert "continuation" not in old_api["payload"]
    assert digest(old_api["payload"]) == original["digest"]
    reconfirm(engine, changed_definition(engine, body))
    response = client.get("/api/v1/cases/case/snapshots/snap/item-bindings")
    assert response.status_code == 200, response.text
    current = response.json()["current_version"]
    assert current["payload"]["continuation"]["version_id"] == original["id"]
    assert digest(current["payload"]) == current["digest"]
    assert client.get(original_url).json() == old_api


def test_explicitly_cleared_binding_never_resurrects_older_entries(binding_db):
    engine, _, body = binding_db
    put(engine, body)
    cleared = put(engine, body | {"entries": [], "expected_binding_revision": 1}, key="clear")["current_version"]
    reconfirm(engine, changed_definition(engine, body))
    current = ItemBindingRepository(engine).get("case", "snap")["current_version"]
    assert current["payload"]["entries"] == []
    assert current["payload"]["continuation"]["version_id"] == cleared["id"]


def test_recovery_rolls_back_with_owning_write_transaction(binding_db):
    engine, _, body = binding_db
    original = put(engine, body)["current_version"]
    changed = changed_definition(engine, body)
    confirmed = reconfirm(engine, changed)
    restore_legacy_stale_head(engine, original)
    with pytest.raises(RuntimeError, match="later write failed"), engine.begin() as conn:
        assert freeze_current(conn, "case", "snap", confirmed["confirmed_version"]["id"], changed,
                              allow_continuation=True)["version_id"] is not None
        raise RuntimeError("later write failed")
    assert count_versions(engine) == 1
    assert ItemBindingRepository(engine).get("case", "snap")["state"] == "stale"


def test_legacy_recovery_obeys_case_busy_and_read_only_guards(binding_db):
    engine, _, body = binding_db
    original = put(engine, body)["current_version"]
    changed = changed_definition(engine, body)
    confirmed = reconfirm(engine, changed)
    restore_legacy_stale_head(engine, original)
    submit(engine, "proposal", "proposal")  # Has no confirmed-checklist authority.
    with engine.begin() as conn, pytest.raises(ValueError, match="case_busy"):
        freeze_current(conn, "case", "snap", confirmed["confirmed_version"]["id"], changed,
                       allow_continuation=True)
    with engine.begin() as conn, pytest.raises(ValueError, match="invalid_request"):
        freeze_current(conn, "case", "snap", confirmed["confirmed_version"]["id"], changed,
                       current_read=False, allow_continuation=True)
    assert count_versions(engine) == 1


@pytest.mark.parametrize("gap,continued", [(100, True), (101, False)])
def test_legacy_history_recovery_has_a_bounded_identity_scan(binding_db, gap, continued):
    engine, _, body = binding_db
    put(engine, body)
    changed = changed_definition(engine, body)
    with engine.begin() as conn:
        original = dict(conn.execute(select(checklist_versions)).mappings().one())
        for n in range(gap):
            target = uuid4().hex
            conn.execute(insert(checklist_versions).values(**(original | {
                "id": target, "version": n + 2, "definition_json": changed,
                "content_digest": digest([changed, []]),
            })))
        conn.execute(update(checklist_heads).values(confirmed_version_id=target, revision=2 + gap))
    with engine.begin() as conn:
        frozen = freeze_current(conn, "case", "snap", target, changed, allow_continuation=True)
    assert (frozen["version_id"] is not None) is continued
    assert count_versions(engine) == (2 if continued else 1)


def test_continuation_never_crosses_input_snapshot_or_case_deletion(binding_db):
    from backend.app.db.models import input_snapshots

    engine, _, body = binding_db
    original = put(engine, body)["current_version"]
    definition = changed_definition(engine, body)
    with engine.begin() as conn:
        conn.execute(insert(input_snapshots).values(id="next", case_id="case", revision=2, created_at=NOW))
        conn.execute(update(cases).values(revision=2))
    checklist = ChecklistRepository(engine)
    checklist.put("case", "next", definition, actor=ACTOR, expected_revision=2,
                  expected_checklist_revision=2, idempotency_key="new-snapshot-draft", reason="新资料快照")
    confirmation = checklist.confirm("case", "next", ["rice-0"], actor=ACTOR, expected_revision=2,
                  expected_checklist_revision=3, confirmed=True, idempotency_key="new-snapshot-confirm", reason="核对")
    with engine.begin() as conn:
        frozen = freeze_current(conn, "case", "next", confirmation["confirmed_version"]["id"], definition,
                                allow_continuation=True)
    assert frozen["version_id"] is None and count_versions(engine) == 1
    assert ItemBindingRepository(engine).version("case", original["id"]) == original
    with engine.begin() as conn:
        conn.execute(update(cases).values(deletion_requested_at=NOW))
    with engine.begin() as conn, pytest.raises(ValueError, match="case_deleting"):
        freeze_current(conn, "case", "next", confirmation["confirmed_version"]["id"], definition,
                       allow_continuation=True)
