"""Opt-in reviewed-correction Run through isolated Linux Celery worker."""

import os
import subprocess
from dataclasses import asdict
from io import BytesIO
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook
from sqlalchemy import create_engine, delete, func, select

from backend.app.agent.graph import AgentSession
from backend.app.agent.session_memory import serialize_session
from backend.app.api.main import create_app
from backend.app.db.agent_snapshot import load_agent_snapshot
from backend.app.db.case_deletion import CaseDeletion
from backend.app.db.connection import database_url
from backend.app.db.models import (
    agent_runs,
    cases,
    correction_reviews,
    document_versions,
    outbox,
    run_attempts,
)
from backend.app.db.parse_worker import process_once
from backend.app.db.run_repository import RunRepository
from backend.app.evidence.repository import digest
from backend.app.extraction.fields import FieldCandidate, _validate_candidate
from backend.app.field_contract import FieldFactSet, FieldKey
from backend.app.runtime.lifecycle import RunStatus
from backend.tests.test_agent_worker_e2e_mysql import (
    BROKER,
    ROOT,
    SMOKE_WORKER,
    _await,
    _case_deleted,
    _compose,
    _docker,
)


def _workbook() -> bytes:
    workbook = Workbook()
    workbook.active.title = "报价"
    workbook.active["B6"] = "标称 SSD 512 GB；标称 SSD 1 TB"
    stream = BytesIO()
    workbook.save(stream)
    return stream.getvalue()


def _stored_fact_set(engine, case_id: str, snapshot_id: str):
    repo = load_agent_snapshot(engine, case_id, snapshot_id)
    block = next(block for block in repo.blocks if block.cell == "B6")
    fact = _validate_candidate(FieldCandidate(
        field_key=FieldKey.STORAGE, raw_value="512", raw_unit="GB",
        evidence_refs=[block.evidence_id],
    ), repo, "vendor-a")
    scanned = (block.evidence_id,)
    fact_set_id = digest([
        snapshot_id, "vendor-a", "storage", scanned, False,
        [asdict(fact) | {"value": str(fact.value)}],
    ])
    session = AgentSession(repo, object())
    session.tools.fact_sets[fact_set_id] = FieldFactSet(
        fact_set_id=fact_set_id, snapshot_id=snapshot_id,
        supplier="vendor-a", field_key=FieldKey.STORAGE,
        facts=(fact,), scanned_ids=scanned, complete=False,
        coverage_issues=("test_partial",),
    )
    return fact_set_id, fact.fact_id, serialize_session(session, "原始抽取记录")


@pytest.mark.skipif(os.getenv("DOCUCHECK_RUN_CORRECTED_WORKER_E2E") != "1",
                    reason="explicit Docker/MySQL/Redis/Celery opt-in")
