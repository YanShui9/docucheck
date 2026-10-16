"""A submitted Run uses evidence from its bound uploaded snapshot."""

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from time import monotonic, sleep

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage
from sqlalchemy import create_engine, event, select, update

from backend.app.api.main import create_app
from backend.app.db.ingestion import DocumentIngestion
from backend.app.db.models import (
    agent_runs,
    document_versions,
    evidence_blocks,
    metadata,
    run_attempts,
)
from backend.app.db.run_repository import RepositoryError, RunRepository
from backend.app.parsing import parse_mineru_middle, verify_pdf_source_coverage
from backend.app.providers import ProviderError

EXAMPLES = Path(__file__).resolve().parents[2] / "examples" / "dev-pack-001"


@pytest.fixture
def uploaded_case(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'agent-run.db'}")

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, record):
        connection.execute("PRAGMA foreign_keys=ON")

    metadata.create_all(engine)
    storage = tmp_path / "data"
    monkeypatch.setenv("DOCUCHECK_DATA_DIR", str(storage))
    with TestClient(create_app(engine)) as client:
        case_id = client.post("/api/v1/cases").json()["id"]
        upload = client.post(
            f"/api/v1/cases/{case_id}/documents",
            headers={"Idempotency-Key": "quote-1"},
            data={"role": "quote", "supplier_handle": "vendor-a",
                  "supplier_name": "Vendor A", "expected_revision": "0"},
            files={"file": ("vendor-a-quote.xlsx",
                            (EXAMPLES / "vendor-a-quote.xlsx").read_bytes(),
                            "application/octet-stream")},
        )
        assert upload.status_code == 201, upload.text
        yield client, engine, storage, case_id, upload.json()
    engine.dispose()


def test_database_snapshot_keeps_persisted_evidence_identity_and_scope(uploaded_case):
    from backend.app.db.agent_snapshot import load_agent_snapshot

    _, engine, storage, case_id, uploaded = uploaded_case
    DocumentIngestion(engine, storage).process_document(
        case_id, uploaded["snapshot_id"], uploaded["document_id"])
    with engine.connect() as connection:
        row = connection.execute(select(evidence_blocks).where(
            evidence_blocks.c.document_version_id == uploaded["document_id"],
            evidence_blocks.c.cell == "B6",
        )).mappings().one()
    repo = load_agent_snapshot(engine, case_id, uploaded["snapshot_id"])
    assert repo.case_id == case_id
    assert repo.snapshot_id == uploaded["snapshot_id"]
    assert repo.revision == uploaded["revision"]
    source = repo.read([row["id"]])[0]
    assert source.evidence_id == row["id"]
    assert source.document_id == uploaded["document_id"]
    assert source.role == "quote" and source.supplier == "vendor-a"
    assert source.sheet == "报价" and source.cell == "B6"
    assert "16 GB" in source.text
    assert repo.coverage("vendor-a")["complete"] is False


def test_database_snapshot_rejects_unparsed_and_wrong_case(uploaded_case):
    from backend.app.db.agent_snapshot import SnapshotLoadError, load_agent_snapshot

    _, engine, storage, case_id, uploaded = uploaded_case
    with pytest.raises(SnapshotLoadError, match="snapshot_not_ready"):
        load_agent_snapshot(engine, case_id, uploaded["snapshot_id"])
    DocumentIngestion(engine, storage).process_document(
        case_id, uploaded["snapshot_id"], uploaded["document_id"])
    with pytest.raises(SnapshotLoadError, match="snapshot_not_found"):
        load_agent_snapshot(engine, "wrong-case", uploaded["snapshot_id"])


def test_database_snapshot_rejects_missing_evidence(uploaded_case):
    from backend.app.db.agent_snapshot import SnapshotLoadError, load_agent_snapshot

    _, engine, storage, case_id, uploaded = uploaded_case
    DocumentIngestion(engine, storage).process_document(
        case_id, uploaded["snapshot_id"], uploaded["document_id"])
    with engine.begin() as connection:
        connection.execute(evidence_blocks.delete().where(
            evidence_blocks.c.document_version_id == uploaded["document_id"]
        ))
    with pytest.raises(SnapshotLoadError, match="snapshot_not_ready"):
        load_agent_snapshot(engine, case_id, uploaded["snapshot_id"])


