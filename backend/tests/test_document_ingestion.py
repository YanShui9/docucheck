"""Uploaded originals become snapshot-scoped, source-located database evidence."""

from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, func, select, update
from sqlalchemy.exc import IntegrityError

from backend.app.api.main import create_app
from backend.app.db.models import document_versions, evidence_blocks, metadata
from backend.app.parsing import parse_mineru_middle, verify_pdf_source_coverage

EXAMPLES = Path(__file__).resolve().parents[2] / "examples" / "dev-pack-001"


@pytest.fixture
def ingestion_case(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'ingestion.db'}")

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, record):
        connection.execute("PRAGMA foreign_keys=ON")

    metadata.create_all(engine)
    storage = tmp_path / "data"
    monkeypatch.setenv("DOCUCHECK_DATA_DIR", str(storage))
    with TestClient(create_app(engine)) as client:
        case_id = client.post("/api/v1/cases").json()["id"]
        yield client, engine, storage, case_id
    engine.dispose()


def _upload(client, case_id, name, role, revision, key, supplier=None):
    form = {"role": role, "expected_revision": str(revision)}
    if supplier:
        form.update(supplier_handle=supplier, supplier_name=supplier)
    response = client.post(
        f"/api/v1/cases/{case_id}/documents",
        headers={"Idempotency-Key": key}, data=form,
        files={"file": (name, (EXAMPLES / name).read_bytes(),
                        "application/octet-stream")},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _evidence_url(case_id, receipt):
    return (f"/api/v1/cases/{case_id}/snapshots/{receipt['snapshot_id']}"
            f"/documents/{receipt['document_id']}/evidence")


def test_ingest_uploaded_xlsx_is_idempotent_and_scoped(ingestion_case):
    from backend.app.db.ingestion import DocumentIngestion

    client, engine, storage, case_id = ingestion_case
    receipt = _upload(client, case_id, "vendor-a-quote.xlsx", "quote", 0,
                      "quote-1", "vendor-a")
    url = _evidence_url(case_id, receipt)
    waiting = client.get(url)
    assert waiting.status_code == 200
    assert waiting.json()["parse_status"] == "pending"
    assert waiting.json()["evidence"] == []

    service = DocumentIngestion(engine, storage)
    ingested = service.process_document(case_id, receipt["snapshot_id"],
                                        receipt["document_id"])
    assert ingested["parse_status"] == "complete"
    assert ingested["evidence_count"] > 0
    assert service.process_document(case_id, receipt["snapshot_id"],
                                    receipt["document_id"]) == ingested
    with engine.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(evidence_blocks)) == (
            ingested["evidence_count"]
        )
        document = connection.execute(select(document_versions).where(
            document_versions.c.id == receipt["document_id"])).mappings().one()
    assert len(document["parser_version"]) <= 64
    assert document["coverage_json"]["parser_version_full"].startswith("openpyxl:")
    listed = client.get(url)
    assert listed.status_code == 200
    assert listed.json()["parse_status"] == "complete"
    target = next(item for item in listed.json()["evidence"]
                  if item["cell"] == "B6")
    assert "16 GB" in target["source_text"]
    assert target["sheet"] == "报价"
    first_page = client.get(url, params={"limit": 1}).json()
    assert len(first_page["evidence"]) == 1
    next_page = client.get(url, params={"limit": 1,
                                        "after_ordinal": first_page["next_ordinal"]}).json()
    assert len(next_page["evidence"]) == 1
    assert next_page["evidence"][0]["id"] != first_page["evidence"][0]["id"]
    read_url = (f"/api/v1/cases/{case_id}/snapshots/{receipt['snapshot_id']}"
                f"/evidence/{target['id']}")
    assert client.get(read_url).json()["source_text"] == target["source_text"]
    assert client.get(read_url).json()["document_id"] == receipt["document_id"]
    assert client.get(read_url.replace(case_id, "wrong-case")).status_code == 404
    assert client.get(read_url.replace(receipt["snapshot_id"], "wrong-snapshot")).status_code == 404


