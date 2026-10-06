# ruff: noqa: F811 -- Imported pytest fixture is resolved by name.
from copy import deepcopy

import pytest
from pydantic import ValidationError
from sqlalchemy import insert, select, update

from backend.app.db.models import (
    cases,
    document_versions,
    evidence_blocks,
    input_snapshots,
)
from backend.tests.test_index_repository import NOW, indexed_db  # noqa: F401

ACTOR = "a" * 64
OTHER = "b" * 64


def definition(count=10):
    return {
        "items": [{"item_id": "rice", "name": "大米"}],
        "criteria": [
            {
                "criterion_id": f"rice-{n}",
                "item_id": "rice",
                "name": f"采购指标{n}",
                "rule": {
                    "type": "numeric",
                    "operator": "gte",
                    "value": str(n),
                    "unit": "袋",
                },
                "source": {"kind": "user_defined", "reason": "买方明确设置"},
            }
            for n in range(count)
        ],
    }


def service(engine):
    from backend.app.db.checklist_repository import ChecklistRepository

    return ChecklistRepository(engine)


def put(
    engine,
    body=None,
    *,
    revision=0,
    key="save",
    actor=ACTOR,
    snap="snap",
    input_revision=1,
):
    return service(engine).put(
        "case",
        snap,
        body or definition(),
        actor=actor,
        expected_revision=input_revision,
        expected_checklist_revision=revision,
        idempotency_key=key,
        reason="设置本次采购要求",
    )


def confirm(engine, ids=None, *, revision=1, key="confirm", actor=ACTOR):
    return service(engine).confirm(
        "case",
        "snap",
        ids or [f"rice-{n}" for n in range(10)],
        actor=actor,
        expected_revision=1,
        expected_checklist_revision=revision,
        confirmed=True,
        idempotency_key=key,
        reason="已核对本次采购要求",
    )


def test_food_checklist_supports_more_than_eight_and_survives_reopen(indexed_db):
    from sqlalchemy import create_engine

    engine, path = indexed_db
    saved = put(engine)
    assert saved["checklist_revision"] == 1 and len(saved["draft"]["criteria"]) == 10
    result = confirm(engine)
    assert result["state"] == "confirmed" and result["execution_available"] is True
    assert len(result["confirmed_version"]["definition"]["criteria"]) == 10
    reopened = create_engine(f"sqlite:///{path}")
    try:
        assert (
            service(reopened).get("case", "snap")["confirmed_version"]["id"]
            == result["confirmed_version"]["id"]
        )
    finally:
        reopened.dispose()


def test_multiple_items_can_have_same_metric_and_conditions_stay_manual(indexed_db):
    engine, _ = indexed_db
    body = definition(2)
    body["items"].append({"item_id": "apple", "name": "苹果"})
    body["criteria"][1]["item_id"] = "apple"
    body["criteria"][1]["name"] = body["criteria"][0]["name"]
    body["criteria"][1]["conditions"] = ["仅适用于一级果"]
    put(engine, body)
    result = confirm(engine, ["rice-0", "rice-1"])
    assert len(result["confirmed_version"]["definition"]["items"]) == 2
    assert result["confirmed_version"]["manual_review_ids"] == ["rice-1"]


@pytest.mark.parametrize(
    "change",
    ["duplicate_item", "duplicate_criterion", "unknown_item", "too_many", "bad_id"],
)
def test_identity_and_capacity_limits_are_explicit(change):
    from backend.app.criteria.models import ChecklistDefinition

    body = definition(2)
    if change == "duplicate_item":
        body["items"].append(deepcopy(body["items"][0]))
    elif change == "duplicate_criterion":
        body["criteria"][1]["criterion_id"] = "rice-0"
    elif change == "unknown_item":
        body["criteria"][0]["item_id"] = "absent"
    elif change == "too_many":
        body = definition(101)
    else:
        body["criteria"][0]["criterion_id"] = "../other"
    with pytest.raises(ValidationError):
        ChecklistDefinition.model_validate(body)


@pytest.mark.parametrize(
    "source_scope", ["other_case", "other_snapshot", "supplier_document"]
)
def test_requirement_context_cannot_cross_case_snapshot_or_supplier_role(
    indexed_db, source_scope
):
    from backend.app.db.checklist_repository import ChecklistError
    from backend.app.db.models import checklist_heads, checklist_operations, suppliers

    engine, _ = indexed_db
    with engine.begin() as conn:
        if source_scope == "supplier_document":
            conn.execute(
                insert(suppliers).values(
                    id="vendor",
                    case_id="case",
                    handle="vendor",
                    name="供应商",
                    created_at=NOW,
                )
            )
            conn.execute(
                update(document_versions).values(role="quote", supplier_id="vendor")
            )
        else:
            other_case = "other" if source_scope == "other_case" else "case"
            if other_case == "other":
                conn.execute(
                    insert(cases).values(
                        id="other", revision=1, created_at=NOW, updated_at=NOW
                    )
                )
            conn.execute(
                insert(input_snapshots).values(
                    id="foreign", case_id=other_case, revision=2, created_at=NOW
                )
            )
            conn.execute(
                update(document_versions).values(
                    case_id=other_case, snapshot_id="foreign"
                )
            )
        conn.execute(
            insert(evidence_blocks).values(
                id="foreign-e",
                document_version_id="doc",
                ordinal=0,
                source_text="采购数量 120 袋",
                page_idx=0,
                created_at=NOW,
            )
        )
    body = definition(1)
    body["criteria"][0]["source"]["context"] = {
        "evidence_refs": ["foreign-e"],
        "quote": "采购数量 120 袋",
    }
    with pytest.raises(ChecklistError, match="checklist_source_scope"):
        put(engine, body)
    with engine.connect() as conn:
        assert conn.execute(select(checklist_heads)).first() is None
        assert conn.execute(select(checklist_operations)).first() is None