def test_database_snapshot_rejects_failed_or_versionless_document(uploaded_case):
    from backend.app.db.agent_snapshot import SnapshotLoadError, load_agent_snapshot

    _, engine, storage, case_id, uploaded = uploaded_case
    DocumentIngestion(engine, storage).process_document(
        case_id, uploaded["snapshot_id"], uploaded["document_id"])
    with engine.begin() as connection:
        coverage = connection.scalar(select(document_versions.c.coverage_json).where(
            document_versions.c.id == uploaded["document_id"],
        ))
        connection.execute(update(document_versions).where(
            document_versions.c.id == uploaded["document_id"],
        ).values(parse_status="failed"))
    with pytest.raises(SnapshotLoadError, match="snapshot_not_ready"):
        load_agent_snapshot(engine, case_id, uploaded["snapshot_id"])
    with engine.begin() as connection:
        connection.execute(update(document_versions).where(
            document_versions.c.id == uploaded["document_id"],
        ).values(parse_status="complete", coverage_json={
            key: value for key, value in coverage.items()
            if key != "parser_version_full"
        }))
    with pytest.raises(SnapshotLoadError, match="snapshot_not_ready"):
        load_agent_snapshot(engine, case_id, uploaded["snapshot_id"])


def test_database_snapshot_keeps_partial_coverage_and_bounds_memory(uploaded_case,
                                                                   monkeypatch):
    from backend.app.db import agent_snapshot

    _, engine, storage, case_id, uploaded = uploaded_case
    DocumentIngestion(engine, storage).process_document(
        case_id, uploaded["snapshot_id"], uploaded["document_id"])
    with engine.begin() as connection:
        row = connection.execute(select(document_versions.c.coverage_json).where(
            document_versions.c.id == uploaded["document_id"])).one()
        connection.execute(update(document_versions).where(
            document_versions.c.id == uploaded["document_id"],
        ).values(parse_status="partial", coverage_json={
            **row.coverage_json, "coverage_issues": ["hidden_sheet"],
        }))
    repo = agent_snapshot.load_agent_snapshot(engine, case_id,
                                              uploaded["snapshot_id"])
    assert repo.sources()[0]["status"] == "partial"
    assert repo.sources()[0]["coverage_issues"] == ("hidden_sheet",)
    monkeypatch.setattr(agent_snapshot, "MAX_AGENT_BLOCKS", 1)
    with pytest.raises(agent_snapshot.SnapshotLoadError,
                       match="snapshot_capacity"):
        agent_snapshot.load_agent_snapshot(engine, case_id,
                                           uploaded["snapshot_id"])


def test_new_snapshot_loads_pdf_page_location_without_old_evidence(uploaded_case):
    from backend.app.db.agent_snapshot import load_agent_snapshot

    client, engine, storage, case_id, uploaded = uploaded_case
    DocumentIngestion(engine, storage).process_document(
        case_id, uploaded["snapshot_id"], uploaded["document_id"])
    with engine.connect() as connection:
        old_id = connection.scalar(select(evidence_blocks.c.id).where(
            evidence_blocks.c.document_version_id == uploaded["document_id"],
            evidence_blocks.c.cell == "B6",
        ))
    response = client.post(
        f"/api/v1/cases/{case_id}/documents",
        headers={"Idempotency-Key": "requirement-1"},
        data={"role": "requirement", "expected_revision": "1"},
        files={"file": ("requirements.pdf", (EXAMPLES / "requirements.pdf").read_bytes(),
                        "application/octet-stream")},
    )
    assert response.status_code == 201, response.text
    current = response.json()

    def cached_pdf(path, document_id):
        parsed = parse_mineru_middle(EXAMPLES / "parsed" / "requirements.middle.json",
                                     document_id=document_id)
        return verify_pdf_source_coverage(parsed, path)

    DocumentIngestion(engine, storage, pdf_parser=cached_pdf).process_snapshot(
        case_id, current["snapshot_id"])
    repo = load_agent_snapshot(engine, case_id, current["snapshot_id"])
    assert repo.snapshot_id == current["snapshot_id"]
    pdf_source = next(block for block in repo.blocks if block.role == "requirement")
    assert pdf_source.page_idx == 0
    assert pdf_source.bbox is not None and len(pdf_source.bbox) == 4
    assert pdf_source.page_width_pt > 0 and pdf_source.page_height_pt > 0
    with pytest.raises(ValueError, match="evidence_scope"):
        repo.read([old_id])


