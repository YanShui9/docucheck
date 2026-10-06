"""A correction is revalidated and evaluated without invoking a model."""

from dataclasses import asdict
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import func, insert, select, update

from backend.app.agent.graph import AgentSession
from backend.app.agent.session_memory import serialize_session
from backend.app.db.agent_snapshot import load_agent_snapshot
from backend.app.db.models import (
    agent_runs,
    cases,
    correction_reviews,
    corrections,
    document_versions,
    evidence_blocks,
    input_snapshots,
)
from backend.app.evidence.repository import digest
from backend.app.extraction.fields import FieldCandidate, _validate_candidate
from backend.app.extraction.memory import FactSet
from backend.app.extraction.validation import Candidate, validate_memory
from backend.app.field_contract import FieldFactSet, FieldKey
from backend.tests.test_corrections import _request
from backend.tests.test_corrections import prepared as _prepared


@pytest.fixture
def prepared(tmp_path, monkeypatch):
    yield from _prepared.__wrapped__(tmp_path, monkeypatch)


def _submit(client, case_id, run_id, set_id, fact_id):
    response = client.post(
        f"/api/v1/cases/{case_id}/corrections",
        headers={"Idempotency-Key": "recheck-source"},
        json=_request(case_id, run_id, set_id, fact_id),
    )
    assert response.status_code == 201, response.text
    return response.json()


def _recheck(client, case_id, correction_id):
    return client.post(
        f"/api/v1/cases/{case_id}/corrections/{correction_id}/recheck",
    )


def test_partial_fact_set_recheck_never_creates_business_status(prepared):
    client, engine, case_id, run_id, set_id, fact_id, block = prepared
    receipt = _submit(client, case_id, run_id, set_id, fact_id)
    first = _recheck(client, case_id, receipt["correction_id"])
    assert first.status_code == 200, first.text
    result = first.json()
    assert result["review_status"] == "needs_review"
    assert result["check_result"]["processing_status"] == "partial"
    assert result["check_result"]["business_status"] is None
    assert result["check_result"]["snapshot_id"] == receipt["snapshot_id"]
    assert result["corrected_fact"]["value"] == "1000"
    assert result["corrected_fact"]["fact_id"] != fact_id
    assert result["corrected_fact"]["evidence_refs"] != [block.evidence_id]
    assert _recheck(client, case_id, receipt["correction_id"]).json() == result
    assert client.get(
        f"/api/v1/cases/{case_id}/corrections/{receipt['correction_id']}"
    ).json() == result
    with engine.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(corrections)) == 1


def test_legacy_memory_fact_set_uses_same_source_bound_recheck(prepared):
    client, engine, case_id, run_id, _, _, block = prepared
    with engine.begin() as connection:
        connection.execute(update(evidence_blocks).where(
            evidence_blocks.c.id == block.evidence_id,
        ).values(source_text="已装内存 16 GB 或 32 GB"))
    snapshot_id = client.get(f"/api/v1/agent-runs/{run_id}").json()["snapshot_id"]
    repo = load_agent_snapshot(engine, case_id, snapshot_id)
    fact = validate_memory(Candidate(
        raw_value="16", raw_unit="GB", qualifier="unknown", comparator="eq",
        evidence_refs=[block.evidence_id],
    ), repo, "vendor-a")
    scanned = (block.evidence_id,)
    identity = digest([
        repo.snapshot_id, "vendor-a", scanned, False,
        [asdict(fact) | {"value": str(fact.value)}],
    ])
    session = AgentSession(repo, object())
    session.tools.fact_sets[identity] = FactSet(
        identity, "vendor-a", (fact,), scanned, False,
    )
    with engine.begin() as connection:
        connection.execute(update(agent_runs).where(agent_runs.c.id == run_id).values(
            memory_json=serialize_session(session, "old"),
        ))
    response = client.post(
        f"/api/v1/cases/{case_id}/corrections",
        headers={"Idempotency-Key": "memory-recheck"},
        json=_request(case_id, run_id, identity, fact.fact_id,
                      value="32", unit="GB"),
    )
    assert response.status_code == 201, response.text
    receipt = response.json()
    reviewed = _recheck(client, case_id, receipt["correction_id"])
    assert reviewed.status_code == 200, reviewed.text
    assert reviewed.json()["check_result"]["business_status"] is None
    with engine.connect() as connection:
        saved = connection.execute(select(correction_reviews)).mappings().one()
    assert {item["value"] for item in saved["fact_set_json"]["facts"]} == {
        "16", "32",
    }


