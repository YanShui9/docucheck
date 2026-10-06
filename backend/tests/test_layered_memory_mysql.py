"""Opt-in real MySQL races and ten-turn recovery; no external model or embedding."""

import os
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, delete, select

from backend.app.api.auth import AuthenticationError, Principal
from backend.app.api.main import create_app
from backend.app.db.case_cleanup import CaseCleanup
from backend.app.db.case_deletion import CaseDeletion
from backend.app.db.connection import database_url
from backend.app.db.ingestion import DocumentIngestion
from backend.app.db.memory_repository import MemoryError, MemoryRepository
from backend.app.db.models import (
    cases,
    conversation_summaries,
    evidence_blocks,
    memory_profiles,
    memory_records,
    project_members,
    projects,
    task_memory,
)
from backend.app.db.run_executor import execute_run
from backend.tests.test_persisted_agent_run_mysql import EXAMPLES, SourceProvider

pytestmark = pytest.mark.skipif(
    os.getenv("DOCUCHECK_RUN_MYSQL_TESTS") != "1", reason="explicit MySQL opt-in"
)


def test_mysql_concurrent_first_memory_save_has_one_winner():
    engine = create_engine(
        database_url(), pool_size=4, pool_pre_ping=True, hide_parameters=True
    )
    actor = Principal("urn:memory-test", uuid4().hex).key
    gate = Barrier(2)

    def save():
        gate.wait(timeout=10)
        try:
            return MemoryRepository(engine).put(
                actor, "answer_length", "concise", 0, confirmed=True
            )["revision"]
        except MemoryError as exc:
            return exc.code

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: save(), range(2)))
        assert sorted(map(str, results)) == ["1", "memory_revision_conflict"]
        assert (
            MemoryRepository(engine).delete(actor, "answer_length", 1)["revision"] == 2
        )
    finally:
        with engine.begin() as conn:
            conn.execute(
                delete(memory_records).where(memory_records.c.principal_key == actor)
            )
            conn.execute(
                delete(memory_profiles).where(memory_profiles.c.principal_key == actor)
            )
        engine.dispose()


def test_mysql_ten_turns_reopen_summary_authorization_and_cleanup(
    tmp_path, monkeypatch
):
    engine = create_engine(database_url(), pool_pre_ping=True, hide_parameters=True)
    issuer = "urn:memory-acceptance:" + uuid4().hex
    actor = Principal(issuer, "tester")

    class Auth:
        enabled = True

        def authenticate(self, authorization):
            if authorization == "Bearer tester":
                return actor
            if authorization == "Bearer other":
                return Principal(issuer, "other")
            raise AuthenticationError()

    storage = tmp_path / "data"
    monkeypatch.setenv("DOCUCHECK_DATA_DIR", str(storage))
    headers = {"Authorization": "Bearer tester"}
    case_id = project_id = None
    try:
        with TestClient(
            create_app(engine, authenticator=Auth()), headers=headers
        ) as client:
            project_id = client.post(
                "/api/v1/projects", json={"name": "记忆验收"}
            ).json()["id"]
            case_id = client.post(
                "/api/v1/cases", json={"project_id": project_id}
            ).json()["id"]
            url = "/api/v1/me/memories"
            assert (
                client.put(
                    url + "/answer_length",
                    json={
                        "value": "concise",
                        "expected_revision": 0,
                        "confirmed": True,
                    },
                ).status_code
                == 200
            )
            assert (
                client.get(url, headers={"Authorization": "Bearer other"}).json()[
                    "records"
                ]
                == []
            )
            uploaded = client.post(
                f"/api/v1/cases/{case_id}/documents",
                headers={"Idempotency-Key": "memory-upload"},
                data={
                    "role": "quote",
                    "supplier_handle": "vendor-a",
                    "supplier_name": "供应商 A",
                    "expected_revision": "0",
                },
                files={
                    "file": (
                        "quote.xlsx",
                        (EXAMPLES / "vendor-a-quote.xlsx").read_bytes(),
                        "application/octet-stream",
                    )
                },
            )
            assert uploaded.status_code == 201, uploaded.text
            receipt = uploaded.json()
            DocumentIngestion(engine, storage).process_document(
                case_id, receipt["snapshot_id"], receipt["document_id"]
            )
            with engine.connect() as conn:
                evidence_id = conn.scalar(
                    select(evidence_blocks.c.id).where(
                        evidence_blocks.c.document_version_id == receipt["document_id"],
                        evidence_blocks.c.cell == "B6",
                    )
                )
            conv = client.post(f"/api/v1/cases/{case_id}/conversations").json()["id"]
            first_message_id = None
            for i in range(10):
                submitted = client.post(
                    f"/api/v1/conversations/{conv}/messages",
                    headers={"Idempotency-Key": f"memory-question-{i}"},
                    json={
                        "content": f"第{i + 1}轮，请读取供应商 A 的内存报价原文",
                        "expected_revision": 1,
                    },
                )
                assert submitted.status_code == 202, submitted.text
                run = submitted.json()
                first_message_id = first_message_id or run["message_id"]
                result = execute_run(
                    engine,
                    run["run_id"],
                    run["attempt_id"],
                    SourceProvider(evidence_id),
                )
                assert result.status == "completed", result.error_code
            memory = client.get(f"/api/v1/conversations/{conv}/memory").json()
            assert len(memory["summary"]["entries"]) == 2
            assert (
                client.get(
                    f"/api/v1/conversations/{conv}/memory",
                    headers={"Authorization": "Bearer other"},
                ).status_code
                == 404
            )
            reopened = create_engine(
                database_url(), pool_pre_ping=True, hide_parameters=True
            )
            try:
                service = MemoryRepository(reopened)
                assert service.profile(actor.key)["revision"] == 1
                assert (
                    service.conversation(case_id, conv, receipt["snapshot_id"])[
                        "summary"
                    ]
                    == memory["summary"]
                )
                assert (
                    "第1轮"
                    in service.read_history(
                        case_id,
                        conv,
                        receipt["snapshot_id"],
                        [first_message_id],
                        part="question",
                    )[0]["text"]
                )
            finally:
                reopened.dispose()
            CaseDeletion(engine).request(case_id, 1)
            cleaner = CaseCleanup(engine, storage)
            claim = cleaner.claim(case_id=case_id)
            assert claim and cleaner.process(claim)["status"] == "deleted"
            with engine.connect() as conn:
                for table in (task_memory, conversation_summaries):
                    assert (
                        conn.execute(
                            select(table.c.id).where(table.c.case_id == case_id)
                        ).first()
                        is None
                    )
            assert (
                MemoryRepository(engine).profile(actor.key)["records"][0]["value"]
                == "concise"
            )
    finally:
        try:
            if case_id:
                state = CaseDeletion(engine).request(case_id, 1)
                if state["status"] != "deleted":
                    cleaner = CaseCleanup(engine, storage)
                    claim = cleaner.claim(case_id=case_id)
                    if claim:
                        cleaner.process(claim)
            with engine.begin() as conn:
                conn.execute(
                    delete(memory_records).where(
                        memory_records.c.principal_key == actor.key
                    )
                )
                conn.execute(
                    delete(memory_profiles).where(
                        memory_profiles.c.principal_key == actor.key
                    )
                )
                if case_id:
                    conn.execute(delete(cases).where(cases.c.id == case_id))
                if project_id:
                    conn.execute(
                        delete(project_members).where(
                            project_members.c.project_id == project_id
                        )
                    )
                    conn.execute(delete(projects).where(projects.c.id == project_id))
        finally:
            engine.dispose()