def _submit_question(client, case_id, *, key="question-1"):
    conversation = client.post(f"/api/v1/cases/{case_id}/conversations")
    assert conversation.status_code == 201
    conversation_id = conversation.json()["id"]
    response = client.post(
        f"/api/v1/conversations/{conversation_id}/messages",
        headers={"Idempotency-Key": key},
        json={"content": "请列出供应商 A 报价中的内存原文", "mode": "standard",
              "expected_revision": 1},
    )
    assert response.status_code == 202, response.text
    return response.json(), conversation_id


def _call(name, args):
    return AIMessage(content="", tool_calls=[{"id": "test-call", "name": name,
                                               "args": args}],
                     usage_metadata={"input_tokens": 100, "output_tokens": 20,
                                     "total_tokens": 120})


class SourceProvider:
    model_id = "scripted-source-provider"
    context_window = 32_768

    def __init__(self, evidence_id):
        self.evidence_id = evidence_id
        self.calls = 0

    def decide(self, messages, tools, **kwargs):
        self.calls += 1
        if self.calls == 1:
            return _call("search_evidence", {"query": "已装内存", "supplier": "vendor-a"})
        if self.calls == 2:
            return _call("read_source", {"evidence_ids": [self.evidence_id]})
        return _call("submit_answer", {"kind": "evidence", "evidence_ids": [self.evidence_id]})


def test_submitted_run_uses_persisted_ids_and_finishes(uploaded_case):
    from backend.app.db.run_executor import execute_run

    client, engine, storage, case_id, uploaded = uploaded_case
    DocumentIngestion(engine, storage).process_document(
        case_id, uploaded["snapshot_id"], uploaded["document_id"])
    with engine.connect() as connection:
        evidence_id = connection.scalar(select(evidence_blocks.c.id).where(
            evidence_blocks.c.document_version_id == uploaded["document_id"],
            evidence_blocks.c.cell == "B6",
        ))
    submitted, _ = _submit_question(client, case_id)
    provider = SourceProvider(evidence_id)
    report = execute_run(engine, submitted["run_id"], submitted["attempt_id"], provider)
    assert report.status == "completed"
    assert report.run_id == submitted["run_id"]
    assert report.attempt_id == submitted["attempt_id"]
    assert report.model_calls == 3
    assert "B6" in report.answer_text and "16 GB" in report.answer_text
    assert "来源：vendor-a-quote.xlsx · 报价!B6" in report.answer_text
    persisted = client.get(f"/api/v1/agent-runs/{submitted['run_id']}").json()
    assert persisted["status"] == "completed"
    assert persisted["answer_text"] == report.answer_text
    assert persisted["snapshot_id"] == uploaded["snapshot_id"]
    with engine.connect() as connection:
        attempt = connection.execute(select(run_attempts).where(
            run_attempts.c.id == submitted["attempt_id"],
        )).mappings().one()
    assert attempt["model_id"] == provider.model_id
    assert attempt["usage_json"]["model_calls"] == 3
    with pytest.raises(RepositoryError, match="stale_attempt"):
        execute_run(engine, submitted["run_id"], submitted["attempt_id"], provider)
    assert provider.calls == 3


def test_clarification_resume_and_followup_restore_conversation(uploaded_case):
    from backend.app.db.run_executor import execute_run

    client, engine, storage, case_id, uploaded = uploaded_case
    DocumentIngestion(engine, storage).process_document(
        case_id, uploaded["snapshot_id"], uploaded["document_id"])
    with engine.connect() as connection:
        evidence_id = connection.scalar(select(evidence_blocks.c.id).where(
            evidence_blocks.c.document_version_id == uploaded["document_id"],
            evidence_blocks.c.cell == "B6"))
    submitted, conversation_id = _submit_question(client, case_id)

    class Clarify:
        model_id = "scripted-clarify"
        context_window = 32_768

        def decide(self, messages, tools, **kwargs):
            return _call("request_clarification", {"question": "请确认供应商。"})

    first = execute_run(engine, submitted["run_id"], submitted["attempt_id"], Clarify())
    assert first.status == "waiting_user"
    resumed = client.post(f"/api/v1/agent-runs/{submitted['run_id']}/resume",
                          headers={"Idempotency-Key": "answer-clarification"},
                          json={"content": "供应商 A", "expected_revision": 1})
    assert resumed.status_code == 202, resumed.text

    class ResumeProvider(SourceProvider):
        def decide(self, messages, tools, **kwargs):
            if self.calls == 0:
                contents = [str(item.content) for item in messages]
                assert "供应商 A" in contents[-1]
                assert any("请确认供应商。" in item for item in contents)
                assert any("请列出供应商 A 报价中的内存原文" in item
                           for item in contents)
            return super().decide(messages, tools, **kwargs)

    second = execute_run(engine, submitted["run_id"],
                         resumed.json()["attempt_id"], ResumeProvider(evidence_id))
    assert second.status == "completed" and "16 GB" in second.answer_text
    followup = client.post(
        f"/api/v1/conversations/{conversation_id}/messages",
        headers={"Idempotency-Key": "followup"},
        json={"content": "那条原文在哪里？", "mode": "standard",
              "expected_revision": 1})
    assert followup.status_code == 202, followup.text

    class FollowupProvider(SourceProvider):
        def decide(self, messages, tools, **kwargs):
            if self.calls == 0:
                contents = [str(item.content) for item in messages]
                assert any("供应商 A" in item for item in contents)
                assert any("16 GB" in item for item in contents)
            return super().decide(messages, tools, **kwargs)

    third = execute_run(engine, followup.json()["run_id"],
                        followup.json()["attempt_id"], FollowupProvider(evidence_id))
    assert third.status == "completed" and "B6" in third.answer_text