def test_complete_fact_set_rechecks_with_existing_rule(prepared):
    client, engine, case_id, run_id, _, _, block = prepared
    now = datetime.now(UTC).replace(tzinfo=None)
    snapshot_id = client.get(f"/api/v1/agent-runs/{run_id}").json()["snapshot_id"]
    with engine.begin() as connection:
        supplier_id = connection.scalar(select(document_versions.c.supplier_id).where(
            document_versions.c.snapshot_id == snapshot_id,
        ))
        for role, source_text in (("requirement", "标称存储至少 512 GB"),
                                  ("spec", "标称 SSD 1 TB")):
            document_id = uuid4().hex
            connection.execute(insert(document_versions).values(
                id=document_id, case_id=case_id, snapshot_id=snapshot_id,
                supplier_id=(None if role == "requirement" else supplier_id),
                role=role, original_name=f"{role}.pdf",
                storage_key=f"uploads/test/{role}.pdf", source_sha256=role[0] * 64,
                media_type="application/pdf", parser_version="test:1",
                parse_status="complete",
                coverage_json={"parser_version_full": "test:1", "total_pages": 1,
                               "processed_pages": [1], "coverage_issues": []},
                created_at=now,
            ))
            connection.execute(insert(evidence_blocks).values(
                id=uuid4().hex, document_version_id=document_id, ordinal=0,
                source_text=source_text, page_idx=0, created_at=now,
            ))
    repo = load_agent_snapshot(engine, case_id, client.get(
        f"/api/v1/agent-runs/{run_id}").json()["snapshot_id"])
    original = _validate_candidate(FieldCandidate(
        field_key=FieldKey.STORAGE, raw_value="512", raw_unit="GB",
        evidence_refs=[block.evidence_id],
    ), repo, "vendor-a")
    alternative = _validate_candidate(FieldCandidate(
        field_key=FieldKey.STORAGE, raw_value="1", raw_unit="TB",
        evidence_refs=[block.evidence_id],
    ), repo, "vendor-a")
    requirement = next(item for item in repo.blocks if item.role == "requirement")
    required = _validate_candidate(FieldCandidate(
        field_key=FieldKey.STORAGE, raw_value="512", raw_unit="GB",
        evidence_refs=[requirement.evidence_id],
    ), repo, "vendor-a")
    # A complete set must also retain the spec source, not just the two values
    # in the quote. The correction must still preserve both quote alternatives.
    spec = next(item for item in repo.blocks if item.role == "spec")
    specified = _validate_candidate(FieldCandidate(
        field_key=FieldKey.STORAGE, raw_value="1", raw_unit="TB",
        evidence_refs=[spec.evidence_id],
    ), repo, "vendor-a")
    facts = (required, original, alternative, specified)
    scanned = tuple(item.evidence_id for item in repo.supplier_blocks("vendor-a"))
    identity = digest([
        repo.snapshot_id, "vendor-a", "storage", scanned, True,
        [asdict(fact) | {"value": str(fact.value)} for fact in facts],
    ])
    fact_set = FieldFactSet(
        fact_set_id=identity, snapshot_id=repo.snapshot_id,
        supplier="vendor-a", field_key=FieldKey.STORAGE,
        facts=facts, scanned_ids=scanned, complete=True,
    )
    session = AgentSession(repo, object())
    session.tools.fact_sets[identity] = fact_set
    with engine.begin() as connection:
        connection.execute(update(agent_runs).where(agent_runs.c.id == run_id).values(
            memory_json=serialize_session(session, "old"),
        ))
    receipt = _submit(client, case_id, run_id, identity, original.fact_id)
    reviewed = _recheck(client, case_id, receipt["correction_id"])
    assert reviewed.status_code == 200, reviewed.text
    result = reviewed.json()
    assert result["review_status"] == "needs_review"
    assert result["check_result"]["processing_status"] == "complete"
    assert result["check_result"]["business_status"] == "待人工确认"
    assert result["check_result"]["reason_code"] == "semantics_unclear"
    assert result["corrected_fact"]["value"] == "1000"
    with engine.connect() as connection:
        review = connection.execute(select(correction_reviews)).mappings().one()
    assert {fact["value"] for fact in review["fact_set_json"]["facts"]} == {"512", "1000"}


