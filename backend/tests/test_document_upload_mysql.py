"""Opt-in upload acceptance against the migrated local MySQL database."""

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, delete, select

from backend.app.api.main import create_app
from backend.app.db.connection import database_url
from backend.app.db.models import (
    cases,
    document_versions,
    idempotency_records,
    input_snapshots,
    suppliers,
)

EXAMPLES = Path(__file__).resolve().parents[2] / "examples" / "dev-pack-001"


def test_mysql_upload_snapshot_and_replay(tmp_path, monkeypatch):
    if os.getenv("DOCUCHECK_RUN_MYSQL_TESTS") != "1":
        pytest.skip("set DOCUCHECK_RUN_MYSQL_TESTS=1 for live MySQL upload check")
    monkeypatch.setenv("DOCUCHECK_DATA_DIR", str(tmp_path / "data"))
    engine = create_engine(database_url(), pool_pre_ping=True, hide_parameters=True)
    case_id = None
    try:
        with TestClient(create_app(engine)) as client:
            case = client.post("/api/v1/cases")
            assert case.status_code == 201
            case_id = case.json()["id"]
            url = f"/api/v1/cases/{case_id}/documents"
            content = (EXAMPLES / "requirements.pdf").read_bytes()
            request = {
                "data": {"role": "requirement", "expected_revision": "0"},
                "files": {"file": ("requirements.pdf", content, "application/pdf")},
                "headers": {"Idempotency-Key": "mysql-upload-1"},
            }
            first = client.post(url, **request)
            assert first.status_code == 201, first.text
            replay = client.post(url, **request)
            assert replay.status_code == 201
            assert replay.json() == first.json()
            listing = client.get(url)
            assert listing.status_code == 200
            assert listing.json()["revision"] == 1
            assert len(listing.json()["documents"]) == 1
            with engine.connect() as connection:
                assert len(connection.execute(select(input_snapshots).where(
                    input_snapshots.c.case_id == case_id)).all()) == 1
                assert len(connection.execute(select(document_versions).where(
                    document_versions.c.case_id == case_id)).all()) == 1
    finally:
        try:
            if case_id:
                with engine.begin() as connection:
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