def test_second_clarification_keeps_first_user_reply(uploaded_case):
    from backend.app.db.run_executor import execute_run

    client, engine, storage, case_id, uploaded = uploaded_case
    DocumentIngestion(engine, storage).process_document(
        case_id, uploaded["snapshot_id"], uploaded["document_id"])
    submitted, _ = _submit_question(client, case_id)

    class Ask:
        model_id = "scripted-clarify"
        context_window = 32_768

        def __init__(self, prompt):
            self.prompt = prompt

        def decide(self, messages, tools, **kwargs):
            return _call("request_clarification", {"question": self.prompt})

    first = execute_run(engine, submitted["run_id"], submitted["attempt_id"],
                        Ask("请确认供应商。"))
    assert first.status == "waiting_user"
    reply_one = client.post(f"/api/v1/agent-runs/{submitted['run_id']}/resume",
                            headers={"Idempotency-Key": "reply-one"},
                            json={"content": "供应商 A", "expected_revision": 1}).json()
    second = execute_run(engine, submitted["run_id"], reply_one["attempt_id"],
                         Ask("请确认型号。"))
    assert second.status == "waiting_user"
    reply_two = client.post(f"/api/v1/agent-runs/{submitted['run_id']}/resume",
                            headers={"Idempotency-Key": "reply-two"},
                            json={"content": "型号 X", "expected_revision": 1}).json()
    with engine.connect() as connection:
        reply_one_id = connection.scalar(select(run_attempts.c.resume_message_id).where(
            run_attempts.c.id == reply_one["attempt_id"]))

    class Inspect:
        model_id = "scripted-inspect"
        context_window = 32_768

        def decide(self, messages, tools, **kwargs):
            contents = [str(item.content) for item in messages]
            historical = [json.loads(item.removeprefix("HISTORY_DATA=")) for item in contents
                          if item.startswith("HISTORY_DATA=")]
            assert {"message_id": reply_one_id, "question": "供应商 A"} in historical
            assert any("请确认供应商。" in item for item in contents)
            assert any("请确认型号。" in item for item in contents)
            assert contents[-1] == "型号 X"
            return _call("request_clarification", {"question": "请继续。"})

    report = execute_run(engine, submitted["run_id"], reply_two["attempt_id"],
                         Inspect())
    assert report.status == "waiting_user"


