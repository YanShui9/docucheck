"""Source-backed correction creates an auditable, immutable Case revision."""

from dataclasses import asdict
from datetime import UTC, datetime
from io import BytesIO

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook
from sqlalchemy import create_engine, event, func, insert, select, update

from backend.app.agent.graph import AgentSession
from backend.app.agent.session_memory import serialize_session
from backend.app.api.main import create_app
from backend.app.db.agent_snapshot import load_agent_snapshot
from backend.app.db.ingestion import DocumentIngestion
from backend.app.db.models import (
    agent_runs,
    cases,
    corrections,
    document_versions,
    evidence_blocks,
    input_snapshots,
    metadata,
    run_attempts,
)
from backend.app.db.run_repository import RepositoryError, RunRepository, SubmitRun
from backend.app.evidence.repository import digest
from backend.app.extraction.fields import FieldCandidate, _validate_candidate
from backend.app.field_contract import FieldFactSet, FieldKey


@pytest.fixture
def prepared(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'corrections.db'}")

    @event.listens_for(engine, "connect")
    def foreign_keys(connection, record):
        connection.execute("PRAGMA foreign_keys=ON")

    metadata.create_all(engine)
    storage = tmp_path / "data"
    monkeypatch.setenv("DOCUCHECK_DATA_DIR", str(storage))
    with TestClient(create_app(engine)) as client:
        case_id = client.post("/api/v1/cases").json()["id"]
        workbook = Workbook()
        workbook.active.title = "报价"
        workbook.active["B6"] = "标称 SSD 512 GB；标称 SSD 1 TB"
        stream = BytesIO()
        workbook.save(stream)
        uploaded = client.post(
            f"/api/v1/cases/{case_id}/documents",
            headers={"Idempotency-Key": "upload-1"},
            data={"role": "quote", "supplier_handle": "vendor-a",
                  "supplier_name": "vendor-a", "expected_revision": "0"},
            files={"file": ("quote.xlsx", stream.getvalue(),
                            "application/octet-stream")},
        )
        assert uploaded.status_code == 201, uploaded.text
        upload = uploaded.json()
        DocumentIngestion(engine, storage).process_document(
            case_id, upload["snapshot_id"], upload["document_id"])
        repo = load_agent_snapshot(engine, case_id, upload["snapshot_id"])
        block = next(block for block in repo.blocks if block.cell == "B6")
        fact = _validate_candidate(FieldCandidate(
            field_key=FieldKey.STORAGE, raw_value="512", raw_unit="GB",
            evidence_refs=[block.evidence_id],
        ), repo, "vendor-a")
        scanned = (block.evidence_id,)
        fact_set_id = digest([
            repo.snapshot_id, "vendor-a", "storage", scanned, False,
            [asdict(fact) | {"value": str(fact.value)}],
        ])
        fact_set = FieldFactSet(
            fact_set_id=fact_set_id, snapshot_id=repo.snapshot_id,
            supplier="vendor-a", field_key=FieldKey.STORAGE,
            facts=(fact,), scanned_ids=scanned, complete=False,
            coverage_issues=("test_partial",),
        )
        session = AgentSession(repo, object())
        session.tools.fact_sets[fact_set_id] = fact_set
        conversation = client.post(f"/api/v1/cases/{case_id}/conversations").json()
        submitted = client.post(
            f"/api/v1/conversations/{conversation['id']}/messages",
            headers={"Idempotency-Key": "run-1"},
            json={"content": "审查存储", "expected_revision": 1},
        )
        assert submitted.status_code == 202, submitted.text
        run_id = submitted.json()["run_id"]
        with engine.begin() as connection:
            connection.execute(update(agent_runs).where(agent_runs.c.id == run_id).values(
                status="completed", memory_json=serialize_session(session, "old"),
            ))
            connection.execute(update(run_attempts).where(
                run_attempts.c.id == submitted.json()["attempt_id"],
            ).values(status="completed"))
        yield client, engine, case_id, run_id, fact_set_id, fact.fact_id, block
    engine.dispose()


