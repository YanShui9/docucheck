"""A deletion request fences Case work while retaining records for cleanup."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select

from backend.app.api.main import create_app
from backend.app.db.correction_repository import (
    CorrectionError,
    CorrectionRepository,
    SubmitCorrection,
)
from backend.app.db.document_repository import DocumentRepository
from backend.app.db.ingestion import DocumentIngestion, IngestionError
from backend.app.db.models import (
    agent_runs,
    cases,
    document_versions,
    metadata,
    outbox,
    run_attempts,
    run_events,
)
from backend.app.db.outbox_repository import OutboxRepository
from backend.app.db.parse_repository import ParseRepository
from backend.app.db.run_repository import RepositoryError, RunRepository
from backend.app.runtime.lifecycle import RunStatus

EXAMPLE = (Path(__file__).resolve().parents[2] / "examples" /
           "dev-pack-001" / "requirements.pdf")


@pytest.fixture
def app_state(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'delete.db'}")

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, record):
        connection.execute("PRAGMA foreign_keys=ON")

    metadata.create_all(engine)
    data_root = tmp_path / "data"
    monkeypatch.setenv("DOCUCHECK_DATA_DIR", str(data_root))
    with TestClient(create_app(engine)) as client:
        yield client, engine, data_root
    engine.dispose()


def _case_with_run(client):
    case_id = client.post("/api/v1/cases").json()["id"]
    upload = client.post(
        f"/api/v1/cases/{case_id}/documents",
        headers={"Idempotency-Key": "upload-1"},
        data={"role": "requirement", "expected_revision": "0"},
        files={"file": ("requirements.pdf", EXAMPLE.read_bytes(),
                        "application/pdf")},
    )
    assert upload.status_code == 201, upload.text
    conversation = client.post(
        f"/api/v1/cases/{case_id}/conversations").json()["id"]
    message = client.post(
        f"/api/v1/conversations/{conversation}/messages",
        headers={"Idempotency-Key": "message-1"},
        json={"content": "审查", "expected_revision": 1},
    )
    assert message.status_code == 202, message.text
    return case_id, conversation, upload.json(), message.json()


def test_delete_request_is_idempotent_and_closes_case_writes(app_state):
    client, engine, data_root = app_state
    case_id, conversation, upload, message = _case_with_run(client)
    endpoint = f"/api/v1/cases/{case_id}"
    assert client.delete(endpoint, params={"expected_revision": 0}).status_code == 409
    first = client.delete(endpoint, params={"expected_revision": 1})
    assert first.status_code == 202, first.text
    assert first.json()["case_id"] == case_id
    assert first.json()["revision"] == 1
    assert first.json()["status"] == "deleting"
    assert first.json()["requested_at"]
    assert client.delete(endpoint, params={"expected_revision": 1}).json() == first.json()

    with engine.connect() as connection:
        assert connection.scalar(select(cases.c.deletion_requested_at).where(
            cases.c.id == case_id)) is not None
        assert connection.scalar(select(agent_runs.c.status).where(
            agent_runs.c.id == message["run_id"])) == "cancelled"
        assert connection.scalar(select(run_attempts.c.status).where(
            run_attempts.c.id == message["attempt_id"])) == "cancelled"
        assert connection.scalar(select(outbox.c.status).where(
            outbox.c.run_id == message["run_id"])) == "cancelled"
        assert connection.scalar(select(run_events.c.kind).where(
            run_events.c.run_id == message["run_id"]).order_by(
                run_events.c.seq.desc()).limit(1)) == "case_delete_requested"
        storage_key = connection.scalar(select(document_versions.c.storage_key).where(
            document_versions.c.id == upload["document_id"]))
    assert (data_root / storage_key).is_file()
    assert client.get(f"/api/v1/agent-runs/{message['run_id']}").json()[
        "error_code"] == "case_deleting"
    assert client.post(f"{endpoint}/conversations").json()["error"]["code"] == (
        "case_deleting")
    blocked_message = client.post(
        f"/api/v1/conversations/{conversation}/messages",
        headers={"Idempotency-Key": "message-2"},
        json={"content": "再次审查", "expected_revision": 1},
    )
    assert blocked_message.status_code == 409
    assert blocked_message.json()["error"]["code"] == "case_deleting"
    for action, body in (("retry", {"expected_revision": 1}),
                         ("resume", {"expected_revision": 1,
                                     "content": "继续审查"})):
        response = client.post(
            f"/api/v1/agent-runs/{message['run_id']}/{action}",
            headers={"Idempotency-Key": f"after-delete-{action}"}, json=body,
        )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "case_deleting"
    blocked_upload = client.post(
        f"{endpoint}/documents", headers={"Idempotency-Key": "upload-2"},
        data={"role": "requirement", "expected_revision": "1"},
        files={"file": ("requirements.pdf", EXAMPLE.read_bytes(),
                        "application/pdf")},
    )
    assert blocked_upload.status_code == 409
    assert blocked_upload.json()["error"]["code"] == "case_deleting"
    assert len(list((data_root / "uploads").rglob("*.pdf"))) == 1
    with pytest.raises(CorrectionError, match="case_deleting"):
        CorrectionRepository(engine).submit(SubmitCorrection(
            case_id, message["run_id"], "set", "fact", "16", "GB",
            "修正", 1, "correction-1",
        ))
    assert DocumentRepository(engine).list_current(case_id)["revision"] == 1
    other_case = client.post("/api/v1/cases").json()["id"]
    assert client.post(f"/api/v1/cases/{other_case}/conversations").status_code == 201


def test_delete_fences_running_run_and_parse_claim(app_state):
    client, engine, data_root = app_state
    case_id, _, upload, message = _case_with_run(client)
    parse_claim = ParseRepository(engine).claim()[0]
    running = RunRepository(engine).claim(message["run_id"], message["attempt_id"])
    assert running.status == RunStatus.RUNNING

    deleted = client.delete(f"/api/v1/cases/{case_id}",
                            params={"expected_revision": 1})
    assert deleted.status_code == 202, deleted.text
    with engine.connect() as connection:
        assert connection.scalar(select(document_versions.c.parse_status).where(
            document_versions.c.id == upload["document_id"])) == "cancelled"
        assert connection.scalar(select(run_attempts.c.outcome_unknown).where(
            run_attempts.c.id == message["attempt_id"])) is True
    assert ParseRepository(engine).claim() == []
    assert OutboxRepository(engine).claim() == []
    with pytest.raises(RepositoryError, match="case_deleting"):
        RunRepository(engine).finish(
            message["run_id"], message["attempt_id"], running.fence,
            upload["snapshot_id"], RunStatus.COMPLETED, answer_text="late",
        )
    with pytest.raises(IngestionError, match="case_deleting"):
        DocumentIngestion(engine, data_root).process_document(
            case_id, upload["snapshot_id"], upload["document_id"],
            claim_id=parse_claim.claim_id,
        )
    with engine.connect() as connection:
        assert connection.scalar(select(agent_runs.c.answer_text).where(
            agent_runs.c.id == message["run_id"])) is None
