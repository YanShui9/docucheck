"""Opt-in MySQL row-lock check for background parse claims."""

import os
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, delete, insert, select

from backend.app.db.connection import database_url
from backend.app.db.models import cases, document_versions, input_snapshots
from backend.app.db.parse_repository import ParseRepository


def test_mysql_claim_skips_locked_document():
    if os.getenv("DOCUCHECK_RUN_MYSQL_TESTS") != "1":
        pytest.skip("set DOCUCHECK_RUN_MYSQL_TESTS=1 for live MySQL parse claim")
    engine = create_engine(database_url(), pool_pre_ping=True,
                           hide_parameters=True)
    now = datetime.now(UTC).replace(tzinfo=None)
    identities = [(uuid4().hex, uuid4().hex, uuid4().hex)
                  for _ in range(2)]
    try:
        with engine.begin() as connection:
            for case_id, snapshot_id, document_id in identities:
                connection.execute(insert(cases).values(
                    id=case_id, revision=1, created_at=now, updated_at=now))
                connection.execute(insert(input_snapshots).values(
                    id=snapshot_id, case_id=case_id, revision=1,
                    created_at=now))
                connection.execute(insert(document_versions).values(
                    id=document_id, case_id=case_id, snapshot_id=snapshot_id,
                    supplier_id=None, role="requirement", original_name="input.pdf",
                    storage_key=f"uploads/{document_id[:2]}/{document_id}.pdf",
                    source_sha256="a" * 64, media_type="application/pdf",
                    parse_status="pending", coverage_json={"preflight": {
                        "pdf_pages": 1}}, created_at=now))
        with engine.connect() as locked, locked.begin():
            locked.scalar(select(document_versions.c.id).where(
                document_versions.c.id == identities[0][2]).with_for_update())
            claimed = ParseRepository(engine).claim(limit=2)
            assert len(claimed) == 1
            assert claimed[0].document_id == identities[1][2]
        remaining = ParseRepository(engine).claim(limit=2)
        assert len(remaining) == 1
        assert remaining[0].document_id == identities[0][2]
    finally:
        try:
            with engine.begin() as connection:
                for case_id, snapshot_id, document_id in identities:
                    connection.execute(delete(document_versions).where(
                        document_versions.c.id == document_id))
                    connection.execute(delete(input_snapshots).where(
                        input_snapshots.c.id == snapshot_id))
                    connection.execute(delete(cases).where(cases.c.id == case_id))
        finally:
            engine.dispose()
