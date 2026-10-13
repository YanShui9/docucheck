"""Interrupted upload recovery uses durable SQL ownership and real hard exits."""

import os
import subprocess
import sys
import textwrap
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from io import BytesIO
from pathlib import Path
from threading import Event

import pytest
from fastapi import UploadFile
from sqlalchemy import select

from backend.app.api.upload import UploadStorage
from backend.app.cleanup_cases import main as cleanup_main
from backend.app.db.document_repository import DocumentError
from backend.app.db.models import document_versions, upload_intents
from backend.app.db.upload_intents import GRACE_SECONDS, UploadIntents
from backend.tests import test_document_upload
from backend.tests.test_document_upload import _case, _pdf, _upload
from scripts.verify_offline import sanitized_env

uploaded_api = test_document_upload.uploaded_api


def _hard_exit(client, engine, storage, tmp_path, phase):
    case_id = _case(client)
    source = tmp_path / "input.pdf"
    source.write_bytes(_pdf(1))
    code = textwrap.dedent("""
        import os
        import sys
        from datetime import UTC, datetime, timedelta
        from pathlib import Path
        from fastapi.testclient import TestClient
        from sqlalchemy import create_engine
        from scripts.offline_guard import NetworkGuard
        from backend.app.api.main import create_app

        guard = NetworkGuard()
        guard.install()
        phase, database, storage, case_id, source = sys.argv[1:]
        os.environ["DOCUCHECK_DATA_DIR"] = storage
        try:
            from backend.app.db import upload_intents
        except ImportError:
            pass
        else:
            # The child clock is old, so restart recovery needs no real-time sleep.
            old = datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=1)
            upload_intents._now = lambda value=None: old if value is None else value
        original_replace = Path.replace
        def replace(path, target):
            if path.parent.name == ".staging" and phase == "staging":
                os._exit(17)
            result = original_replace(path, target)
            if path.parent.name == ".staging" and phase == "final":
                os._exit(17)
            return result
        Path.replace = replace
        engine = create_engine(database)
        with TestClient(create_app(engine)) as client:
            response = client.post(
                f"/api/v1/cases/{case_id}/documents",
                headers={"Idempotency-Key": "hard-exit"},
                data={"role": "requirement", "expected_revision": "0"},
                files={"file": ("input.pdf", Path(source).read_bytes(), "application/pdf")},
            )
            assert response.status_code == 201, response.text
            os._exit(17)
    """)
    result = subprocess.run(
        [sys.executable, "-c", code, phase, str(engine.url), str(storage),
         case_id, str(source)], env=sanitized_env(os.environ),
        cwd=Path(__file__).resolve().parents[2], capture_output=True,
        text=True, timeout=30, check=False,
    )
    assert result.returncode == 17, result.stdout + result.stderr
    return case_id, source


@pytest.mark.parametrize("phase", ["staging", "final"])
def test_restart_reclaims_uncommitted_upload_after_hard_exit(
    uploaded_api, tmp_path, monkeypatch, phase,
):
    client, engine, storage = uploaded_api
    case_id, _ = _hard_exit(client, engine, storage, tmp_path, phase)
    orphans = list(storage.rglob("*.pdf"))
    assert len(orphans) == 1
    with engine.connect() as connection:
        assert connection.scalar(select(document_versions.c.id).where(
            document_versions.c.case_id == case_id)) is None
    monkeypatch.setattr("backend.app.cleanup_cases.load_dotenv", lambda *a, **k: None)
    assert cleanup_main(["--once", "--data-dir", str(storage)], engine=engine) == 0
    assert not any(path.exists() for path in orphans)


def test_restart_retains_committed_original_and_replays_original_receipt(
    uploaded_api, tmp_path, monkeypatch,
):
    client, engine, storage = uploaded_api
    case_id, source = _hard_exit(client, engine, storage, tmp_path, "committed")
    with engine.connect() as connection:
        document = connection.execute(select(document_versions).where(
            document_versions.c.case_id == case_id)).mappings().one()
    path = storage / document["storage_key"]
    assert path.is_file()
    monkeypatch.setattr("backend.app.cleanup_cases.load_dotenv", lambda *a, **k: None)
    assert cleanup_main(["--once", "--data-dir", str(storage)], engine=engine) == 0
    assert path.is_file()
    replay = _upload(client, case_id, name="input.pdf", content=source.read_bytes(),
                     role="requirement", revision=0, key="hard-exit")
    assert replay.status_code == 201, replay.text
    assert replay.json()["document_id"] == document["id"]
    assert replay.json()["snapshot_id"] == document["snapshot_id"]
    assert len(list(storage.rglob("*.pdf"))) == 1


def _planned(engine, root, *, old=True):
    storage = UploadStorage(root)
    plan = storage.prepare(UploadFile(file=BytesIO(_pdf(1)), filename="orphan.pdf"))
    service = UploadIntents(engine, root)
    now = datetime.now(UTC).replace(tzinfo=None)
    intent = service.create(plan.document_id, plan.storage_key,
        now=now - timedelta(seconds=GRACE_SECONDS + 1) if old else now)
    return service, intent, now