def test_explicit_edit_replaces_partial_base_and_retains_history(indexed_db):
    engine, _ = indexed_db
    put(engine, definition(2))
    old = confirm(engine, ["rice-0"])["confirmed_version"]["id"]
    body = definition(1)
    body["criteria"][0]["criterion_id"] = "new-metric"
    put(engine, body, revision=2, key="replacement")
    current = confirm(engine, ["new-metric"], revision=3, key="confirm-replacement")
    assert [
        c["criterion_id"]
        for c in current["confirmed_version"]["definition"]["criteria"]
    ] == ["new-metric"]
    assert (
        service(engine).version("case", old)["definition"]["criteria"][0][
            "criterion_id"
        ]
        == "rice-0"
    )


@pytest.mark.parametrize(
    "rule",
    [
        {"type": "numeric", "operator": "gte", "value": 0.1, "unit": "kg"},
        {"type": "numeric", "operator": "gte", "value": "NaN", "unit": "kg"},
        {
            "type": "numeric",
            "operator": "between",
            "lower": "5",
            "upper": "2",
            "unit": "kg",
        },
        {"type": "money", "operator": "lte", "value": "120", "currency": "CNY"},
        {"type": "python", "code": "print('do not run')"},
    ],
)
def test_invalid_rules_are_rejected_without_coercion(rule):
    from backend.app.criteria.models import ChecklistDefinition

    body = definition(1)
    body["criteria"][0]["rule"] = rule
    with pytest.raises(ValidationError):
        ChecklistDefinition.model_validate(body)


def test_money_unknown_tax_and_duplicate_identity_require_resolution(indexed_db):
    from backend.app.db.checklist_repository import ChecklistError

    engine, _ = indexed_db
    body = definition(1)
    body["criteria"][0]["rule"] = {
        "type": "money",
        "operator": "lte",
        "value": "120",
        "currency": "CNY",
        "per_unit": "袋",
        "tax_basis": "unspecified",
    }
    saved = put(engine, body)
    assert saved["state"] == "needs_resolution"
    with pytest.raises(ChecklistError, match="checklist_needs_resolution"):
        confirm(engine, ["rice-0"])


def test_versions_are_immutable_and_edits_require_revision_and_idempotency(indexed_db):
    from backend.app.db.checklist_repository import ChecklistError

    engine, _ = indexed_db
    first = put(engine)
    assert put(engine) == first
    changed = definition()
    changed["criteria"][0]["rule"]["value"] = "100"
    with pytest.raises(ChecklistError, match="idempotency_conflict"):
        put(engine, changed)
    with pytest.raises(ChecklistError, match="checklist_revision_conflict"):
        put(engine, changed, key="other")
    confirmed = confirm(engine)
    assert confirm(engine) == confirmed
    old_id = confirmed["confirmed_version"]["id"]
    put(engine, changed, revision=2, key="edit")
    current = confirm(engine, revision=3, key="confirm-edit")
    assert current["confirmed_version"]["version"] == 2
    old = service(engine).version("case", old_id)
    assert old["definition"]["criteria"][0]["rule"]["value"] == "0"
    assert (
        current["confirmed_version"]["definition"]["criteria"][0]["rule"]["value"]
        == "100"
    )


def test_partial_confirmation_preserves_remaining_draft_and_manual_review(indexed_db):
    engine, _ = indexed_db
    body = definition(3)
    body["criteria"][0]["rule"] = {
        "type": "manual",
        "instruction": "核对证照真实有效性",
    }
    put(engine, body)
    result = confirm(engine, ["rice-0", "rice-1"])
    assert result["state"] == "partially_confirmed"
    assert result["excluded_criterion_ids"] == ["rice-2"]
    assert result["draft"]["criteria"][0]["criterion_id"] == "rice-2"
    assert result["confirmed_version"]["manual_review_ids"] == ["rice-0"]