def test_pdf_location_partial_coverage_and_new_snapshot_identity(ingestion_case,
                                                                 monkeypatch):
    from backend.app.db.ingestion import DocumentIngestion, IngestionError

    client, engine, storage, case_id = ingestion_case
    pdf = _upload(client, case_id, "requirements.pdf", "requirement", 0, "pdf-1")
    monkeypatch.delenv("DOCUCHECK_MINERU_EXECUTABLE", raising=False)
    with pytest.raises(IngestionError, match="parser_not_configured"):
        DocumentIngestion(engine, storage).process_document(
            case_id, pdf["snapshot_id"], pdf["document_id"])
    assert client.get(_evidence_url(case_id, pdf)).json()["parse_status"] == "pending"

    def cached_pdf(source: Path, document_id: str):
        parsed = parse_mineru_middle(EXAMPLES / "parsed" / "requirements.middle.json",
                                     document_id=document_id)
        verified = verify_pdf_source_coverage(parsed, source)
        return replace(verified, status="partial",
                       coverage_issues=(*verified.coverage_issues, "controlled_partial"))

    service = DocumentIngestion(engine, storage, pdf_parser=cached_pdf)
    parsed = service.process_document(case_id, pdf["snapshot_id"], pdf["document_id"])
    assert parsed["parse_status"] == "partial"
    evidence = client.get(_evidence_url(case_id, pdf)).json()
    assert "controlled_partial" in evidence["coverage_issues"]
    first = next(item for item in evidence["evidence"] if item["page_idx"] == 0)
    assert first["page_width_pt"] > 0
    assert first["page_height_pt"] > 0
    assert len(first["bbox"]) == 4

    quote = _upload(client, case_id, "vendor-a-quote.xlsx", "quote", 1,
                    "quote-2", "vendor-a")
    new_docs = client.get(f"/api/v1/cases/{case_id}/documents").json()["documents"]
    copied_pdf = next(doc for doc in new_docs if doc["role"] == "requirement")
    assert copied_pdf["id"] != pdf["document_id"]
    assert copied_pdf["parse_status"] == "pending"
    service.process_document(case_id, quote["snapshot_id"], copied_pdf["id"])
    copied = client.get(_evidence_url(case_id, {
        "snapshot_id": quote["snapshot_id"], "document_id": copied_pdf["id"],
    })).json()
    assert copied["evidence"]
    assert {row["id"] for row in copied["evidence"]}.isdisjoint(
        {row["id"] for row in evidence["evidence"]}
    )


def test_tampered_original_fails_without_evidence(ingestion_case):
    from backend.app.db.ingestion import DocumentIngestion

    client, engine, storage, case_id = ingestion_case
    receipt = _upload(client, case_id, "vendor-a-quote.xlsx", "quote", 0,
                      "quote-1", "vendor-a")
    with engine.connect() as connection:
        key = connection.scalar(select(document_versions.c.storage_key).where(
            document_versions.c.id == receipt["document_id"]))
    (storage / key).write_bytes(b"tampered")
    service = DocumentIngestion(engine, storage)
    result = service.process_document(case_id, receipt["snapshot_id"],
                                      receipt["document_id"])
    assert result["parse_status"] == "failed"
    assert result["coverage_issues"] == ["source_hash_mismatch"]
    assert client.get(_evidence_url(case_id, receipt)).json()["evidence"] == []


def test_forged_storage_path_is_rejected(ingestion_case):
    from backend.app.db.ingestion import DocumentIngestion

    client, engine, storage, case_id = ingestion_case
    receipt = _upload(client, case_id, "vendor-a-quote.xlsx", "quote", 0,
                      "quote-1", "vendor-a")
    with engine.begin() as connection:
        connection.execute(update(document_versions).where(
            document_versions.c.id == receipt["document_id"]
        ).values(storage_key="../outside.xlsx"))
    result = DocumentIngestion(engine, storage).process_document(
        case_id, receipt["snapshot_id"], receipt["document_id"])
    assert result["parse_status"] == "failed"
    assert result["coverage_issues"] == ["source_path_invalid"]


def test_evidence_write_failure_rolls_back_status_and_rows(ingestion_case,
                                                           monkeypatch):
    from backend.app.db import ingestion

    client, engine, storage, case_id = ingestion_case
    receipt = _upload(client, case_id, "vendor-a-quote.xlsx", "quote", 0,
                      "quote-1", "vendor-a")
    monkeypatch.setattr(ingestion, "_digest", lambda value: "same-evidence-id")
    with pytest.raises(IntegrityError):
        ingestion.DocumentIngestion(engine, storage).process_document(
            case_id, receipt["snapshot_id"], receipt["document_id"])
    with engine.connect() as connection:
        status = connection.scalar(select(document_versions.c.parse_status).where(
            document_versions.c.id == receipt["document_id"]))
        count = connection.scalar(select(func.count()).select_from(evidence_blocks))
    assert status == "pending"
    assert count == 0


def test_source_changed_during_pdf_parse_cannot_commit_evidence(ingestion_case):
    from backend.app.db.ingestion import DocumentIngestion

    client, engine, storage, case_id = ingestion_case
    receipt = _upload(client, case_id, "requirements.pdf", "requirement", 0,
                      "pdf-1")

    def mutate_source(path: Path, document_id: str):
        parsed = parse_mineru_middle(EXAMPLES / "parsed" / "requirements.middle.json",
                                     document_id=document_id)
        path.write_bytes(b"changed-while-parsing")
        return parsed

    result = DocumentIngestion(engine, storage, pdf_parser=mutate_source).process_document(
        case_id, receipt["snapshot_id"], receipt["document_id"])
    assert result["parse_status"] == "failed"
    assert result["coverage_issues"] == ["source_hash_mismatch"]
    assert client.get(_evidence_url(case_id, receipt)).json()["evidence"] == []
