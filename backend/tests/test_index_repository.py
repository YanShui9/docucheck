from dataclasses import replace
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine, delete, event, insert, select, update

from backend.app.db.index_repository import (
    IndexError,
    IndexRepository,
    enqueue_document,
)
from backend.app.db.models import (
    cases,
    document_index_jobs,
    document_versions,
    index_manifests,
    input_snapshots,
    metadata,
)
from backend.app.vector_store.target import IndexTarget

NOW = datetime(2026, 10, 3, 12)  # noqa: DTZ001 -- SQL UTC timestamps are deliberately naive.
TARGET = IndexTarget("ollama-bge-m3-" + "a" * 64 + "-normalized-v1", dimension=3)


@pytest.fixture
def indexed_db(tmp_path, monkeypatch):
    from backend.app.db import case_deletion

    # Cleanup scheduling and lease assertions must use the same clock. The
    # production deletion clock would otherwise overtake this fixed fixture.
    monkeypatch.setattr(case_deletion, "_now", lambda: NOW)
    path = tmp_path / "index.db"
    engine = create_engine(f"sqlite:///{path}")

    @event.listens_for(engine, "connect")
    def fk(conn, record):
        conn.execute("PRAGMA foreign_keys=ON")

    metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(insert(cases).values(id="case", revision=1, created_at=NOW, updated_at=NOW))
        conn.execute(insert(input_snapshots).values(id="snap", case_id="case", revision=1, created_at=NOW))
        conn.execute(insert(document_versions).values(id="doc", case_id="case", snapshot_id="snap", role="requirement",
                     original_name="需求.pdf", storage_key="unused", source_sha256="a" * 64, media_type="application/pdf",
                     parser_version="parser", parse_status="complete", coverage_json={}, created_at=NOW))
    yield engine, path
    engine.dispose()


def queue(engine):
    with engine.begin() as conn:
        enqueue_document(conn, "doc", now=NOW)


def test_registration_is_atomic_idempotent_and_survives_reopen(indexed_db):
    engine, path = indexed_db
    with pytest.raises(RuntimeError), engine.begin() as conn:
        enqueue_document(conn, "doc", now=NOW)
        raise RuntimeError("rollback")
    with engine.connect() as conn:
        assert conn.execute(select(document_index_jobs)).first() is None
    queue(engine)
    queue(engine)
    reopened = create_engine(f"sqlite:///{path}")
    try:
        claim = IndexRepository(reopened).claim(TARGET, now=NOW)
        assert claim.document_id == "doc" and claim.generation
        assert IndexRepository(engine).claim(TARGET, now=NOW) is None
    finally:
        reopened.dispose()


def test_expired_worker_cannot_submit_and_generation_changes(indexed_db):
    engine, _ = indexed_db
    queue(engine)
    service = IndexRepository(engine)
    first = service.claim(TARGET, now=NOW, lease_seconds=60)
    next_time = NOW + timedelta(seconds=61)
    second = service.claim(TARGET, now=next_time, lease_seconds=60)
    assert first.generation != second.generation and second.fence > first.fence
    with pytest.raises(IndexError, match="stale_index_claim"):
        service.finish(first, "f" * 64, 2, now=next_time)
    service.finish(second, "f" * 64, 2, now=next_time)
    with engine.connect() as conn:
        job = conn.execute(select(document_index_jobs)).mappings().one()
        old = conn.execute(select(index_manifests).where(index_manifests.c.id == first.manifest_id)).mappings().one()
    assert job["status"] == "ready" and job["manifest_id"] == second.manifest_id
    assert old["status"] == "deleting"


def test_failure_has_retry_delay_and_bounded_attempts(indexed_db):
    engine, _ = indexed_db
    queue(engine)
    service = IndexRepository(engine)
    current = NOW
    for _ in range(3):
        claim = service.claim(TARGET, now=current)
        service.fail(claim, "index_processing_failed", now=current)
        assert service.claim(TARGET, now=current) is None
        current += timedelta(seconds=301)
    assert service.claim(TARGET, now=current) is None
    service.schedule("case", "snap", 1, retry_failed=True, now=current)
    assert service.claim(TARGET, now=current) is not None


