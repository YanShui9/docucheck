"""Opt-in real MySQL acceptance for uploaded XLSX evidence persistence."""

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, delete, func, select

from backend.app.api.main import create_app
from backend.app.db.connection import database_url
from backend.app.db.ingestion import DocumentIngestion
from backend.app.db.models import (
    cases,
    document_versions,
    evidence_blocks,
    idempotency_records,
    input_snapshots,
    suppliers,
)
from backend.app.db.parse_worker import process_once

EXAMPLES = Path(__file__).resolve().parents[2] / "examples" / "dev-pack-001"


def test_mysql_uploaded_xlsx_becomes_scoped_evidence(tmp_path, monkeypatch):
    if os.getenv("DOCUCHECK_RUN_MYSQL_TESTS") != "1":
        pytest.skip("set DOCUCHECK_RUN_MYSQL_TESTS=1 for live MySQL ingestion check")
    storage = tmp_path / "data"
    monkeypatch.setenv("DOCUCHECK_DATA_DIR", str(storage))
    engine = create_engine(database_url(), pool_pre_ping=True, hide_parameters=True)
    case_id = None
    try:
        with TestClient(create_app(engine)) as client:
            created = client.post("/api/v1/cases")
            assert created.status_code == 201
            case_id = created.json()["id"]
            uploaded = client.post(
                f"/api/v1/cases/{case_id}/documents",
                headers={"Idempotency-Key": "mysql-ingestion-1"},
                data={"role": "quote", "supplier_handle": "vendor-a",
                      "supplier_name": "vendor-a", "expected_revision": "0"},
                files={"file": ("vendor-a-quote.xlsx",
                                (EXAMPLES / "vendor-a-quote.xlsx").read_bytes(),
                                "application/octet-stream")},
            )
            assert uploaded.status_code == 201, uploaded.text
            receipt = uploaded.json()
            service = DocumentIngestion(engine, storage)
            first = process_once(engine, storage, limit=1)[0]
            assert first["parse_status"] == "complete"
            assert first["evidence_count"] > 0
            assert process_once(engine, storage, limit=1) == []
            assert service.process_document(case_id, receipt["snapshot_id"],
                                            receipt["document_id"]) == first
            url = (f"/api/v1/cases/{case_id}/snapshots/{receipt['snapshot_id']}"
                   f"/documents/{receipt['document_id']}/evidence")
            response = client.get(url)
            assert response.status_code == 200
            assert any(row["cell"] == "B6" and "16 GB" in row["source_text"]
                       for row in response.json()["evidence"])
            assert client.get(url.replace(case_id, "another-case")).status_code == 404
            with engine.connect() as connection:
                assert connection.scalar(select(func.count()).select_from(
                    evidence_blocks).where(evidence_blocks.c.document_version_id ==
                                           receipt["document_id"])) == first["evidence_count"]
    finally:
        try:
            if case_id:
                with engine.begin() as connection:
                    ids = connection.scalars(select(document_versions.c.id).where(
                        document_versions.c.case_id == case_id)).all()
                    if ids:
                        connection.execute(delete(evidence_blocks).where(
                            evidence_blocks.c.document_version_id.in_(ids)))
                    connection.execute(delete(idempotency_records).where(
                        idempotency_records.c.operation_scope ==
                        f"case:{case_id}:upload"))
                    connection.execute(delete(document_versions).where(
                        document_versions.c.case_id == case_id))
                    connection.execute(delete(input_snapshots).where(
                        input_snapshots.c.case_id == case_id))
                    connection.execute(delete(suppliers).where(
                        suppliers.c.case_id == case_id))
                    connection.execute(delete(cases).where(cases.c.id == case_id))
        finally:
            engine.dispose()