def test_changed_persisted_memory_stops_before_model_call(uploaded_case):
    from backend.app.db.run_executor import execute_run

    client, engine, storage, case_id, uploaded = uploaded_case
    DocumentIngestion(engine, storage).process_document(
        case_id, uploaded["snapshot_id"], uploaded["document_id"])
    with engine.connect() as connection:
        evidence_id = connection.scalar(select(evidence_blocks.c.id).where(
            evidence_blocks.c.document_version_id == uploaded["document_id"],
            evidence_blocks.c.cell == "B6"))
    first, conversation_id = _submit_question(client, case_id)
    assert execute_run(engine, first["run_id"], first["attempt_id"],
                       SourceProvider(evidence_id)).status == "completed"
    with engine.begin() as connection:
        memory = connection.scalar(select(agent_runs.c.memory_json).where(
            agent_runs.c.id == first["run_id"]))
        connection.execute(update(agent_runs).where(
            agent_runs.c.id == first["run_id"]).values(memory_json={
                **memory, "repository_fingerprint": "invalid",
            }))
    followup = client.post(
        f"/api/v1/conversations/{conversation_id}/messages",
        headers={"Idempotency-Key": "invalid-memory-followup"},
        json={"content": "它在哪里？", "mode": "standard",
              "expected_revision": 1}).json()

    class ForbiddenProvider:
        model_id = "forbidden"
        context_window = 32_768

        def decide(self, messages, tools, **kwargs):
            raise AssertionError("invalid memory reached model")

    report = execute_run(engine, followup["run_id"],
                         followup["attempt_id"], ForbiddenProvider())
    assert report.status == "partial"
    assert report.error_code == "memory_scope"
    assert report.model_calls == 0


def test_executor_heartbeats_during_blocked_model_call(uploaded_case):
    from backend.app.db.run_executor import execute_run

    client, engine, storage, case_id, uploaded = uploaded_case
    DocumentIngestion(engine, storage).process_document(
        case_id, uploaded["snapshot_id"], uploaded["document_id"])
    with engine.connect() as connection:
        evidence_id = connection.scalar(select(evidence_blocks.c.id).where(
            evidence_blocks.c.document_version_id == uploaded["document_id"],
            evidence_blocks.c.cell == "B6",
        ))
    submitted, _ = _submit_question(client, case_id)
    entered, release = Event(), Event()

    class BlockingProvider(SourceProvider):
        def decide(self, messages, tools, **kwargs):
            if self.calls == 0:
                entered.set()
                if not release.wait(5):
                    raise AssertionError("model-call test wait expired")
            return super().decide(messages, tools, **kwargs)

    with ThreadPoolExecutor(max_workers=1) as pool:
        task = pool.submit(execute_run, engine, submitted["run_id"],
                           submitted["attempt_id"], BlockingProvider(evidence_id),
                           heartbeat_interval_seconds=0.02)
        try:
            assert entered.wait(5)
            with engine.connect() as connection:
                claimed_at = connection.scalar(select(agent_runs.c.updated_at).where(
                    agent_runs.c.id == submitted["run_id"]))
            deadline = monotonic() + 1
            while monotonic() < deadline:
                with engine.connect() as connection:
                    updated = connection.scalar(select(agent_runs.c.updated_at).where(
                        agent_runs.c.id == submitted["run_id"]))
                if updated > claimed_at:
                    break
                sleep(0.01)
            else:
                pytest.fail("executor did not heartbeat while the model was blocked")
        finally:
            release.set()
        assert task.result(timeout=10).status == "completed"


def test_worker_execution_ignores_duplicate_without_second_model_call(uploaded_case):
    from backend.app.db.worker_runner import execute_queued_run

    client, engine, storage, case_id, uploaded = uploaded_case
    DocumentIngestion(engine, storage).process_document(
        case_id, uploaded["snapshot_id"], uploaded["document_id"])
    with engine.connect() as connection:
        evidence_id = connection.scalar(select(evidence_blocks.c.id).where(
            evidence_blocks.c.document_version_id == uploaded["document_id"],
            evidence_blocks.c.cell == "B6",
        ))
    submitted, _ = _submit_question(client, case_id)
    provider = SourceProvider(evidence_id)
    created = []

    def provider_factory():
        created.append(True)
        return provider

    first = execute_queued_run(engine, submitted["run_id"],
                               submitted["attempt_id"], provider_factory)
    second = execute_queued_run(engine, submitted["run_id"],
                                submitted["attempt_id"], provider_factory)
    assert first.status == "completed" and first.model_calls == 3
    assert second.status == "ignored" and second.error_code == "stale_attempt"
    assert created == [True] and provider.calls == 3


def test_worker_provider_configuration_error_is_persisted(uploaded_case):
    from backend.app.db.worker_runner import execute_queued_run

    client, engine, _, case_id, _ = uploaded_case
    submitted, _ = _submit_question(client, case_id)

    def unconfigured():
        raise ProviderError("configuration_error")

    report = execute_queued_run(engine, submitted["run_id"],
                                submitted["attempt_id"], unconfigured)
    assert report.status == "failed" and report.error_code == "configuration_error"
    persisted = client.get(f"/api/v1/agent-runs/{submitted['run_id']}").json()
    assert persisted["status"] == "failed"
    assert persisted["error_code"] == "configuration_error"