def test_deleted_case_and_snapshot_change_fence_running_job(indexed_db):
    from backend.app.db.case_deletion import CaseDeletion

    engine, _ = indexed_db
    queue(engine)
    service = IndexRepository(engine)
    claim = service.claim(TARGET, now=NOW)
    CaseDeletion(engine).request("case", 1)
    with pytest.raises(IndexError):
        service.finish(claim, "f" * 64, 1, now=NOW)
    with engine.connect() as conn:
        assert conn.scalar(select(index_manifests.c.status)) == "deleting"
    assert service.claim(TARGET, now=NOW) is None


def test_new_snapshot_cannot_be_marked_ready_by_old_claim(indexed_db):
    engine, _ = indexed_db
    queue(engine)
    service = IndexRepository(engine)
    claim = service.claim(TARGET, now=NOW)
    with engine.begin() as conn:
        conn.execute(update(cases).values(revision=2))
    with pytest.raises(IndexError, match="index_snapshot_changed"):
        service.finish(claim, "f" * 64, 1, now=NOW)
    assert service.reconcile_stale(now=NOW) == 1
    with engine.connect() as conn:
        assert conn.scalar(select(index_manifests.c.status)) == "deleting"
    assert service.claim_cleanup(now=NOW) is not None


def test_failed_cleanup_lease_blocks_early_reclaim_and_old_claim(indexed_db):
    from backend.app.db.case_deletion import CaseDeletion
    engine, _ = indexed_db
    queue(engine)
    service = IndexRepository(engine)
    service.claim(TARGET, now=NOW)
    CaseDeletion(engine).request("case", 1)
    current = NOW + timedelta(days=1)
    first = service.claim_cleanup(now=current, lease_seconds=60)
    assert service.claim_cleanup(now=current) is None
    second = service.claim_cleanup(now=current + timedelta(seconds=61))
    with pytest.raises(IndexError, match="stale_index_cleanup_claim"):
        service.finish_cleanup(first, now=current + timedelta(seconds=61))
    service.finish_cleanup(second, success=False, now=current + timedelta(seconds=61))
    assert service.claim_cleanup(now=current + timedelta(seconds=62)) is None
    assert service.claim_cleanup(now=current + timedelta(seconds=122)) is not None


def test_cleanup_tombstone_survives_source_deletion_and_can_reconcile_again(indexed_db):
    from backend.app.db.case_deletion import CaseDeletion

    engine, _ = indexed_db
    queue(engine)
    service = IndexRepository(engine)
    claim = service.claim(TARGET, now=NOW)
    service.finish(claim, "f" * 64, 1, now=NOW)
    CaseDeletion(engine).request("case", 1)
    with engine.begin() as conn:
        conn.execute(delete(document_versions))
        conn.execute(delete(input_snapshots))
    cleanup = service.claim_cleanup(now=NOW + timedelta(days=1))
    assert cleanup.manifest_id == claim.manifest_id and cleanup.document_id == "doc"
    service.finish_cleanup(cleanup, now=NOW + timedelta(days=1))
    assert service.claim_cleanup(now=NOW + timedelta(days=1)) is None
    assert service.claim_cleanup(now=NOW + timedelta(days=1, seconds=301)) is not None


def test_ready_job_does_not_repeat_embedding_until_explicit_target_changes(indexed_db):
    engine, _ = indexed_db
    queue(engine)
    service = IndexRepository(engine)
    first = service.claim(TARGET, now=NOW)
    service.finish(first, "f" * 64, 1, now=NOW)
    assert service.claim(TARGET, now=NOW) is None
    service.schedule("case", "snap", 1, target=replace(TARGET, revision=TARGET.revision.replace("a" * 64, "b" * 64)), now=NOW)
    assert service.claim(replace(TARGET, revision=TARGET.revision.replace("a" * 64, "b" * 64)), now=NOW) is not None
