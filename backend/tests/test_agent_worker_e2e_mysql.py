"""Opt-in upload/parse/dispatch/Celery acceptance without an external model."""

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
from backend.app.db.models import (
    agent_runs,
    cases,
    document_versions,
    evidence_blocks,
    outbox,
    run_attempts,
)

ROOT = Path(__file__).resolve().parents[2]
SAMPLE = ROOT / "examples" / "dev-pack-001" / "vendor-a-quote.xlsx"
SMOKE_WORKER = ROOT / "backend" / "tests" / "support" / "mock_model_worker.py"
BROKER = "redis://redis:6379/15"


def _docker(*args: str, timeout: int = 40) -> str:
    completed = subprocess.run(
        ["docker", *args], cwd=ROOT, capture_output=True, text=True,
        timeout=timeout, check=False,
    )
    assert completed.returncode == 0, (
        f"docker {' '.join(args[:4])} failed: {completed.stderr[-1000:]}"
    )
    return completed.stdout.strip()


def _compose(*args: str, timeout: int = 40) -> str:
    return _docker("compose", "--profile", "agent", *args, timeout=timeout)


def _await(condition, *, timeout: float = 45):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = condition()
        if value:
            return value
        time.sleep(0.25)
    pytest.fail("isolated Agent worker did not reach the expected state")


@pytest.mark.skipif(os.getenv("DOCUCHECK_RUN_AGENT_WORKER_E2E") != "1",
                    reason="explicit Docker/MySQL/Redis/Celery opt-in")
