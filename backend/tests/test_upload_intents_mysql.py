"""Opt-in MySQL upload fencing in an explicitly isolated local database."""
import os
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from io import BytesIO
from threading import Event, current_thread
from uuid import uuid4

import pytest
from fastapi import UploadFile
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, delete, event, insert, select, update
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError

from backend.app.api.auth import LocalAuthenticator
from backend.app.api.main import create_app
from backend.app.api.upload import UploadStorage
from backend.app.db.document_repository import DocumentError
from backend.app.db.models import (
    agent_runs,
    cases,
    document_versions,
    idempotency_records,
    input_snapshots,
    run_attempts,
    upload_intents,
)
from backend.app.db.run_repository import RepositoryError, SubmitRun
from backend.app.db.upload_intents import GRACE_SECONDS
from backend.app.runtime.lifecycle import RunStatus
from backend.tests import test_run_repository_mysql
from backend.tests.test_document_upload import _pdf, _upload

pytestmark = pytest.mark.skipif(os.getenv("DOCUCHECK_RUN_B_MYSQL_TESTS") != "1",
    reason="explicit isolated B MySQL opt-in")
mysql_repository = test_run_repository_mysql.mysql_repository


@pytest.fixture(autouse=True)
def isolated_target():
    targets = [make_url(os.environ[key]) for key in ("DOCUCHECK_B_MYSQL_URL", "DOCUCHECK_DATABASE_URL")]
    for url in targets:
        if (url.get_backend_name() != "mysql" or url.host not in {"localhost", "127.0.0.1"}
                or not re.fullmatch(r"docucheck_b_[a-f0-9]{12}", url.database or "")):
            raise ValueError("isolated_mysql_required")
    if targets[0] != targets[1]:
        raise ValueError("isolated_mysql_target_mismatch")


@pytest.fixture
def mysql_upload_scope(tmp_path, monkeypatch):
    url = make_url(os.environ["DOCUCHECK_B_MYSQL_URL"])
    if (url.get_backend_name() != "mysql" or url.host not in {"localhost", "127.0.0.1"}
            or not re.fullmatch(r"docucheck_b_[a-f0-9]{12}", url.database or "")):
        raise ValueError("isolated_mysql_required")
    engine = create_engine(url, pool_pre_ping=True, hide_parameters=True, pool_size=4)
    root = tmp_path / "originals"
    monkeypatch.setenv("DOCUCHECK_DATA_DIR", str(root))
    case_ids, intent_ids = [], []
    now = datetime.now(UTC).replace(tzinfo=None)

    def create_case():
        case_id = uuid4().hex
        with engine.begin() as connection:
            connection.execute(insert(cases).values(id=case_id, revision=0, created_at=now, updated_at=now))
        case_ids.append(case_id)
        return case_id

    try:
        with TestClient(create_app(engine, LocalAuthenticator())) as client:
            service = client.app.state.upload_intents
            original_create = service.create

            def track(*args, **kwargs):
                intent = original_create(*args, **kwargs)
                intent_ids.append(intent.document_id)
                return intent

            monkeypatch.setattr(service, "create", track)
            try:
                yield engine, root, service, client, create_case, now
            finally:
                with engine.begin() as connection:
                    connection.execute(delete(idempotency_records).where(
                        idempotency_records.c.operation_scope.in_([f"case:{key}:upload" for key in case_ids])))
                    connection.execute(delete(document_versions).where(document_versions.c.case_id.in_(case_ids)))
                    connection.execute(delete(input_snapshots).where(input_snapshots.c.case_id.in_(case_ids)))
                    connection.execute(delete(cases).where(cases.c.id.in_(case_ids)))
                    connection.execute(delete(upload_intents).where(upload_intents.c.id.in_(intent_ids)))
    finally:
        engine.dispose()


def orphan(service, root, now):
    plan = UploadStorage(root).prepare(UploadFile(file=BytesIO(_pdf(1)), filename="orphan.pdf"))
    intent = service.create(plan.document_id, plan.storage_key, now=now - timedelta(hours=1))
    path = root / intent.storage_key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_pdf(1))
    return intent, path


def test_mysql_skips_active_upload_and_reclaims_later_orphan(mysql_upload_scope, monkeypatch):
    engine, root, service, client, create_case, now = mysql_upload_scope
    case_id = create_case()
    entered, release = Event(), Event()
    published = []
    original_create, original_save = service.create, UploadStorage.save

    def old_create(*args, **kwargs):
        kwargs.setdefault("now", now - timedelta(hours=2))
        return original_create(*args, **kwargs)

    def pause(storage, file, **kwargs):
        saved = original_save(storage, file, **kwargs)
        published.append(saved.path)
        entered.set()
        assert release.wait(timeout=20)
        return saved

    monkeypatch.setattr(service, "create", old_create)
    monkeypatch.setattr(UploadStorage, "save", pause)
    with ThreadPoolExecutor(max_workers=1) as pool:
        request = pool.submit(_upload, client, case_id, name="active.pdf", content=_pdf(1), role="requirement", revision=0)
        try:
            assert entered.wait(timeout=10)
            _, path = orphan(service, root, now)
            recovered = service.reconcile(now=now, limit=1)
            assert recovered["checked"] == recovered["removed"] == 1
            assert not path.exists() and published[0].is_file()
        finally:
            release.set()
        response = request.result(timeout=10)
    assert response.status_code == 201, response.text
    with engine.connect() as connection:
        key = connection.scalar(select(document_versions.c.storage_key).where(document_versions.c.id == response.json()["document_id"]))
    assert (root / key).is_file()


