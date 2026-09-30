"""Retryable Case cleanup keeps shared originals and terminal records safe."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, func, insert, inspect, select, update

from backend.app.api.main import create_app
from backend.app.cleanup_cases import main as cleanup_main
from backend.app.db.case_cleanup import CaseCleanup, CleanupError
from backend.app.db.models import (
    agent_runs,
    case_cleanup_files,
    cases,
    conversations,
    correction_reviews,
    corrections,
    document_versions,
    evidence_blocks,
    idempotency_records,
    input_snapshots,
    messages,
    metadata,
    outbox,
    run_attempts,
    run_events,
)
from backend.app.db.run_repository import RepositoryError, RunRepository
from backend.app.runtime.lifecycle import RunStatus

EXAMPLES = Path(__file__).resolve().parents[2] / "examples" / "dev-pack-001"


@pytest.fixture
def app_state(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'cleanup.db'}")

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, record):
        connection.execute("PRAGMA foreign_keys=ON")

    metadata.create_all(engine)
    data_root = tmp_path / "data"
    monkeypatch.setenv("DOCUCHECK_DATA_DIR", str(data_root))
    with TestClient(create_app(engine)) as client:
        yield client, engine, data_root
    engine.dispose()


def _upload(client, case_id, name, role, revision, key, supplier=None):
    form = {"role": role, "expected_revision": str(revision)}
    if supplier:
        form.update(supplier_handle=supplier, supplier_name=supplier)
    response = client.post(
        f"/api/v1/cases/{case_id}/documents",
        headers={"Idempotency-Key": key}, data=form,
        files={"file": (name, (EXAMPLES / name).read_bytes(),
                        "application/octet-stream")},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _count(connection, table, condition):
    return connection.scalar(select(func.count()).select_from(table).where(condition))


def test_cleanup_multisnapshot_case_preserves_run_until_files_done(app_state):
    client, engine, data_root = app_state
    case_id = client.post("/api/v1/cases").json()["id"]
    first = _upload(client, case_id, "requirements.pdf", "requirement", 0,
                    "cleanup-requirement")
    conversation = client.post(
        f"/api/v1/cases/{case_id}/conversations").json()["id"]
    message = client.post(
        f"/api/v1/conversations/{conversation}/messages",
        headers={"Idempotency-Key": "cleanup-question"},
        json={"content": "审查", "expected_revision": 1},
    ).json()
    second = _upload(client, case_id, "vendor-a-quote.xlsx", "quote", 1,
                     "cleanup-quote", supplier="vendor-a")
    with engine.begin() as connection:
        now = datetime.now(UTC).replace(tzinfo=None)
        connection.execute(insert(evidence_blocks).values(
            id="cleanup-evidence", document_version_id=first["document_id"],
            ordinal=0, source_text="待删除的证据", created_at=now,
        ))
        connection.execute(insert(corrections).values(
            id="cleanup-correction", case_id=case_id,
            source_run_id=message["run_id"],
            from_snapshot_id=first["snapshot_id"],
            to_snapshot_id=second["snapshot_id"],
            fact_set_id="fact-set", fact_id="fact", field_key="memory",
            supplier="vendor-a", old_fact_json={},
            corrected_candidate_json={}, source_evidence_ids_json=[],
            target_evidence_ids_json=[], reason="审计", created_at=now,
        ))
        connection.execute(insert(correction_reviews).values(
            correction_id="cleanup-correction", case_id=case_id,
            snapshot_id=second["snapshot_id"], review_status="complete",
            repository_fingerprint="a" * 64, corrected_fact_id="corrected",
            fact_set_json={}, check_result_json={}, created_at=now,
        ))
        connection.execute(insert(idempotency_records).values(
            id="cleanup-attempt-key",
            operation_scope=f"run:{message['run_id']}:retry",
            key="retry-key", request_hash="a" * 64,
            resource_type="attempt", resource_id=message["attempt_id"],
            created_at=now,
        ))
    with engine.connect() as connection:
        keys = set(connection.scalars(select(document_versions.c.storage_key).where(
            document_versions.c.case_id == case_id)).all())
    assert len(keys) == 2
    assert all((data_root / key).is_file() for key in keys)
    deleted = client.delete(f"/api/v1/cases/{case_id}",
                            params={"expected_revision": 2})
    assert deleted.status_code == 202
    assert client.get(f"/api/v1/cases/{case_id}/deletion").json()[
        "status"] == "deleting"
    cleaner = CaseCleanup(engine, data_root)
    claim = cleaner.claim(case_id=case_id)
    assert claim is not None
    assert client.get(f"/api/v1/agent-runs/{message['run_id']}").status_code == 200
    result = cleaner.process(claim)
    assert result["status"] == "deleted"
    assert result["files_removed"] == 2
    assert all(not (data_root / key).exists() for key in keys)
    status = client.get(f"/api/v1/cases/{case_id}/deletion")
    assert status.status_code == 200
    assert status.json()["status"] == "deleted"
    assert status.json()["completed_at"]
    assert client.delete(f"/api/v1/cases/{case_id}",
                         params={"expected_revision": 2}).json() == status.json()
    assert client.get(f"/api/v1/agent-runs/{message['run_id']}").status_code == 404
    assert client.get(f"/api/v1/cases/{case_id}/documents").status_code == 410
    assert client.get(
        f"/api/v1/cases/{case_id}/snapshots/{first['snapshot_id']}/evidence/"
        "cleanup-evidence"
    ).status_code == 410
    with engine.connect() as connection:
        assert connection.scalar(select(cases.c.deletion_completed_at).where(
            cases.c.id == case_id)) is not None
        for table, condition in (
            (case_cleanup_files, case_cleanup_files.c.case_id == case_id),
            (correction_reviews, correction_reviews.c.case_id == case_id),
            (corrections, corrections.c.case_id == case_id),
            (document_versions, document_versions.c.case_id == case_id),
            (input_snapshots, input_snapshots.c.case_id == case_id),
            (conversations, conversations.c.case_id == case_id),
            (agent_runs, agent_runs.c.case_id == case_id),
        ):
            assert _count(connection, table, condition) == 0
        for table in (evidence_blocks, messages, run_attempts, run_events,
                      outbox, idempotency_records):
            assert connection.scalar(select(func.count()).select_from(table)) == 0
    assert first["document_id"]


def test_cleanup_retains_file_referenced_by_another_case(app_state):
    client, engine, data_root = app_state
    first_case = client.post("/api/v1/cases").json()["id"]
    uploaded = _upload(client, first_case, "requirements.pdf", "requirement", 0,
                       "shared-original")
    second_case = client.post("/api/v1/cases").json()["id"]
    with engine.begin() as connection:
        source = connection.execute(select(document_versions).where(
            document_versions.c.id == uploaded["document_id"])).mappings().one()
        now = datetime.now(UTC).replace(tzinfo=None)
        connection.execute(update(cases).where(cases.c.id == second_case).values(
            revision=1))
        connection.execute(insert(input_snapshots).values(
            id="shared-snapshot", case_id=second_case, revision=1,
            created_at=now))
        connection.execute(insert(document_versions).values(
            id="shared-document", case_id=second_case,
            snapshot_id="shared-snapshot", role="requirement",
            original_name=source["original_name"],
            storage_key=source["storage_key"],
            source_sha256=source["source_sha256"],
            media_type=source["media_type"], parse_status="pending",
            created_at=now,
        ))
    path = data_root / source["storage_key"]
    assert client.delete(f"/api/v1/cases/{first_case}",
                         params={"expected_revision": 1}).status_code == 202
    assert CaseCleanup(engine, data_root).process_once(
        case_id=first_case)["status"] == "deleted"
    assert path.is_file()
    with engine.connect() as connection:
        assert _count(connection, document_versions,
                      document_versions.c.case_id == second_case) == 1
    assert client.delete(f"/api/v1/cases/{second_case}",
                         params={"expected_revision": 1}).status_code == 202
    result = CaseCleanup(engine, data_root).process_once(case_id=second_case)
    assert result["status"] == "deleted"
    assert result["files_removed"] == 1
    assert not path.exists()


def test_cleanup_failure_retries_and_expired_claim_is_fenced(app_state,
                                                               monkeypatch):
    client, engine, data_root = app_state
    case_id = client.post("/api/v1/cases").json()["id"]
    uploaded = _upload(client, case_id, "requirements.pdf", "requirement", 0,
                       "retry-original")
    conversation = client.post(
        f"/api/v1/cases/{case_id}/conversations").json()["id"]
    message = client.post(
        f"/api/v1/conversations/{conversation}/messages",
        headers={"Idempotency-Key": "retry-question"},
        json={"content": "审查", "expected_revision": 1},
    ).json()
    client.delete(f"/api/v1/cases/{case_id}",
                  params={"expected_revision": 1})
    cleaner = CaseCleanup(engine, data_root)
    now = datetime.now(UTC).replace(tzinfo=None)
    old_claim = cleaner.claim(case_id=case_id, now=now, lease_seconds=300)
    assert old_claim is not None
    assert cleaner.claim(case_id=case_id, now=now + timedelta(seconds=1)) is None
    new_claim = cleaner.claim(case_id=case_id,
                              now=now + timedelta(seconds=301))
    assert new_claim is not None and new_claim.claim_id != old_claim.claim_id
    with pytest.raises(CleanupError, match="stale_cleanup_claim"):
        cleaner.process(old_claim, now=now + timedelta(seconds=301))

    with engine.connect() as connection:
        key = connection.scalar(select(document_versions.c.storage_key).where(
            document_versions.c.id == uploaded["document_id"]))
    path = data_root / key
    original_unlink = Path.unlink
    failed = False

    def fail_once(self, *args, **kwargs):
        nonlocal failed
        if self == path and not failed:
            failed = True
            raise OSError("simulated failure")
        return original_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_once)
    result = cleaner.process(new_claim, now=now + timedelta(seconds=302))
    assert result["status"] == "deleting"
    assert result["error_code"] == "unlink_failed"
    assert path.is_file()
    assert client.get(f"/api/v1/agent-runs/{message['run_id']}").status_code == 200
    assert client.get(f"/api/v1/agent-runs/{message['run_id']}/facts").status_code == 409
    assert client.get(f"/api/v1/cases/{case_id}/documents").status_code == 409
    with engine.connect() as connection:
        assert _count(connection, document_versions,
                      document_versions.c.case_id == case_id) == 0
        assert _count(connection, case_cleanup_files,
                      case_cleanup_files.c.case_id == case_id) == 1
    assert cleaner.claim(case_id=case_id,
                         now=now + timedelta(seconds=303)) is None
    retry = cleaner.process_once(case_id=case_id,
                                  now=now + timedelta(seconds=363))
    assert retry["status"] == "deleted"
    assert not path.exists()


def test_cleanup_refuses_path_escape(app_state):
    client, engine, data_root = app_state
    case_id = client.post("/api/v1/cases").json()["id"]
    uploaded = _upload(client, case_id, "requirements.pdf", "requirement", 0,
                       "unsafe-original")
    outside = data_root.parent / "outside.pdf"
    outside.write_bytes(b"keep")
    with engine.begin() as connection:
        connection.execute(update(document_versions).where(
            document_versions.c.id == uploaded["document_id"]
        ).values(storage_key="uploads/../outside.pdf"))
    client.delete(f"/api/v1/cases/{case_id}",
                  params={"expected_revision": 1})
    result = CaseCleanup(engine, data_root).process_once(case_id=case_id)
    assert result["status"] == "deleting"
    assert result["error_code"] == "invalid_storage_key"
    assert outside.read_bytes() == b"keep"


def test_cleanup_expired_during_unlink_cannot_finalize(app_state, monkeypatch):
    client, engine, data_root = app_state
    case_id = client.post("/api/v1/cases").json()["id"]
    _upload(client, case_id, "requirements.pdf", "requirement", 0,
            "lease-original")
    client.delete(f"/api/v1/cases/{case_id}",
                  params={"expected_revision": 1})
    cleaner = CaseCleanup(engine, data_root)
    start = datetime.now(UTC).replace(tzinfo=None)
    clock = {"current": start}
    from backend.app.db import case_cleanup as cleanup_module

    monkeypatch.setattr(cleanup_module, "_now",
                        lambda value: clock["current"] if value is None else value)
    claim = cleaner.claim(now=start, lease_seconds=30)
    assert claim is not None
    original_unlink = Path.unlink

    def expire_on_unlink(path, *args, **kwargs):
        clock["current"] = start + timedelta(seconds=31)
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", expire_on_unlink)
    with pytest.raises(CleanupError, match="stale_cleanup_claim"):
        cleaner.process(claim)
    with engine.connect() as connection:
        assert connection.scalar(select(cases.c.deletion_completed_at).where(
            cases.c.id == case_id)) is None
        assert connection.scalar(select(case_cleanup_files.c.processed_at).where(
            case_cleanup_files.c.case_id == case_id)) is None
    retry = cleaner.process_once(case_id=case_id, now=clock["current"])
    assert retry["status"] == "deleted"


def test_late_run_completion_cannot_recreate_cleaned_case(app_state):
    client, engine, data_root = app_state
    case_id = client.post("/api/v1/cases").json()["id"]
    uploaded = _upload(client, case_id, "requirements.pdf", "requirement", 0,
                       "late-original")
    conversation = client.post(
        f"/api/v1/cases/{case_id}/conversations").json()["id"]
    message = client.post(
        f"/api/v1/conversations/{conversation}/messages",
        headers={"Idempotency-Key": "late-question"},
        json={"content": "审查", "expected_revision": 1},
    ).json()
    claimed = RunRepository(engine).claim(message["run_id"],
                                          message["attempt_id"])
    client.delete(f"/api/v1/cases/{case_id}",
                  params={"expected_revision": 1})
    assert CaseCleanup(engine, data_root).process_once(
        case_id=case_id)["status"] == "deleted"
    with pytest.raises(RepositoryError):
        RunRepository(engine).finish(
            message["run_id"], message["attempt_id"], claimed.fence,
            uploaded["snapshot_id"], RunStatus.COMPLETED, answer_text="late",
        )
    with engine.connect() as connection:
        assert _count(connection, agent_runs,
                      agent_runs.c.case_id == case_id) == 0


def test_cleanup_claim_queue_is_indexed(app_state):
    _, engine, _ = app_state
    assert "ix_cases_cleanup_due" in {
        index["name"] for index in inspect(engine).get_indexes("cases")
    }


def test_missing_file_after_interrupted_unlink_is_retried(app_state):
    client, engine, data_root = app_state
    case_id = client.post("/api/v1/cases").json()["id"]
    uploaded = _upload(client, case_id, "requirements.pdf", "requirement", 0,
                       "interrupted-unlink")
    client.delete(f"/api/v1/cases/{case_id}",
                  params={"expected_revision": 1})
    cleaner = CaseCleanup(engine, data_root)
    start = datetime.now(UTC).replace(tzinfo=None)
    claim = cleaner.claim(case_id=case_id, now=start, lease_seconds=30)
    assert claim is not None
    cleaner._prepare(claim, start)
    with engine.connect() as connection:
        key = connection.scalar(select(case_cleanup_files.c.storage_key).where(
            case_cleanup_files.c.case_id == case_id))
        assert connection.scalar(select(document_versions.c.id).where(
            document_versions.c.id == uploaded["document_id"])) is None
    (data_root / key).unlink()
    new_claim = cleaner.claim(case_id=case_id,
                              now=start + timedelta(seconds=31))
    assert new_claim is not None
    result = cleaner.process(new_claim, now=start + timedelta(seconds=31))
    assert result["status"] == "deleted"
    assert result["files_removed"] == 0


def test_cleanup_command_processes_only_requested_case(app_state):
    client, engine, data_root = app_state
    target = client.post("/api/v1/cases").json()["id"]
    other = client.post("/api/v1/cases").json()["id"]
    _upload(client, target, "requirements.pdf", "requirement", 0,
            "command-target")
    _upload(client, other, "requirements.pdf", "requirement", 0,
            "command-other")
    for case_id in (target, other):
        assert client.delete(f"/api/v1/cases/{case_id}",
                             params={"expected_revision": 1}).status_code == 202
    assert cleanup_main(["--once", "--case-id", target,
                         "--data-dir", str(data_root)], engine=engine) == 0
    assert client.get(f"/api/v1/cases/{target}/deletion").json()[
        "status"] == "deleted"
    assert client.get(f"/api/v1/cases/{other}/deletion").json()[
        "status"] == "deleting"