def _request(case_id, run_id, set_id, fact_id, *, value="1", unit="TB",
             revision=1, reason="原文另有 1 TB 的数值"):
    return {"source_run_id": run_id, "fact_set_id": set_id,
            "fact_id": fact_id, "raw_value": value, "raw_unit": unit,
            "reason": reason, "expected_revision": revision}


def test_correction_copies_evidence_and_invalidates_old_run(prepared):
    client, engine, case_id, run_id, set_id, fact_id, block = prepared
    facts = client.get(f"/api/v1/agent-runs/{run_id}/facts")
    assert facts.status_code == 200, facts.text
    assert facts.json()["available"] is True
    assert facts.json()["fact_sets"][0]["facts"][0]["fact_id"] == fact_id
    body = _request(case_id, run_id, set_id, fact_id)
    url = f"/api/v1/cases/{case_id}/corrections"
    first = client.post(url, headers={"Idempotency-Key": "correction-1"}, json=body)
    assert first.status_code == 201, first.text
    receipt = first.json()
    assert receipt["revision"] == 2
    assert receipt["review_status"] == "pending_recheck"
    assert receipt["source_run_id"] == run_id
    assert receipt["from_snapshot_id"] != receipt["snapshot_id"]
    audit_response = client.get(f"{url}/{receipt['correction_id']}")
    assert audit_response.status_code == 200, audit_response.text
    assert audit_response.json()["old_fact"]["fact_id"] == fact_id
    assert audit_response.json()["corrected_candidate"]["validated_value"] == "1000"
    assert audit_response.json()["reason"] == body["reason"]
    assert client.get(f"/api/v1/agent-runs/{run_id}").json()["is_current"] is False
    with engine.connect() as connection:
        conversation_id = connection.scalar(select(agent_runs.c.conversation_id).where(
            agent_runs.c.id == run_id))
    pending = client.post(
        f"/api/v1/conversations/{conversation_id}/messages",
        headers={"Idempotency-Key": "before-recheck"},
        json={"content": "继续审查", "expected_revision": 2},
    )
    assert pending.status_code == 409
    assert pending.json()["error"]["code"] == "correction_pending_recheck"
    with pytest.raises(RepositoryError, match="correction_pending_recheck"):
        RunRepository(engine).submit(SubmitRun(
            case_id=case_id, conversation_id=conversation_id,
            snapshot_id=receipt["snapshot_id"], expected_revision=2,
            content="绕过 API 发起审查", mode="standard",
            idempotency_key="direct-before-recheck",
        ))
    assert client.post(url, headers={"Idempotency-Key": "correction-1"},
                       json=body).json() == receipt
    changed = client.post(url, headers={"Idempotency-Key": "correction-1"},
                          json=body | {"reason": "另一理由"})
    assert changed.status_code == 409
    assert changed.json()["error"]["code"] == "idempotency_conflict"
    stale = client.post(url, headers={"Idempotency-Key": "correction-2"}, json=body)
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "revision_conflict"
    with engine.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(corrections)) == 1
        assert connection.scalar(select(func.count()).select_from(input_snapshots)) == 2
        old = connection.execute(select(evidence_blocks).where(
            evidence_blocks.c.id == block.evidence_id)).mappings().one()
        new = connection.execute(select(evidence_blocks).join(
            document_versions,
            evidence_blocks.c.document_version_id == document_versions.c.id,
        ).where(document_versions.c.snapshot_id == receipt["snapshot_id"])).mappings().one()
        audit = connection.execute(select(corrections)).mappings().one()
    assert new["id"] != old["id"]
    assert new["source_text"] == old["source_text"]
    assert audit["source_evidence_ids_json"] == [old["id"]]
    assert audit["target_evidence_ids_json"] == [new["id"]]
    assert audit["corrected_candidate_json"]["raw_value"] == "1"
    assert len(load_agent_snapshot(engine, case_id, receipt["snapshot_id"]).blocks) == 1
    assert client.get(f"/api/v1/agent-runs/{run_id}/facts").json()["is_current"] is False