def test_worker_unexpected_provider_setup_error_does_not_leave_queued_run(uploaded_case):
    from backend.app.db.worker_runner import execute_queued_run

    client, engine, _, case_id, _ = uploaded_case
    submitted, _ = _submit_question(client, case_id)

    def broken_factory():
        raise RuntimeError("private setup detail")

    report = execute_queued_run(engine, submitted["run_id"],
                                submitted["attempt_id"], broken_factory)
    assert report.status == "failed"
    assert report.error_code == "worker_setup_failed"
    persisted = client.get(f"/api/v1/agent-runs/{submitted['run_id']}").json()
    assert persisted["status"] == "failed"
    assert "private" not in str(persisted)


def test_unparsed_snapshot_ends_partial_without_model_call(uploaded_case):
    from backend.app.db.run_executor import execute_run

    client, engine, _, case_id, _ = uploaded_case
    submitted, _ = _submit_question(client, case_id)
    provider = SourceProvider("unused")
    report = execute_run(engine, submitted["run_id"], submitted["attempt_id"], provider)
    assert report.status == "partial"
    assert report.error_code == "snapshot_not_ready"
    assert report.model_calls == 0 and provider.calls == 0
    assert client.get(f"/api/v1/agent-runs/{submitted['run_id']}").json()[
        "status"] == "partial"


def test_partial_run_can_retry_after_documents_are_parsed(uploaded_case):
    from backend.app.db.run_executor import execute_run

    client, engine, storage, case_id, uploaded = uploaded_case
    submitted, _ = _submit_question(client, case_id)
    first = execute_run(engine, submitted["run_id"], submitted["attempt_id"],
                        SourceProvider("unused"))
    assert first.status == "partial" and first.model_calls == 0
    DocumentIngestion(engine, storage).process_document(
        case_id, uploaded["snapshot_id"], uploaded["document_id"])
    with engine.connect() as connection:
        evidence_id = connection.scalar(select(evidence_blocks.c.id).where(
            evidence_blocks.c.document_version_id == uploaded["document_id"],
            evidence_blocks.c.cell == "B6",
        ))
    retry = client.post(
        f"/api/v1/agent-runs/{submitted['run_id']}/retry",
        headers={"Idempotency-Key": "retry-after-parse"},
        json={"expected_revision": 1},
    )
    assert retry.status_code == 202, retry.text
    second = execute_run(engine, submitted["run_id"],
                         retry.json()["attempt_id"], SourceProvider(evidence_id))
    assert second.status == "completed"
    assert second.attempt_id != first.attempt_id
    assert "B6" in second.answer_text


def test_changed_case_revision_rejects_run_before_model_call(uploaded_case):
    from backend.app.db.run_executor import execute_run

    client, engine, storage, case_id, uploaded = uploaded_case
    DocumentIngestion(engine, storage).process_document(
        case_id, uploaded["snapshot_id"], uploaded["document_id"])
    submitted, _ = _submit_question(client, case_id)
    newer = client.post(
        f"/api/v1/cases/{case_id}/documents",
        headers={"Idempotency-Key": "quote-2"},
        data={"role": "quote", "supplier_handle": "vendor-a",
              "supplier_name": "Vendor A", "expected_revision": "1"},
        files={"file": ("vendor-a-quote.xlsx",
                        (EXAMPLES / "vendor-a-quote.xlsx").read_bytes(),
                        "application/octet-stream")},
    )
    assert newer.status_code == 201, newer.text
    provider = SourceProvider("unused")
    with pytest.raises(RepositoryError, match="snapshot_changed"):
        execute_run(engine, submitted["run_id"], submitted["attempt_id"], provider)
    assert provider.calls == 0


def test_provider_crash_marks_claimed_run_interrupted(uploaded_case):
    from backend.app.db.run_executor import execute_run

    client, engine, storage, case_id, uploaded = uploaded_case
    DocumentIngestion(engine, storage).process_document(
        case_id, uploaded["snapshot_id"], uploaded["document_id"])
    submitted, _ = _submit_question(client, case_id)

    class Crashes:
        model_id = "crash"
        context_window = 32_768

        def decide(self, messages, tools, **kwargs):
            raise RuntimeError("scripted crash")

    with pytest.raises(RuntimeError, match="scripted crash"):
        execute_run(engine, submitted["run_id"], submitted["attempt_id"], Crashes())
    persisted = client.get(f"/api/v1/agent-runs/{submitted['run_id']}").json()
    assert persisted["status"] == "interrupted"
    assert persisted["answer_text"] is None