def test_uploaded_xlsx_reaches_celery_and_returns_source_without_deepseek(
    monkeypatch,
):
    data_root = ROOT / "data"
    monkeypatch.setenv("DOCUCHECK_DATA_DIR", str(data_root))
    engine = create_engine(database_url(), pool_pre_ping=True,
                           hide_parameters=True)
    case_id = None
    isolated_containers = []
    dispatcher_stopped = False
    try:
        with engine.connect() as connection:
            assert connection.scalar(select(func.count()).select_from(
                agent_runs).where(agent_runs.c.status.in_((
                    "queued", "running", "waiting_user",
            )))) == 0
            assert connection.scalar(select(func.count()).select_from(
                outbox).where(outbox.c.status.in_(("pending", "claimed")))) == 0
            assert connection.scalar(select(func.count()).select_from(
                document_versions).where(document_versions.c.parse_status.in_((
                    "pending", "processing",
            )))) == 0
        assert _compose("ps", "--status", "running", "-q", "dispatcher")
        assert _compose("ps", "--status", "running", "-q", "cleaner")
        broker_keys = set(_compose(
            "exec", "-T", "redis", "redis-cli", "-n", "15", "KEYS", "*",
        ).splitlines())
        assert broker_keys <= {
            "_kombu.binding.runs", "_kombu.binding.reply.celery.pidbox",
        }, broker_keys

        dispatcher_stopped = True
        _compose("stop", "dispatcher")
        with TestClient(create_app(engine)) as client:
            created = client.post("/api/v1/cases")
            assert created.status_code == 201, created.text
            case_id = created.json()["id"]
            uploaded = client.post(
                f"/api/v1/cases/{case_id}/documents",
                headers={"Idempotency-Key": f"e2e-upload-{uuid4().hex}"},
                data={"role": "quote", "supplier_handle": "vendor-a",
                      "supplier_name": "Vendor A", "expected_revision": "0"},
                files={"file": (SAMPLE.name, SAMPLE.read_bytes(),
                                "application/octet-stream")},
            )
            assert uploaded.status_code == 201, uploaded.text
            document_id = uploaded.json()["document_id"]
            conversation = client.post(f"/api/v1/cases/{case_id}/conversations")
            assert conversation.status_code == 201, conversation.text
            submitted = client.post(
                f"/api/v1/conversations/{conversation.json()['id']}/messages",
                headers={"Idempotency-Key": f"e2e-question-{uuid4().hex}"},
                json={"content": "请列出供应商 A 报价中的内存原文",
                      "mode": "standard", "expected_revision": 1},
            )
            assert submitted.status_code == 202, submitted.text
            run_id = submitted.json()["run_id"]

            dispatch_id = _compose(
                "run", "-d", "--rm", "--no-deps", "--pull", "never",
                "-e", f"DOCUCHECK_CELERY_BROKER_URL={BROKER}", "dispatcher",
            )
            isolated_containers.append(dispatch_id)
            time.sleep(3)
            with engine.connect() as connection:
                assert connection.scalar(select(agent_runs.c.status).where(
                    agent_runs.c.id == run_id)) == "queued"
                assert connection.scalar(select(outbox.c.status).where(
                    outbox.c.run_id == run_id)) == "pending"

            parsed = subprocess.run(
                [sys.executable, "-m", "backend.app.parse_background", "--once",
                 "--data-dir", str(data_root)],
                cwd=ROOT, capture_output=True, text=True, timeout=45,
                check=False,
            )
            assert parsed.returncode == 0, parsed.stdout + parsed.stderr
            assert json.loads(parsed.stdout)["document_id"] == document_id
            with engine.connect() as connection:
                evidence_id = connection.scalar(select(evidence_blocks.c.id).where(
                    evidence_blocks.c.document_version_id == document_id,
                    evidence_blocks.c.cell == "B6",
                ))
            assert evidence_id is not None

            worker_id = _compose(
                "run", "-d", "--rm", "--no-deps", "--pull", "never",
                "-e", f"DOCUCHECK_CELERY_BROKER_URL={BROKER}",
                "-e", "DOCUCHECK_MODEL_PROVIDER=openai-compatible",
                "-e", "DOCUCHECK_MODEL_ID=docucheck-smoke-model",
                "-e", "DOCUCHECK_MODEL_BASE_URL=http://127.0.0.1:18742/v1",
                "-e", "DOCUCHECK_MODEL_API_KEY=smoke-only-key",
                "-e", "DOCUCHECK_MODEL_CONTEXT_WINDOW=32768",
                "-e", f"DOCUCHECK_SMOKE_EVIDENCE_ID={evidence_id}",
                "-v", f"{SMOKE_WORKER}:/opt/docucheck/backend/mock_model_worker.py:ro",
                "worker", "python", "/opt/docucheck/backend/mock_model_worker.py",
            )
            isolated_containers.append(worker_id)
            _await(lambda: "smoke_model_ready" in _docker("logs", worker_id))

            def completed_run():
                response = client.get(f"/api/v1/agent-runs/{run_id}")
                assert response.status_code == 200, response.text
                data = response.json()
                return data if data["status"] not in {"queued", "running"} else None

            result = _await(completed_run, timeout=50)
            assert result["status"] == "completed", result
            assert "B6" in result["answer_text"]
            assert "16 GB" in result["answer_text"]
            assert "vendor-a-quote.xlsx" in result["answer_text"]
            with engine.connect() as connection:
                attempt = connection.execute(select(
                    run_attempts.c.model_id, run_attempts.c.usage_json,
                ).where(run_attempts.c.run_id == run_id)).one()
            assert attempt.model_id == "docucheck-smoke-model"
            assert attempt.usage_json["model_calls"] == 3
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
                    _await(lambda: _case_deleted(engine, case_id), timeout=30)
                    with engine.begin() as connection:
                        connection.execute(delete(cases).where(cases.c.id == case_id))
        finally:
            for container_id in reversed(isolated_containers):
                try:
                    subprocess.run(["docker", "stop", container_id], cwd=ROOT,
                                   capture_output=True, text=True, timeout=20,
                                   check=False)
                except subprocess.TimeoutExpired:
                    subprocess.run(["docker", "kill", container_id], cwd=ROOT,
                                   capture_output=True, text=True, timeout=10,
                                   check=False)
            if dispatcher_stopped:
                _compose("start", "dispatcher")
            engine.dispose()


def _case_deleted(engine, case_id: str) -> bool:
    with engine.connect() as connection:
        return connection.scalar(select(cases.c.deletion_completed_at).where(
            cases.c.id == case_id)) is not None