def test_confirmation_is_explicit_and_cannot_pick_unknown_disabled_or_conflicting_items(
    indexed_db,
):
    from backend.app.db.checklist_repository import ChecklistError

    engine, _ = indexed_db
    body = definition(3)
    body["criteria"][1]["enabled"] = False
    body["criteria"][2]["name"] = body["criteria"][0]["name"]
    put(engine, body)
    for ids, error in [
        (["foreign"], "invalid_checklist_selection"),
        (["rice-1"], "invalid_checklist_selection"),
        (["rice-0", "rice-2"], "checklist_needs_resolution"),
    ]:
        with pytest.raises(ChecklistError, match=error):
            confirm(engine, ids, key=error + str(ids))
    with pytest.raises(ChecklistError, match="checklist_confirmation_required"):
        service(engine).confirm(
            "case",
            "snap",
            ["rice-0"],
            actor=ACTOR,
            expected_revision=1,
            expected_checklist_revision=1,
            confirmed=False,
            idempotency_key="unconfirmed",
            reason="未确认",
        )


def test_requirement_context_is_source_bound_and_never_changes_user_defined_provenance(
    indexed_db,
):
    from backend.app.db.checklist_repository import ChecklistError

    engine, _ = indexed_db
    with engine.begin() as conn:
        conn.execute(
            insert(evidence_blocks).values(
                id="e",
                document_version_id="doc",
                ordinal=0,
                source_text="采购数量 120 袋",
                page_idx=0,
                created_at=NOW,
            )
        )
    body = definition(1)
    body["criteria"][0]["source"]["context"] = {
        "evidence_refs": ["e"],
        "quote": "采购数量 120 袋",
    }
    saved = put(engine, body)
    assert saved["draft"]["criteria"][0]["source"]["kind"] == "user_defined"
    # Explicit user requirements may differ from the referenced background.
    assert saved["draft"]["criteria"][0]["rule"]["value"] == "0"
    bad = deepcopy(body)
    bad["criteria"][0]["source"]["context"]["quote"] = "采购数量 999 袋"
    with pytest.raises(ChecklistError, match="checklist_source_scope"):
        put(engine, bad, revision=1, key="fake-quote")
    with engine.begin() as conn:
        conn.execute(update(evidence_blocks).values(source_text="改过的原文"))
    with pytest.raises(ChecklistError, match="checklist_source_changed"):
        service(engine).get("case", "snap")


def test_snapshot_change_requires_reconfirmation_and_delete_blocks_writes(indexed_db):
    from backend.app.db.checklist_repository import ChecklistError

    engine, _ = indexed_db
    put(engine)
    original = confirm(engine)["confirmed_version"]["id"]
    with engine.begin() as conn:
        conn.execute(
            insert(input_snapshots).values(
                id="new", case_id="case", revision=2, created_at=NOW
            )
        )
        conn.execute(update(cases).values(revision=2))
    view = service(engine).get("case", "new")
    assert view["state"] == "needs_reconfirmation" and view["confirmed_version"] is None
    old_view = service(engine).get("case", "snap")
    assert old_view["state"] == "historical"
    assert old_view["is_current_snapshot"] is False
    assert service(engine).version("case", original)["snapshot_id"] == "snap"
    with pytest.raises(ChecklistError, match="checklist_snapshot_changed"):
        put(engine, revision=2, key="stale")
    put(engine, revision=2, key="new-snapshot", snap="new", input_revision=2)
    with engine.begin() as conn:
        conn.execute(update(cases).values(deletion_requested_at=NOW))
    with pytest.raises(ChecklistError, match="case_deleting"):
        put(engine, revision=3, key="delete", snap="new", input_revision=2)


@pytest.mark.parametrize("quote", [" ", "\n\t", "\u3000"])
def test_blank_source_quote_cannot_be_saved_as_evidence(indexed_db, quote):
    from backend.app.db.checklist_repository import ChecklistError

    engine, _ = indexed_db
    with engine.begin() as conn:
        conn.execute(
            insert(evidence_blocks).values(
                id="e",
                document_version_id="doc",
                ordinal=0,
                source_text="采购数量 120 袋",
                page_idx=0,
                created_at=NOW,
            )
        )
    body = definition(1)
    body["criteria"][0]["source"]["context"] = {"evidence_refs": ["e"], "quote": quote}
    with pytest.raises(ChecklistError, match="invalid_checklist"):
        put(engine, body)


def test_actor_scoped_operation_keys_and_server_authorship(indexed_db):
    engine, _ = indexed_db
    put(engine)
    other = put(engine, revision=1, actor=OTHER)
    assert other["checklist_revision"] == 2
    current = confirm(engine, revision=2, actor=OTHER)
    assert current["confirmed_version"]["confirmed_by"] == OTHER
    from backend.app.db.models import checklist_operations

    with engine.connect() as conn:
        assert len(conn.execute(select(checklist_operations)).all()) == 3


def test_successive_partial_confirmations_accumulate_in_current_version(indexed_db):
    engine, _ = indexed_db
    put(engine, definition(3))
    confirm(engine, ["rice-0"])
    second = confirm(engine, ["rice-1"], revision=2, key="confirm-second")
    assert {
        c["criterion_id"] for c in second["confirmed_version"]["definition"]["criteria"]
    } == {"rice-0", "rice-1"}
    final = confirm(engine, ["rice-2"], revision=3, key="confirm-last")
    assert final["state"] == "confirmed" and final["draft"] is None
    assert len(final["confirmed_version"]["definition"]["criteria"]) == 3