def test_keyboard_interrupt_marks_claimed_run_interrupted(uploaded_case):
    from backend.app.db.run_executor import execute_run

    client, engine, storage, case_id, uploaded = uploaded_case
    DocumentIngestion(engine, storage).process_document(
        case_id, uploaded["snapshot_id"], uploaded["document_id"])
    submitted, _ = _submit_question(client, case_id)

    class Interrupted:
        model_id = "interrupt"
        context_window = 32_768

        def decide(self, messages, tools, **kwargs):
            raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        execute_run(engine, submitted["run_id"], submitted["attempt_id"],
                    Interrupted())
    assert client.get(f"/api/v1/agent-runs/{submitted['run_id']}").json()[
        "status"] == "interrupted"


def test_model_timeout_records_unknown_outcome_without_formal_answer(uploaded_case):
    from backend.app.db.run_executor import execute_run

    client, engine, storage, case_id, uploaded = uploaded_case
    DocumentIngestion(engine, storage).process_document(
        case_id, uploaded["snapshot_id"], uploaded["document_id"])
    submitted, _ = _submit_question(client, case_id)

    class TimesOut:
        model_id = "timeout-model"
        context_window = 32_768

        def decide(self, messages, tools, **kwargs):
            raise ProviderError("timeout", outcome_unknown=True)

    report = execute_run(engine, submitted["run_id"], submitted["attempt_id"],
                         TimesOut())
    assert report.status == "failed" and report.error_code == "timeout"
    with engine.connect() as connection:
        attempt = connection.execute(select(run_attempts).where(
            run_attempts.c.id == submitted["attempt_id"],
        )).mappings().one()
    assert attempt["outcome_unknown"] is True
    assert attempt["model_id"] == "timeout-model"


def test_failed_extraction_persists_safe_diagnostic_events(uploaded_case):
    from backend.app.db.run_executor import execute_run

    client, engine, storage, case_id, uploaded = uploaded_case
    DocumentIngestion(engine, storage).process_document(
        case_id, uploaded["snapshot_id"], uploaded["document_id"])
    with engine.connect() as connection:
        evidence_id = connection.scalar(select(evidence_blocks.c.id).where(
            evidence_blocks.c.document_version_id == uploaded["document_id"],
            evidence_blocks.c.cell == "B6"))
    submitted, _ = _submit_question(client, case_id)

    class BadValue:
        model_id = "scripted-bad-value"
        context_window = 32_768

        def decide(self, messages, tools, **kwargs):
            return _call("extract_facts", {"supplier": "vendor-a", "field_keys": ["memory"]})

        def extract(self, messages, schema, **kwargs):
            reply = _call("MemoryCandidates", {"facts": [{
                "raw_value": "999", "raw_unit": "GB", "qualifier": "installed",
                "comparator": "eq", "evidence_refs": [evidence_id],
            }]})
            reply.response_metadata["request_id"] = "SECRET-do-not-store"
            return reply

    report = execute_run(engine, submitted["run_id"], submitted["attempt_id"], BadValue())
    assert report.error_code == "invalid_tool_result"
    events = client.get(f"/api/v1/agent-runs/{report.run_id}/events").json()["events"]
    trace = [event for event in events if event["kind"].startswith("agent_")]
    assert len(trace) == 3
    failed_tool = next(event for event in trace if event["kind"] == "agent_tool")
    assert failed_tool["summary"]["failure_detail"] == "fact_value"
    assert failed_tool["summary"]["tool"] == "extract_facts"
    assert [event["summary"]["sequence"] for event in trace] == [1, 2, 3]
    assert {event["attempt_id"] for event in trace} == {submitted["attempt_id"]}
    assert "SECRET" not in json.dumps(trace)
    assert "evidence_refs" not in json.dumps(trace)
    assert events[-1]["kind"] == "finished"