def test_reviewed_correction_is_used_by_celery_without_reextraction(monkeypatch):
    data_root = ROOT / "data"
    monkeypatch.setenv("DOCUCHECK_DATA_DIR", str(data_root))
    engine = create_engine(database_url(), pool_pre_ping=True,
                           hide_parameters=True)
    case_id = None
    containers = []
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
        dispatcher_stopped = True
        _compose("stop", "dispatcher")
        with TestClient(create_app(engine)) as client:
            created = client.post("/api/v1/cases")
            assert created.status_code == 201, created.text
            case_id = created.json()["id"]
            uploaded = client.post(
                f"/api/v1/cases/{case_id}/documents",
                headers={"Idempotency-Key": f"review-upload-{uuid4().hex}"},
                data={"role": "quote", "supplier_handle": "vendor-a",
                      "supplier_name": "Vendor A", "expected_revision": "0"},
                files={"file": ("review.xlsx", _workbook(),
                                "application/octet-stream")},
            )
            assert uploaded.status_code == 201, uploaded.text
            upload = uploaded.json()
            parsed = process_once(engine, data_root, limit=1)
            assert len(parsed) == 1 and parsed[0]["parse_status"] == "complete"
            fact_set_id, fact_id, memory = _stored_fact_set(
                engine, case_id, upload["snapshot_id"])
            conversation = client.post(f"/api/v1/cases/{case_id}/conversations")
            assert conversation.status_code == 201, conversation.text
            conversation_id = conversation.json()["id"]
            source = client.post(
                f"/api/v1/conversations/{conversation_id}/messages",
                headers={"Idempotency-Key": f"review-source-{uuid4().hex}"},
                json={"content": "审查存储", "expected_revision": 1},
            )
            assert source.status_code == 202, source.text
            source_run = source.json()
            repository = RunRepository(engine)
            claim = repository.claim(source_run["run_id"], source_run["attempt_id"])
            repository.finish(
                source_run["run_id"], source_run["attempt_id"], claim.fence,
                claim.snapshot_id, RunStatus.COMPLETED,
                answer_text="原始抽取记录", memory_json=memory,
            )
            correction = client.post(
                f"/api/v1/cases/{case_id}/corrections",
                headers={"Idempotency-Key": f"review-correction-{uuid4().hex}"},
                json={
                    "source_run_id": source_run["run_id"],
                    "fact_set_id": fact_set_id, "fact_id": fact_id,
                    "raw_value": "1", "raw_unit": "TB",
                    "reason": "同一原文另有 1 TB", "expected_revision": 1,
                },
            )
            assert correction.status_code == 201, correction.text
            receipt = correction.json()
            reviewed = client.post(
                f"/api/v1/cases/{case_id}/corrections/"
                f"{receipt['correction_id']}/recheck",
            )
            assert reviewed.status_code == 200, reviewed.text
            review = reviewed.json()
            assert review["review_status"] == "needs_review"
            old_facts = client.get(
                f"/api/v1/agent-runs/{source_run['run_id']}/facts")
            assert old_facts.status_code == 200 and not old_facts.json()["is_current"]
            submitted = client.post(
                f"/api/v1/conversations/{conversation_id}/messages",
                headers={"Idempotency-Key": f"review-question-{uuid4().hex}"},
                json={"content": "供应商 A 的存储是否达标？",
                      "expected_revision": receipt["revision"]},
            )
            assert submitted.status_code == 202, submitted.text
            run_id = submitted.json()["run_id"]
            with engine.connect() as connection:
                fact_set = connection.scalar(select(
                    correction_reviews.c.fact_set_json,
                ).where(correction_reviews.c.correction_id ==
                        receipt["correction_id"]))
            worker_id = _compose(
                "run", "-d", "--rm", "--no-deps", "--pull", "never",
                "-e", f"DOCUCHECK_CELERY_BROKER_URL={BROKER}",
                "-e", "DOCUCHECK_MODEL_PROVIDER=openai-compatible",
                "-e", "DOCUCHECK_MODEL_ID=docucheck-smoke-model",
                "-e", "DOCUCHECK_MODEL_BASE_URL=http://127.0.0.1:18742/v1",
                "-e", "DOCUCHECK_MODEL_API_KEY=smoke-only-key",
                "-e", "DOCUCHECK_MODEL_CONTEXT_WINDOW=32768",
                "-e", "DOCUCHECK_SMOKE_SCENARIO=reviewed",
                "-e", f"DOCUCHECK_SMOKE_FACT_SET_ID={fact_set['fact_set_id']}",
                "-e", ("DOCUCHECK_SMOKE_CHECK_RESULT_ID="
                       f"{review['check_result']['check_result_id']}"),
                "-v", f"{SMOKE_WORKER}:/opt/docucheck/backend/mock_model_worker.py:ro",
                "worker", "python", "/opt/docucheck/backend/mock_model_worker.py",
            )
            containers.append(worker_id)
            _await(lambda: "smoke_model_ready" in _docker("logs", worker_id))
            dispatch_id = _compose(
                "run", "-d", "--rm", "--no-deps", "--pull", "never",
                "-e", f"DOCUCHECK_CELERY_BROKER_URL={BROKER}", "dispatcher",
            )
            containers.append(dispatch_id)

            def finished():
                response = client.get(f"/api/v1/agent-runs/{run_id}")
                assert response.status_code == 200, response.text
                result = response.json()
                return result if result["status"] not in {"queued", "running"} else None

            result = _await(finished, timeout=60)
            assert result["status"] == "partial", result
            assert result["error_code"] is None
            assert result["correction_id"] == receipt["correction_id"]
            assert result["correction_review_status"] == "needs_review"
            assert "人工复核" in result["answer_text"]
            with engine.connect() as connection:
                saved = connection.scalar(select(agent_runs.c.memory_json).where(
                    agent_runs.c.id == run_id))
                attempt = connection.execute(select(
                    run_attempts.c.model_id, run_attempts.c.usage_json,
                ).where(run_attempts.c.id ==
                        submitted.json()["attempt_id"])).one()
            assert attempt.model_id == "docucheck-smoke-model"
            assert attempt.usage_json["model_calls"] == 3
            assert {fact["value"] for facts in saved["fact_sets"]
                    for fact in facts["facts"]} == {"512", "1000"}
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
            for container_id in reversed(containers):
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