def test_mysql_disconnected_owner_cannot_commit_and_late_file_is_recovered(mysql_upload_scope):
    engine, root, service, _, _, now = mysql_upload_scope
    intent, path = orphan(service, root, now)
    with pytest.raises(SQLAlchemyError), service.transaction(intent) as connection:
        connection_id = int(connection.exec_driver_sql("SELECT CONNECTION_ID()").scalar_one())
        with engine.connect() as killer:
            killer.exec_driver_sql(f"KILL CONNECTION {connection_id}")
        recovered = service.reconcile(now=now)
        assert recovered["removed"] == 1 and not path.exists()
        path.write_bytes(_pdf(1))  # A paused writer resumes after losing its SQL lock.
        service.require_owner(connection, intent)
    with pytest.raises(DocumentError, match="upload_expired"), service.transaction(intent):
        pytest.fail("an abandoned owner must not resume SQL submission")
    assert service.reconcile(now=now + timedelta(seconds=GRACE_SECONDS + 1))["removed"] == 1
    assert not path.exists()
    with engine.connect() as connection:
        assert connection.scalar(select(document_versions.c.id).where(document_versions.c.storage_key == intent.storage_key)) is None


def test_mysql_current_read_preserves_new_reference_despite_old_rr_snapshot(mysql_upload_scope):
    engine, root, service, _, create_case, now = mysql_upload_scope
    create_case()  # Original uploader's Case has no committed document yet.
    other_case = create_case()
    intent, path = orphan(service, root, now)
    snapshot_ready, reference_committed = Event(), Event()

    def stale_read(connection, cursor, statement, parameters, context, executemany):
        if (current_thread().name.startswith("b-mysql-rr") and "FROM document_versions" in statement
                and "FOR UPDATE" in statement and not connection.info.get("b_snapshot_created")):
            connection.info["b_snapshot_created"] = True
            assert connection.exec_driver_sql("SELECT @@session.transaction_isolation").scalar_one() == "REPEATABLE-READ"
            assert connection.scalar(select(document_versions.c.id).where(document_versions.c.storage_key == intent.storage_key)) is None
            snapshot_ready.set()
            assert reference_committed.wait(timeout=10)

    event.listen(engine, "before_cursor_execute", stale_read)
    try:
        with ThreadPoolExecutor(max_workers=1, thread_name_prefix="b-mysql-rr") as pool:
            recovery = pool.submit(service.reconcile, now=now)
            try:
                assert snapshot_ready.wait(timeout=10)
                with engine.begin() as connection:
                    snapshot = uuid4().hex
                    connection.execute(update(cases).where(cases.c.id == other_case).values(revision=1))
                    connection.execute(insert(input_snapshots).values(id=snapshot, case_id=other_case, revision=1, created_at=now))
                    connection.execute(insert(document_versions).values(id=uuid4().hex, case_id=other_case,
                        snapshot_id=snapshot, role="requirement", original_name="shared.pdf", storage_key=intent.storage_key,
                        source_sha256=sha256(path.read_bytes()).hexdigest(), media_type="application/pdf",
                        parse_status="pending", created_at=now))
            finally:
                reference_committed.set()
            result = recovery.result(timeout=10)
        assert result["retained"] == 1 and result["removed"] == 0 and path.is_file()
    finally:
        event.remove(engine, "before_cursor_execute", stale_read)


def test_mysql_running_cancel_remains_unknown_and_rejects_late_finish(mysql_repository):
    repository, (case_id, snapshot, conversation) = mysql_repository
    receipt = repository.submit(SubmitRun(case_id=case_id, snapshot_id=snapshot, conversation_id=conversation,
        expected_revision=1, content="取消中的外部调用", mode="standard", idempotency_key="b-cancel-unknown"))
    claimed = repository.claim(receipt.run_id, receipt.attempt_id)
    assert repository.cancel(receipt.run_id).status == RunStatus.CANCELLED
    with pytest.raises(RepositoryError, match="stale_attempt"):
        repository.finish(receipt.run_id, receipt.attempt_id, claimed.fence, snapshot, RunStatus.COMPLETED, "late")
    with repository.engine.connect() as connection:
        assert connection.scalar(select(agent_runs.c.status).where(agent_runs.c.id == receipt.run_id)) == RunStatus.CANCELLED.value
        assert connection.scalar(select(run_attempts.c.status).where(run_attempts.c.id == receipt.attempt_id)) == RunStatus.CANCELLED.value
        assert connection.scalar(select(run_attempts.c.outcome_unknown).where(run_attempts.c.id == receipt.attempt_id)) is True
        assert connection.scalar(select(agent_runs.c.answer_text).where(agent_runs.c.id == receipt.run_id)) is None
