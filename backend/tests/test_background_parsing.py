"""A durable parser claim lets uploads finish without parsing in the request."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select

from backend.app.api.main import create_app
from backend.app.db.ingestion import DocumentIngestion, IngestionError
from backend.app.db.models import document_versions, metadata
from backend.app.db.parse_repository import ParseRepository
from backend.app.db.parse_worker import process_once
from backend.app.parsing import parse_mineru_middle, verify_pdf_source_coverage

EXAMPLES = Path(__file__).resolve().parents[2] / "examples" / "dev-pack-001"


@pytest.fixture
def uploaded(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'parse.db'}")

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, record):
        connection.execute("PRAGMA foreign_keys=ON")

    metadata.create_all(engine)
    data_root = tmp_path / "data"
    monkeypatch.setenv("DOCUCHECK_DATA_DIR", str(data_root))
    with TestClient(create_app(engine)) as client:
        case_id = client.post("/api/v1/cases").json()["id"]
        yield client, engine, data_root, case_id
    engine.dispose()


def _upload(client, case_id, name, role, revision, key, supplier=None):
    data = {"role": role, "expected_revision": str(revision)}
    if supplier:
        data.update(supplier_handle=supplier, supplier_name=supplier)
    response = client.post(
        f"/api/v1/cases/{case_id}/documents",
        headers={"Idempotency-Key": key}, data=data,
        files={"file": (name, (EXAMPLES / name).read_bytes(),
                        "application/octet-stream")},
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_background_worker_parses_uploaded_xlsx_once(uploaded):
    client, engine, data_root, case_id = uploaded
    receipt = _upload(client, case_id, "vendor-a-quote.xlsx", "quote", 0,
                      "quote-1", "vendor-a")
    assert receipt["parse_status"] == "pending"
    results = process_once(engine, data_root, limit=1)
    assert len(results) == 1
    assert results[0]["parse_status"] == "complete"
    assert results[0]["evidence_count"] > 0
    assert process_once(engine, data_root, limit=1) == []
    listing = client.get(f"/api/v1/cases/{case_id}/documents").json()
    assert listing["documents"][0]["parse_status"] == "complete"


def test_expired_claim_is_reclaimed_and_old_worker_cannot_commit(uploaded):
    client, engine, data_root, case_id = uploaded
    receipt = _upload(client, case_id, "vendor-a-quote.xlsx", "quote", 0,
                      "quote-1", "vendor-a")
    repository = ParseRepository(engine)
    now = datetime.now(UTC).replace(tzinfo=None)
    first = repository.claim(now=now, lease_seconds=240)
    assert len(first) == 1
    assert repository.claim(now=now + timedelta(seconds=30)) == []
    second = repository.claim(now=now + timedelta(seconds=241))
    assert len(second) == 1
    assert first[0].claim_id != second[0].claim_id
    service = DocumentIngestion(engine, data_root)
    with pytest.raises(IngestionError, match="stale_parse_claim"):
        service.process_document(case_id, receipt["snapshot_id"],
                                 receipt["document_id"],
                                 claim_id=first[0].claim_id)
    parsed = service.process_document(case_id, receipt["snapshot_id"],
                                      receipt["document_id"],
                                      claim_id=second[0].claim_id)
    assert parsed["parse_status"] == "complete"


def test_repeated_worker_loss_becomes_visible_failure(uploaded):
    client, engine, _, case_id = uploaded
    receipt = _upload(client, case_id, "vendor-a-quote.xlsx", "quote", 0,
                      "quote-1", "vendor-a")
    repository = ParseRepository(engine)
    now = datetime.now(UTC).replace(tzinfo=None)
    assert len(repository.claim(now=now)) == 1
    assert len(repository.claim(now=now + timedelta(seconds=241))) == 1
    assert len(repository.claim(now=now + timedelta(seconds=482))) == 1
    assert repository.claim(now=now + timedelta(seconds=723)) == []
    with engine.connect() as connection:
        row = connection.execute(select(
            document_versions.c.parse_status,
            document_versions.c.coverage_json,
        ).where(document_versions.c.id == receipt["document_id"])).one()
    assert row.parse_status == "failed"
    assert row.coverage_json["coverage_issues"] == ["worker_lost"]


def test_latest_snapshot_only_and_pdf_uses_configured_parser(uploaded):
    client, engine, data_root, case_id = uploaded
    first = _upload(client, case_id, "requirements.pdf", "requirement", 0,
                    "requirement-1")
    second = _upload(client, case_id, "vendor-a-quote.xlsx", "quote", 1,
                     "quote-1", "vendor-a")

    def cached_pdf(source: Path, document_id: str):
        parsed = parse_mineru_middle(EXAMPLES / "parsed" / "requirements.middle.json",
                                     document_id=document_id)
        return verify_pdf_source_coverage(parsed, source)

    results = process_once(engine, data_root, limit=2, pdf_parser=cached_pdf)
    assert len(results) == 2
    assert {item["snapshot_id"] for item in results} == {second["snapshot_id"]}
    assert {item["parse_status"] for item in results} <= {"complete", "partial"}
    with engine.connect() as connection:
        old_status = connection.scalar(select(document_versions.c.parse_status).where(
            document_versions.c.id == first["document_id"]))
    assert old_status == "pending"


def test_lease_loss_during_pdf_parse_discards_late_result(uploaded):
    client, engine, data_root, case_id = uploaded
    receipt = _upload(client, case_id, "requirements.pdf", "requirement", 0,
                      "requirement-1")
    repository = ParseRepository(engine)
    now = datetime.now(UTC).replace(tzinfo=None)
    first = repository.claim(now=now, lease_seconds=240)[0]
    newer = []

    def reclaim_during_parse(source: Path, document_id: str):
        newer.extend(repository.claim(now=now + timedelta(seconds=241)))
        parsed = parse_mineru_middle(EXAMPLES / "parsed" / "requirements.middle.json",
                                     document_id=document_id)
        return verify_pdf_source_coverage(parsed, source)

    late = DocumentIngestion(engine, data_root,
                             pdf_parser=reclaim_during_parse).process_document(
        case_id, receipt["snapshot_id"], receipt["document_id"],
        claim_id=first.claim_id)
    assert len(newer) == 1
    assert late["parse_status"] == "processing"
    assert late["evidence_count"] == 0

    def cached_pdf(source: Path, document_id: str):
        parsed = parse_mineru_middle(EXAMPLES / "parsed" / "requirements.middle.json",
                                     document_id=document_id)
        return verify_pdf_source_coverage(parsed, source)

    current = DocumentIngestion(engine, data_root, pdf_parser=cached_pdf)
    completed = current.process_document(case_id, receipt["snapshot_id"],
                                         receipt["document_id"],
                                         claim_id=newer[0].claim_id)
    assert completed["parse_status"] in {"complete", "partial"}
    assert completed["evidence_count"] > 0


def test_pdf_waits_for_parser_configuration_without_losing_attempt(uploaded, monkeypatch):
    client, engine, data_root, case_id = uploaded
    receipt = _upload(client, case_id, "requirements.pdf", "requirement", 0,
                      "requirement-1")
    monkeypatch.delenv("DOCUCHECK_MINERU_EXECUTABLE", raising=False)
    results = process_once(engine, data_root, limit=1)
    assert results == []
    assert client.get(f"/api/v1/cases/{case_id}/documents").json()[
        "documents"][0]["parse_status"] == "pending"
    with engine.connect() as connection:
        assert connection.scalar(select(document_versions.c.parse_attempts).where(
            document_versions.c.id == receipt["document_id"])) == 0

    def cached_pdf(source: Path, document_id: str):
        parsed = parse_mineru_middle(EXAMPLES / "parsed" / "requirements.middle.json",
                                     document_id=document_id)
        return verify_pdf_source_coverage(parsed, source)

    parsed = process_once(engine, data_root, limit=1, pdf_parser=cached_pdf)
    assert parsed[0]["parse_status"] in {"complete", "partial"}
