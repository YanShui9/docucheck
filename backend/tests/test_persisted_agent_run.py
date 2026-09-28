"""A submitted Run uses evidence from its bound uploaded snapshot."""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage
from sqlalchemy import create_engine, event, select, update

from backend.app.api.main import create_app
from backend.app.db.ingestion import DocumentIngestion
from backend.app.db.models import (
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


def test_cancelled_run_rejects_late_agent_result(uploaded_case):
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
            if self.calls == 2:
                RunRepository(engine).cancel(submitted["run_id"])
            return super().decide(messages, tools, **kwargs)

    with pytest.raises(RepositoryError, match="stale_attempt"):
        execute_run(engine, submitted["run_id"], submitted["attempt_id"],
                    Cancels(evidence_id))
    persisted = client.get(f"/api/v1/agent-runs/{submitted['run_id']}").json()
    assert persisted["status"] == "cancelled"
    assert persisted["answer_text"] is None


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