def test_tampered_new_evidence_refuses_recheck(prepared):
    client, engine, case_id, run_id, set_id, fact_id, _ = prepared
    receipt = _submit(client, case_id, run_id, set_id, fact_id)
    audit = client.get(
        f"/api/v1/cases/{case_id}/corrections/{receipt['correction_id']}"
    ).json()
    with engine.begin() as connection:
        connection.execute(update(evidence_blocks).where(
            evidence_blocks.c.id == audit["target_evidence_ids"][0],
        ).values(source_text="标称 SSD 2 TB"))
    rejected = _recheck(client, case_id, receipt["correction_id"])
    assert rejected.status_code == 409
    assert rejected.json()["error"]["code"] == "correction_source_changed"
    assert client.get(
        f"/api/v1/cases/{case_id}/corrections/{receipt['correction_id']}"
    ).json()["review_status"] == "pending_recheck"


def test_tampered_audit_mapping_refuses_recheck(prepared):
    client, engine, case_id, run_id, set_id, fact_id, _ = prepared
    receipt = _submit(client, case_id, run_id, set_id, fact_id)
    with engine.begin() as connection:
        connection.execute(update(corrections).where(
            corrections.c.id == receipt["correction_id"],
        ).values(target_evidence_ids_json=["wrong"] * 1))
    rejected = _recheck(client, case_id, receipt["correction_id"])
    assert rejected.status_code == 409
    assert rejected.json()["error"]["code"] == "correction_source_changed"


def test_tampered_source_fact_memory_refuses_recheck(prepared):
    client, engine, case_id, run_id, set_id, fact_id, _ = prepared
    receipt = _submit(client, case_id, run_id, set_id, fact_id)
    with engine.begin() as connection:
        original = connection.scalar(select(agent_runs.c.memory_json).where(
            agent_runs.c.id == run_id,
        ))
        connection.execute(update(agent_runs).where(agent_runs.c.id == run_id).values(
            memory_json={**original, "repository_fingerprint": "0" * 64},
        ))
    rejected = _recheck(client, case_id, receipt["correction_id"])
    assert rejected.status_code == 409
    assert rejected.json()["error"]["code"] == "memory_scope"


def test_saved_review_refuses_modified_source_text(prepared):
    client, engine, case_id, run_id, set_id, fact_id, _ = prepared
    receipt = _submit(client, case_id, run_id, set_id, fact_id)
    reviewed = _recheck(client, case_id, receipt["correction_id"])
    assert reviewed.status_code == 200, reviewed.text
    with engine.begin() as connection:
        connection.execute(update(evidence_blocks).where(
            evidence_blocks.c.id == reviewed.json()["corrected_fact"]["evidence_refs"][0],
        ).values(source_text="标称 SSD 2 TB"))
    rejected = client.get(
        f"/api/v1/cases/{case_id}/corrections/{receipt['correction_id']}"
    )
    assert rejected.status_code == 409
    assert rejected.json()["error"]["code"] == "correction_source_changed"


def test_historical_correction_is_not_rechecked(prepared):
    client, engine, case_id, run_id, set_id, fact_id, _ = prepared
    receipt = _submit(client, case_id, run_id, set_id, fact_id)
    now = datetime.now(UTC).replace(tzinfo=None)
    with engine.begin() as connection:
        connection.execute(insert(input_snapshots).values(
            id="later-snapshot", case_id=case_id, revision=3,
            created_at=now,
        ))
        connection.execute(update(cases).where(cases.c.id == case_id).values(
            revision=3, updated_at=now,
        ))
    rejected = _recheck(client, case_id, receipt["correction_id"])
    assert rejected.status_code == 409
    assert rejected.json()["error"]["code"] == "revision_conflict"


def test_finished_recheck_remains_readable_after_later_revision(prepared):
    client, engine, case_id, run_id, set_id, fact_id, _ = prepared
    receipt = _submit(client, case_id, run_id, set_id, fact_id)
    original = _recheck(client, case_id, receipt["correction_id"])
    assert original.status_code == 200, original.text
    now = datetime.now(UTC).replace(tzinfo=None)
    with engine.begin() as connection:
        connection.execute(insert(input_snapshots).values(
            id="later-snapshot", case_id=case_id, revision=3, created_at=now,
        ))
        connection.execute(update(cases).where(cases.c.id == case_id).values(
            revision=3, updated_at=now,
        ))
    replay = _recheck(client, case_id, receipt["correction_id"])
    assert replay.status_code == 200, replay.text
    assert replay.json() == original.json()