def test_retired_owner_cannot_resume_and_late_file_is_still_recoverable(uploaded_api):
    _, engine, root = uploaded_api
    service, intent, now = _planned(engine, root)
    assert service.reconcile(now=now)["checked"] == 1
    with pytest.raises(DocumentError, match="upload_expired"), service.transaction(intent):
        pytest.fail("a retired uploader must not enter file publication")
    # Simulate a writer disconnected from SQL, resuming its filesystem work
    # after recovery, and then exiting again. The tombstone still tracks it.
    path = root / intent.storage_key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"late orphan")
    later = now + timedelta(seconds=GRACE_SECONDS + 1)
    assert service.reconcile(now=later)["removed"] == 1
    assert not path.exists()
    with engine.connect() as connection:
        assert connection.scalar(select(upload_intents.c.state)) == "abandoned"


def test_recovery_does_not_delete_a_file_while_upload_is_committing(uploaded_api, monkeypatch):
    client, engine, root = uploaded_api
    case = _case(client)
    entered, release = Event(), Event()
    published = []
    original = UploadStorage.save

    def paused_save(storage, file, **kwargs):
        result = original(storage, file, **kwargs)
        published.append(result.path)
        entered.set()
        assert release.wait(timeout=15)
        return result

    monkeypatch.setattr(UploadStorage, "save", paused_save)
    with ThreadPoolExecutor(max_workers=1) as pool:
        upload = pool.submit(_upload, client, case, name="race.pdf", content=_pdf(1),
            role="requirement", revision=0)
        try:
            assert entered.wait(timeout=10)
            result = UploadIntents(engine, root).reconcile(
                now=datetime.now(UTC).replace(tzinfo=None) + timedelta(hours=1))
            assert result["removed"] == 0 and published[0].is_file()
        finally:
            release.set()
        response = upload.result(timeout=10)
    assert response.status_code == 201, response.text
    with engine.connect() as connection:
        key = connection.scalar(select(document_versions.c.storage_key).where(
            document_versions.c.id == response.json()["document_id"]))
    assert (root / key).is_file()


def test_recovery_retries_unlink_failure_and_competing_cleaners(uploaded_api, monkeypatch):
    _, engine, root = uploaded_api
    service, intent, now = _planned(engine, root)
    path = root / intent.storage_key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"orphan")
    original = Path.unlink

    def fail(target, *args, **kwargs):
        if target == path:
            raise PermissionError("busy")
        return original(target, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "unlink", fail)
        assert service.reconcile(now=now)["error_code"] == "upload_recovery_failed"
    assert path.is_file()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: service.reconcile(now=now), range(2)))
    assert sum(row["removed"] for row in results) == 1
    assert not path.exists()


def test_recovery_preserves_originals_from_historical_snapshots(uploaded_api):
    client, engine, root = uploaded_api
    case = _case(client)
    first = _upload(client, case, name="original.pdf", content=_pdf(1), role="requirement", revision=0)
    assert first.status_code == 201
    second = _upload(client, case, name="replacement.pdf", content=_pdf(2), role="requirement", revision=1, key="replacement")
    assert second.status_code == 201
    with engine.connect() as connection:
        document = connection.execute(select(document_versions).where(
            document_versions.c.id == first.json()["document_id"])).mappings().one()
    service = UploadIntents(engine, root)
    now = datetime.now(UTC).replace(tzinfo=None)
    service.create(document["id"], document["storage_key"], now=now - timedelta(hours=1))
    assert service.reconcile(now=now)["retained"] == 1
    assert (root / document["storage_key"]).is_file()


def test_recovery_is_bounded_and_does_not_scan_legacy_files(uploaded_api):
    _, engine, root = uploaded_api
    service, intent, now = _planned(engine, root)
    root.mkdir(parents=True, exist_ok=True)
    legacy = root / "untracked.pdf"
    legacy.write_bytes(b"legacy")
    second, other, _ = _planned(engine, root)
    assert service.reconcile(now=now + timedelta(seconds=2), limit=1)["checked"] == 1
    assert second.reconcile(now=now + timedelta(seconds=2), limit=1)["checked"] == 1
    assert legacy.is_file()
    assert intent.document_id != other.document_id


@pytest.mark.parametrize("key", ["../escape.pdf", "uploads/00/" + "a" * 32 + ".pdf", "uploads/aa/" + "a" * 32 + ".txt"])
def test_intent_rejects_non_managed_or_wrong_bucket_paths(uploaded_api, key):
    _, engine, root = uploaded_api
    with pytest.raises(DocumentError, match="invalid_storage_key"):
        UploadIntents(engine, root).create("a" * 32, key)


def test_expired_upload_is_a_conflict_before_any_file_is_written(uploaded_api, monkeypatch):
    client, engine, root = uploaded_api
    case = _case(client)
    service = client.app.state.upload_intents
    original = service.create

    def expire(*args, **kwargs):
        intent = original(*args, **kwargs)
        assert service.reconcile(now=datetime.now(UTC).replace(tzinfo=None) + timedelta(hours=1))["checked"] == 1
        return intent

    monkeypatch.setattr(service, "create", expire)
    result = _upload(client, case, name="expired.pdf", content=_pdf(1), role="requirement", revision=0)
    assert result.status_code == 409 and result.json()["error"]["code"] == "upload_expired"
    assert not list(root.rglob("*.pdf"))
    with engine.connect() as connection:
        assert connection.scalar(select(document_versions.c.id)) is None
