"""Opt-in real MinerU upload-to-evidence acceptance on local MySQL."""

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, delete, func, select

from backend.app.api.main import create_app
from backend.app.db.case_deletion import CaseDeletion
from backend.app.db.connection import database_url
from backend.app.db.models import cases, document_versions, evidence_blocks

ROOT = Path(__file__).resolve().parents[2]
SAMPLE = ROOT / "examples" / "dev-pack-001" / "requirements.pdf"


def _await_deleted(engine, case_id: str) -> None:
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        with engine.connect() as connection:
            deleted = connection.scalar(select(cases.c.deletion_completed_at).where(
                cases.c.id == case_id))
        if deleted is not None:
            return
        time.sleep(0.25)
    pytest.fail("cleaner did not delete the MinerU smoke Case")


@pytest.mark.skipif(os.getenv("DOCUCHECK_RUN_MINERU_MYSQL") != "1",
                    reason="explicit real MinerU/MySQL/cleaner opt-in")
def test_uploaded_pdf_is_parsed_by_real_mineru_and_readable_by_api(monkeypatch):
    executable = Path(os.environ["DOCUCHECK_MINERU_EXECUTABLE"])
    assert executable.is_file()
    data_root = ROOT / "data"
    monkeypatch.setenv("DOCUCHECK_DATA_DIR", str(data_root))
    engine = create_engine(database_url(), pool_pre_ping=True,
                           hide_parameters=True)
    case_id = None
    try:
        with engine.connect() as connection:
            assert connection.scalar(select(func.count()).select_from(
                document_versions).where(document_versions.c.parse_status.in_((
                    "pending", "processing",
            )))) == 0
        with TestClient(create_app(engine)) as client:
            created = client.post("/api/v1/cases")
            assert created.status_code == 201, created.text
            case_id = created.json()["id"]
            uploaded = client.post(
                f"/api/v1/cases/{case_id}/documents",
                headers={"Idempotency-Key": f"mineru-upload-{uuid4().hex}"},
                data={"role": "requirement", "expected_revision": "0"},
                files={"file": (SAMPLE.name, SAMPLE.read_bytes(),
                                "application/pdf")},
            )
            assert uploaded.status_code == 201, uploaded.text
            receipt = uploaded.json()
            assert receipt["parse_status"] == "pending"
            started = time.monotonic()
            parsed = subprocess.run(
                [sys.executable, "-m", "backend.app.parse_background", "--once",
                 "--data-dir", str(data_root), "--mineru-executable",
                 str(executable)],
                cwd=ROOT, capture_output=True, text=True, timeout=210,
                check=False,
            )
            assert parsed.returncode == 0, parsed.stdout + parsed.stderr[-1200:]
            output = json.loads(parsed.stdout)
            assert output["document_id"] == receipt["document_id"]
            assert output["parse_status"] == "complete"
            assert output["coverage_issues"] == []
            evidence = client.get(
                f"/api/v1/cases/{case_id}/snapshots/{receipt['snapshot_id']}"
                f"/documents/{receipt['document_id']}/evidence",
            )
            assert evidence.status_code == 200, evidence.text
            blocks = evidence.json()["evidence"]
            assert any(row["page_idx"] == 0 and "16 GB" in row["source_text"]
                       for row in blocks)
            with engine.connect() as connection:
                assert connection.scalar(select(func.count()).select_from(
                    evidence_blocks).where(evidence_blocks.c.document_version_id ==
                                           receipt["document_id"])) == len(blocks)
            assert time.monotonic() - started < 210
    finally:
        try:
            if case_id is not None:
                with engine.connect() as connection:
                    row = connection.execute(select(
                        cases.c.revision, cases.c.deletion_requested_at,
                    ).where(cases.c.id == case_id)).first()
                if row is not None and row.deletion_requested_at is None:
                    CaseDeletion(engine).request(case_id, row.revision)
                if row is not None:
                    _await_deleted(engine, case_id)
                    with engine.begin() as connection:
                        connection.execute(delete(cases).where(cases.c.id == case_id))
        finally:
            engine.dispose()
