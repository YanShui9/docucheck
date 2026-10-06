"""Opt-in real MySQL empty-chat lifecycle; isolated outbox and no model API."""

import os
import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, delete, func, select, update

from backend.app.api.auth import LocalAuthenticator
from backend.app.api.main import create_app
from backend.app.db.case_cleanup import CaseCleanup
from backend.app.db.case_deletion import CaseDeletion
from backend.app.db.connection import database_url
from backend.app.db.models import assistant_runs, cases, input_snapshots, outbox
from backend.app.db.run_executor import execute_run
from backend.app.db.run_repository import RunRepository
from backend.tests.test_assistant_agent import Decisions

pytestmark = pytest.mark.skipif(
    os.getenv("DOCUCHECK_RUN_MYSQL_TESTS") != "1", reason="explicit MySQL opt-in"
)


def test_mysql_empty_chat_reopen_retry_and_candidate_confirmation(tmp_path, monkeypatch):
    engine = create_engine(database_url(), pool_pre_ping=True, hide_parameters=True)
    case_id = None
    dispatch = RunRepository._dispatch

    def isolated_dispatch(self, connection, run_id, attempt_id, timestamp):
        dispatch(self, connection, run_id, attempt_id, timestamp)
        connection.execute(update(outbox).where(outbox.c.run_id == run_id).values(
            event_type="test.assistant." + case_id[:16]
        ))

    monkeypatch.setattr(RunRepository, "_dispatch", isolated_dispatch)
    monkeypatch.setenv("DOCUCHECK_DATA_DIR", str(tmp_path / "data"))
    try:
        with TestClient(create_app(engine, LocalAuthenticator())) as client:
            case_id = client.post("/api/v1/cases").json()["id"]
            conv = client.post(f"/api/v1/cases/{case_id}/conversations").json()["id"]
            messages = f"/api/v1/conversations/{conv}/messages"

            def submit(content, key):
                response = client.post(messages, headers={"Idempotency-Key": key}, json={
                    "content": content, "operation": "assistant", "expected_revision": 0
                })
                assert response.status_code == 202, response.text
                return response.json()

            first = submit("你好", "greet")
            report = execute_run(engine, first["run_id"], first["attempt_id"],
                Decisions(("chat", {"answer": "你好，请说明本次采购要求。"})))
            assert report.status == "completed", report
            assert submit("你好", "greet") == first
            url = f"/api/v1/agent-runs/{first['run_id']}"
            assert client.get(url + "/assistant-result").json()["result"]["kind"] == "chat"
            assert client.get(url + "/checks").json()["checks"] == []
            assert client.get(url + "/export").status_code == 409

            second = submit("采购苹果，数量至少20箱", "demand")
            bad = execute_run(engine, second["run_id"], second["attempt_id"],
                Decisions(("unknown_tool", {})))
            assert bad.status == "partial"
            url = f"/api/v1/agent-runs/{second['run_id']}"
            retry = client.post(url + "/retry", headers={"Idempotency-Key": "retry-demand"},
                json={"expected_revision": 0})
            assert retry.status_code == 202, retry.text
            patch = {"items": [{"item_id": "apple", "name": "苹果"}], "operations": [{
                "op": "add", "reason": "买方要求", "criterion": {"item_id": "apple",
                "name": "数量", "rule": {"type": "numeric", "operator": "gte",
                "value": "20", "unit": "箱"}}}]}
            provider = Decisions(("propose_changes", patch))
            reopened = create_engine(database_url(), pool_pre_ping=True, hide_parameters=True)
            try:
                report = execute_run(reopened, second["run_id"], retry.json()["attempt_id"], provider)
                assert report.status == "completed", report
                assert any(m.type == "ai" and "你好" in str(m.content)
                           for m in provider.messages[0])
            finally:
                reopened.dispose()
            preview = client.get(url + "/proposal")
            assert preview.status_code == 200, preview.text
            candidate = preview.json()["definition"]
            assert candidate["criteria"][0]["source"]["kind"] == "user_defined"
            snapshot = client.get(url).json()["snapshot_id"]
            checklist = f"/api/v1/cases/{case_id}/snapshots/{snapshot}/checklist"
            assert client.get(checklist).json()["confirmed_version"] is None
            saved = client.put(checklist, headers={"Idempotency-Key": "save"}, json={
                **candidate, "expected_revision": 0, "expected_checklist_revision": 0,
                "reason": "保存买方要求"})
            assert saved.status_code == 200, saved.text
            confirmed = client.post(checklist + "/confirm", headers={"Idempotency-Key": "confirm"},
                json={"expected_revision": 0, "expected_checklist_revision": 1,
                "criterion_ids": [candidate["criteria"][0]["criterion_id"]],
                "confirmed": True, "reason": "确认买方要求"})
            assert confirmed.status_code == 200, confirmed.text
            assert client.get(url + "/proposal").json()["is_current"] is False
            with engine.connect() as connection:
                assert connection.scalar(select(func.count()).select_from(input_snapshots)
                    .where(input_snapshots.c.case_id == case_id)) == 1
                assert connection.scalar(select(func.count()).select_from(assistant_runs)
                    .where(assistant_runs.c.case_id == case_id)) == 2
    finally:
        try:
            if case_id:
                CaseDeletion(engine).request(case_id, 0)
                cleaner = CaseCleanup(engine, tmp_path / "data")
                claim = cleaner.claim(case_id=case_id)
                if claim is not None:
                    assert cleaner.process(claim)["status"] == "deleted"
                # A running background cleaner may own this same Case first.
                # Wait only for our Case; never steal its lease or a global job.
                deadline = time.monotonic() + 30
                while CaseDeletion(engine).get(case_id)["status"] != "deleted":
                    assert time.monotonic() < deadline, "own_case_cleanup_timeout"
                    time.sleep(0.1)
                with engine.begin() as connection:
                    assert connection.scalar(select(func.count()).select_from(assistant_runs)
                        .where(assistant_runs.c.case_id == case_id)) == 0
                    connection.execute(delete(cases).where(cases.c.id == case_id))
        finally:
            engine.dispose()
