"""Opt-in API-to-running-cleaner acceptance against local MySQL."""

import os
import time
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, delete, func, select

from backend.app.api.main import create_app
from backend.app.db.case_deletion import CaseDeletion
from backend.app.db.connection import database_url
from backend.app.db.models import (
    case_cleanup_files,
    cases,
    document_versions,
    idempotency_records,
    input_snapshots,
)

ROOT = Path(__file__).resolve().parents[2]
SAMPLE = ROOT / "examples" / "dev-pack-001" / "requirements.pdf"


def _await_deleted(client: TestClient, case_id: str, timeout: float = 15) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = client.get(f"/api/v1/cases/{case_id}/deletion")
        assert response.status_code == 200, response.text
        receipt = response.json()
        if receipt["status"] == "deleted":
            return receipt
        assert receipt["error_code"] is None, receipt
        time.sleep(0.2)
    pytest.fail(f"running cleaner did not finish synthetic Case {case_id}")


def test_running_cleaner_removes_uploaded_case_after_api_delete(monkeypatch):
    if os.getenv("DOCUCHECK_RUN_CLEANER_SERVICE_TESTS") != "1":
        pytest.skip("set DOCUCHECK_RUN_CLEANER_SERVICE_TESTS=1 with cleaner running")
    data_root = ROOT / "data"
    monkeypatch.setenv("DOCUCHECK_DATA_DIR", str(data_root))
    engine = create_engine(database_url(), pool_pre_ping=True,
                           hide_parameters=True)
    case_id = None
    try:
        with TestClient(create_app(engine)) as client:
            created = client.post("/api/v1/cases")
            assert created.status_code == 201, created.text
            case_id = created.json()["id"]
            uploaded = client.post(
                f"/api/v1/cases/{case_id}/documents",
                data={"role": "requirement", "expected_revision": "0"},
                files={"file": ("requirements.pdf", SAMPLE.read_bytes(),
                                "application/pdf")},
                headers={"Idempotency-Key": f"cleaner-smoke-{uuid4().hex}"},
            )
            assert uploaded.status_code == 201, uploaded.text
            with engine.connect() as connection:
                key = connection.scalar(select(document_versions.c.storage_key)
                                        .where(document_versions.c.case_id == case_id))
            assert key is not None
            original = data_root / key
            assert original.is_file()

            requested = client.delete(
                f"/api/v1/cases/{case_id}",
                params={"expected_revision": 1},
            )
            assert requested.status_code == 202, requested.text
            assert requested.json()["status"] == "deleting"
            receipt = _await_deleted(client, case_id)
            assert receipt["completed_at"] is not None
            assert not original.exists()
            assert client.get(f"/api/v1/cases/{case_id}/documents").status_code == 410
            with engine.connect() as connection:
                for table in (document_versions, input_snapshots,
                              case_cleanup_files):
                    assert connection.scalar(select(func.count()).select_from(table)
                                             .where(table.c.case_id == case_id)) == 0
                assert connection.scalar(select(func.count()).select_from(
                    idempotency_records).where(
                        idempotency_records.c.operation_scope ==
                        f"case:{case_id}:upload")) == 0
    finally:
        try:
            if case_id is not None:
                with engine.connect() as connection:
                    row = connection.execute(select(
                        cases.c.revision, cases.c.deletion_requested_at,
                        cases.c.deletion_completed_at,
                    ).where(cases.c.id == case_id)).first()
                if row is not None and row.deletion_requested_at is None:
                    CaseDeletion(engine).request(case_id, row.revision)
                if row is not None:
                    with engine.begin() as connection:
                        connection.execute(delete(cases).where(
                            cases.c.id == case_id,
                            cases.c.deletion_completed_at.is_not(None),
                        ))
        finally:
            engine.dispose()