def test_cancelled_run_stops_before_another_model_call(uploaded_case):
    from backend.app.db.run_executor import execute_run

    client, engine, storage, case_id, uploaded = uploaded_case
    DocumentIngestion(engine, storage).process_document(
        case_id, uploaded["snapshot_id"], uploaded["document_id"])
    submitted, _ = _submit_question(client, case_id)
    with engine.connect() as connection:
        evidence_id = connection.scalar(select(evidence_blocks.c.id).where(
            evidence_blocks.c.document_version_id == uploaded["document_id"],
            evidence_blocks.c.cell == "B6",
        ))

    class Cancels(SourceProvider):
        def decide(self, messages, tools, **kwargs):
            if self.calls == 1:
                RunRepository(engine).cancel(submitted["run_id"])
            return super().decide(messages, tools, **kwargs)

    provider = Cancels(evidence_id)
    report = execute_run(engine, submitted["run_id"], submitted["attempt_id"],
                         provider)
    assert report.status == "cancelled"
    assert provider.calls == 2
    persisted = client.get(f"/api/v1/agent-runs/{submitted['run_id']}").json()
    assert persisted["status"] == "cancelled"
    assert persisted["answer_text"] is None


def test_message_over_agent_limit_is_rejected_before_queueing(uploaded_case):
    client, engine, storage, case_id, uploaded = uploaded_case
    DocumentIngestion(engine, storage).process_document(
        case_id, uploaded["snapshot_id"], uploaded["document_id"])
    conversation = client.post(f"/api/v1/cases/{case_id}/conversations").json()
    response = client.post(
        f"/api/v1/conversations/{conversation['id']}/messages",
        headers={"Idempotency-Key": "too-long"},
        json={"content": "内" * 2001, "mode": "standard", "expected_revision": 1},
    )
    assert response.status_code == 422
    with engine.connect() as connection:
        assert connection.scalar(select(agent_runs.c.id)) is None


def test_run_once_preview_never_claims_or_creates_provider(uploaded_case, capsys):
    from backend.app.run_once import main

    client, engine, storage, case_id, uploaded = uploaded_case
    DocumentIngestion(engine, storage).process_document(
        case_id, uploaded["snapshot_id"], uploaded["document_id"])
    submitted, _ = _submit_question(client, case_id)

    def forbidden_provider():
        raise AssertionError("preview constructed a model provider")

    code = main(["--run-id", submitted["run_id"],
                 "--attempt-id", submitted["attempt_id"]],
                engine=engine, provider_factory=forbidden_provider)
    assert code == 0
    preview = json.loads(capsys.readouterr().out)
    assert preview["mode"] == "preview"
    assert preview["snapshot_status"] == "ready"
    assert preview["evidence_count"] > 0
    assert client.get(f"/api/v1/agent-runs/{submitted['run_id']}").json()[
        "status"] == "queued"


def test_run_once_send_requires_explicit_flag(uploaded_case, capsys):
    from backend.app.run_once import main

    client, engine, storage, case_id, uploaded = uploaded_case
    DocumentIngestion(engine, storage).process_document(
        case_id, uploaded["snapshot_id"], uploaded["document_id"])
    with engine.connect() as connection:
        evidence_id = connection.scalar(select(evidence_blocks.c.id).where(
            evidence_blocks.c.document_version_id == uploaded["document_id"],
            evidence_blocks.c.cell == "B6",
        ))
    submitted, _ = _submit_question(client, case_id)
    provider = SourceProvider(evidence_id)
    code = main(["--run-id", submitted["run_id"],
                 "--attempt-id", submitted["attempt_id"], "--send"],
                engine=engine, provider_factory=lambda: provider)
    assert code == 0
    sent = json.loads(capsys.readouterr().out)
    assert sent["mode"] == "send" and sent["status"] == "completed"
    assert provider.calls == 3


def test_run_once_reports_model_failure_without_echoing_error(uploaded_case, capsys):
    from backend.app.run_once import main

    client, engine, storage, case_id, uploaded = uploaded_case
    DocumentIngestion(engine, storage).process_document(
        case_id, uploaded["snapshot_id"], uploaded["document_id"])
    submitted, _ = _submit_question(client, case_id)

    class Fails:
        model_id = "fails"
        context_window = 32_768

        def decide(self, messages, tools, **kwargs):
            raise RuntimeError("private model failure detail")

    code = main(["--run-id", submitted["run_id"],
                 "--attempt-id", submitted["attempt_id"], "--send"],
                engine=engine, provider_factory=Fails)
    captured = capsys.readouterr()
    assert code == 1
    assert json.loads(captured.out) == {"error": "run_execution_failed"}
    assert "private model failure detail" not in captured.out + captured.err
    assert client.get(f"/api/v1/agent-runs/{submitted['run_id']}").json()[
        "status"] == "interrupted"
