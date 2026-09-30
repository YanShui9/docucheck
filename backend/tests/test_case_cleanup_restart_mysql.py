"""Opt-in recovery check for a queued deletion across cleaner stop/start."""

import os
import subprocess
import time
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, delete, func, select

from backend.app.api.main import create_app
from backend.app.db.case_deletion import CaseDeletion
from backend.app.db.connection import database_url
from backend.app.db.models import case_cleanup_files, cases, document_versions

ROOT = Path(__file__).resolve().parents[2]
SAMPLE = ROOT / "examples" / "dev-pack-001" / "requirements.pdf"


def _compose(*args: str) -> str:
    result = subprocess.run(
        ["docker", "compose", "--profile", "agent", *args],
        cwd=ROOT, check=False, capture_output=True, text=True, timeout=60,
    )
    if result.returncode != 0:
        raise AssertionError(f"docker compose {' '.join(args)} failed: "
                             f"{result.stderr.strip()[-500:]}")
    return result.stdout


def test_queued_delete_completes_after_cleaner_restart(monkeypatch):
    if os.getenv("DOCUCHECK_RUN_CLEANER_RESTART_TESTS") != "1":
        pytest.skip("set DOCUCHECK_RUN_CLEANER_RESTART_TESTS=1 for Docker restart")
    data_root = ROOT / "data"
    monkeypatch.setenv("DOCUCHECK_DATA_DIR", str(data_root))
    engine = create_engine(database_url(), pool_pre_ping=True,
                           hide_parameters=True)
    case_id = None
    cleaner_was_stopped = False
    try:
        with engine.connect() as connection:
            pending = connection.scalar(select(func.count()).select_from(cases)
                                        .where(cases.c.deletion_requested_at.is_not(None),
                                               cases.c.deletion_completed_at.is_(None)))
            ledger = connection.scalar(select(func.count()).select_from(
                case_cleanup_files))
        assert pending == 0 and ledger == 0, "cleanup queue must be empty"
        assert _compose("ps", "--status", "running", "--services", "cleaner").strip() == "cleaner"

        cleaner_was_stopped = True
        _compose("stop", "cleaner")
        with TestClient(create_app(engine)) as client:
            created = client.post("/api/v1/cases")
            assert created.status_code == 201, created.text
            case_id = created.json()["id"]
            uploaded = client.post(
                f"/api/v1/cases/{case_id}/documents",
                data={"role": "requirement", "expected_revision": "0"},
                files={"file": ("requirements.pdf", SAMPLE.read_bytes(),
                                "application/pdf")},
                headers={"Idempotency-Key": f"cleaner-restart-{uuid4().hex}"},
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
            time.sleep(2.5)
            waiting = client.get(f"/api/v1/cases/{case_id}/deletion")
            assert waiting.status_code == 200, waiting.text
            assert waiting.json()["status"] == "deleting"
            assert original.is_file()

            _compose("start", "cleaner")
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                result = client.get(f"/api/v1/cases/{case_id}/deletion")
                assert result.status_code == 200, result.text
                if result.json()["status"] == "deleted":
                    break
                assert result.json()["error_code"] is None, result.text
                time.sleep(0.2)
            else:
                pytest.fail(f"cleaner did not resume synthetic Case {case_id}")
            assert not original.exists()
            assert client.get(f"/api/v1/cases/{case_id}/documents").status_code == 410
            with engine.begin() as connection:
                assert connection.scalar(select(func.count()).select_from(
                    document_versions).where(
                        document_versions.c.case_id == case_id)) == 0
                assert connection.scalar(select(func.count()).select_from(
                    case_cleanup_files).where(
                        case_cleanup_files.c.case_id == case_id)) == 0
                removed = connection.execute(delete(cases).where(
                    cases.c.id == case_id,
                    cases.c.deletion_completed_at.is_not(None),
                ))
                assert removed.rowcount == 1
    finally:
        try:
            if case_id is not None:
                with engine.connect() as connection:
                    row = connection.execute(select(
                        cases.c.revision, cases.c.deletion_requested_at,
                    ).where(cases.c.id == case_id)).first()
                if row is not None and row.deletion_requested_at is None:
                    CaseDeletion(engine).request(case_id, row.revision)
        finally:
            try:
                if cleaner_was_stopped:
                    _compose("start", "cleaner")
            finally:
                engine.dispose()
