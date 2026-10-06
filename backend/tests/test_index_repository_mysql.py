"""Opt-in real InnoDB races and locator retention; synthetic metadata, no model."""
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, delete, insert, select

from backend.app.db.case_deletion import CaseDeletion
from backend.app.db.connection import database_url
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
)
from backend.app.vector_store.target import IndexTarget

pytestmark = pytest.mark.skipif(os.getenv("DOCUCHECK_RUN_MYSQL_TESTS") != "1", reason="explicit MySQL opt-in")


def test_concurrent_claim_stale_finish_and_retained_cleanup():
    engine = create_engine(database_url(), pool_size=4, pool_pre_ping=True, hide_parameters=True)
    case_id, snapshot_id, doc_id = uuid4().hex, uuid4().hex, uuid4().hex
    now = datetime.now(UTC).replace(tzinfo=None)
    target = IndexTarget("ollama-bge-m3-" + "c" * 64 + "-normalized-v1", 3)
    try:
        with engine.begin() as conn:
            conn.execute(insert(cases).values(id=case_id, revision=1, created_at=now, updated_at=now))
            conn.execute(insert(input_snapshots).values(id=snapshot_id, case_id=case_id, revision=1, created_at=now))
            conn.execute(insert(document_versions).values(id=doc_id, case_id=case_id, snapshot_id=snapshot_id,
                role="requirement", original_name="synthetic.pdf", storage_key="unused", media_type="application/pdf",
                source_sha256="a" * 64, parser_version="test", parse_status="complete", coverage_json={}, created_at=now))
            enqueue_document(conn, doc_id, now=now)
        service = IndexRepository(engine)
        service.schedule(case_id, snapshot_id, 1, target=target, now=now)
        gate = Barrier(2)
        def claim():
            gate.wait(timeout=10)
            return service.claim(target, now=now, lease_seconds=60)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: claim(), range(2)))
        assert sum(result is not None for result in results) == 1
        first = next(result for result in results if result is not None)
        assert first.document_id == doc_id
        later = now + timedelta(seconds=61)
        second = service.claim(target, now=later)
        assert second.document_id == doc_id and second.generation != first.generation
        with pytest.raises(IndexError, match="stale_index_claim"):
            service.finish(first, "f" * 64, 1, now=later)
        service.finish(second, "f" * 64, 1, now=later)
        CaseDeletion(engine).request(case_id, 1)
        with engine.begin() as conn:
            conn.execute(delete(document_versions).where(document_versions.c.case_id == case_id))
            conn.execute(delete(input_snapshots).where(input_snapshots.c.case_id == case_id))
            assert not conn.scalar(select(document_index_jobs.c.document_id).where(document_index_jobs.c.case_id == case_id))
            assert conn.scalar(select(index_manifests.c.id).where(index_manifests.c.case_id == case_id))
    finally:
        with engine.begin() as conn:
            conn.execute(delete(document_index_jobs).where(document_index_jobs.c.case_id == case_id))
            conn.execute(delete(index_manifests).where(index_manifests.c.case_id == case_id))
            conn.execute(delete(document_versions).where(document_versions.c.case_id == case_id))
            conn.execute(delete(input_snapshots).where(input_snapshots.c.case_id == case_id))
            conn.execute(delete(cases).where(cases.c.id == case_id))
        engine.dispose()