@pytest.mark.parametrize(("change", "code"), [
    ({"raw_value": "2"}, "unsupported_correction"),
    ({"raw_value": "512", "raw_unit": "GB"}, "correction_unchanged"),
    ({"fact_id": "unknown"}, "fact_not_found"),
    ({"fact_set_id": "unknown"}, "fact_not_found"),
])
def test_correction_rejects_invalid_fact_without_revision(prepared, change, code):
    client, engine, case_id, run_id, set_id, fact_id, _ = prepared
    response = client.post(
        f"/api/v1/cases/{case_id}/corrections",
        headers={"Idempotency-Key": "invalid"},
        json=_request(case_id, run_id, set_id, fact_id) | change,
    )
    assert response.status_code in {404, 422}, response.text
    assert response.json()["error"]["code"] == code
    with engine.connect() as connection:
        assert connection.scalar(select(cases.c.revision).where(cases.c.id == case_id)) == 1
        assert connection.scalar(select(func.count()).select_from(corrections)) == 0


def test_active_run_blocks_correction(prepared):
    client, engine, case_id, run_id, set_id, fact_id, _ = prepared
    with engine.begin() as connection:
        connection.execute(update(agent_runs).where(agent_runs.c.id == run_id).values(
            status="running"))
    response = client.post(
        f"/api/v1/cases/{case_id}/corrections",
        headers={"Idempotency-Key": "busy"},
        json=_request(case_id, run_id, set_id, fact_id),
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "case_busy"


def test_correction_rejects_client_evidence_and_cross_case_run(prepared):
    client, engine, case_id, run_id, set_id, fact_id, block = prepared
    url = f"/api/v1/cases/{case_id}/corrections"
    body = _request(case_id, run_id, set_id, fact_id)
    forged = client.post(url, headers={"Idempotency-Key": "forged"},
                         json=body | {"evidence_refs": [block.evidence_id]})
    assert forged.status_code == 422
    assert forged.json()["error"]["code"] == "validation_error"
    other_case = client.post("/api/v1/cases").json()["id"]
    now = datetime.now(UTC).replace(tzinfo=None)
    with engine.begin() as connection:
        connection.execute(insert(input_snapshots).values(
            id="foreign-snapshot", case_id=other_case, revision=1,
            created_at=now,
        ))
        connection.execute(update(cases).where(cases.c.id == other_case).values(
            revision=1, updated_at=now,
        ))
    foreign = client.post(f"/api/v1/cases/{other_case}/corrections",
                          headers={"Idempotency-Key": "foreign"},
                          json=body)
    assert foreign.status_code == 409
    assert foreign.json()["error"]["code"] == "source_run_not_current"
    with engine.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(corrections)) == 0


def test_legacy_and_tampered_run_facts_fail_closed(prepared):
    client, engine, case_id, run_id, set_id, fact_id, _ = prepared
    body = _request(case_id, run_id, set_id, fact_id)
    with engine.begin() as connection:
        row = connection.scalar(select(agent_runs.c.memory_json).where(
            agent_runs.c.id == run_id))
        connection.execute(update(agent_runs).where(agent_runs.c.id == run_id).values(
            memory_json=row | {"version": 1},
        ))
    legacy = client.get(f"/api/v1/agent-runs/{run_id}/facts")
    assert legacy.status_code == 200
    assert legacy.json()["available"] is False
    unavailable = client.post(f"/api/v1/cases/{case_id}/corrections",
                              headers={"Idempotency-Key": "legacy"}, json=body)
    assert unavailable.status_code == 409
    assert unavailable.json()["error"]["code"] == "facts_unavailable"
    row["fact_sets"][0]["facts"][0]["raw_value"] = "999"
    with engine.begin() as connection:
        connection.execute(update(agent_runs).where(agent_runs.c.id == run_id).values(
            memory_json=row,
        ))
    tampered = client.get(f"/api/v1/agent-runs/{run_id}/facts")
    assert tampered.status_code == 409
    assert tampered.json()["error"]["code"] == "memory_scope"
