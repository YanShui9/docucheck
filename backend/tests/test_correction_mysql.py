"""Opt-in MySQL correction race; synthetic source, no model or worker call."""

import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, replace
from datetime import UTC, datetime
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, delete, insert, select

from backend.app.agent.graph import AgentSession
from backend.app.agent.session_memory import serialize_session
from backend.app.db.agent_snapshot import load_agent_snapshot
from backend.app.db.connection import database_url
from backend.app.db.correction_recheck import CorrectionRecheck
from backend.app.db.correction_repository import (
    CorrectionError,
    CorrectionRepository,
    SubmitCorrection,
)
from backend.app.db.models import (
    agent_runs,
    cases,
    conversations,
    correction_reviews,
    corrections,
    document_versions,
    evidence_blocks,
    idempotency_records,
    input_snapshots,
    messages,
    run_attempts,
    suppliers,
)
from backend.app.evidence.repository import digest
from backend.app.extraction.fields import FieldCandidate, _validate_candidate
from backend.app.field_contract import FieldFactSet, FieldKey


def test_mysql_concurrent_corrections_advance_once():
    if os.getenv("DOCUCHECK_RUN_MYSQL_TESTS") != "1":
        pytest.skip("set DOCUCHECK_RUN_MYSQL_TESTS=1 for live MySQL correction check")
    engine = create_engine(database_url(), pool_size=4, pool_pre_ping=True,
                           hide_parameters=True)
    case_id, snapshot_id, supplier_id = uuid4().hex, uuid4().hex, uuid4().hex
    document_id, evidence_id = uuid4().hex, uuid4().hex
    conversation_id, message_id = uuid4().hex, uuid4().hex
    run_id, attempt_id = uuid4().hex, uuid4().hex
    now = datetime.now(UTC).replace(tzinfo=None)
    created = False
    try:
        with engine.begin() as connection:
            connection.execute(insert(cases).values(
                id=case_id, revision=1, created_at=now, updated_at=now))
            connection.execute(insert(suppliers).values(
                id=supplier_id, case_id=case_id, handle="vendor-a",
                name="Vendor A", created_at=now))
            connection.execute(insert(input_snapshots).values(
                id=snapshot_id, case_id=case_id, revision=1, created_at=now))
            connection.execute(insert(document_versions).values(
                id=document_id, case_id=case_id, snapshot_id=snapshot_id,
                supplier_id=supplier_id, role="quote", original_name="quote.xlsx",
                storage_key="uploads/synthetic/quote.xlsx", source_sha256="a" * 64,
                media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                parser_version="parser:test", parse_status="complete",
                coverage_json={"parser_version_full": "test:1", "total_sheets": 1,
                               "processed_sheets": ["报价"], "coverage_issues": []},
                created_at=now,
            ))
            connection.execute(insert(evidence_blocks).values(
                id=evidence_id, document_version_id=document_id, ordinal=0,
                source_text="标称 SSD 512 GB；标称 SSD 1 TB", sheet="报价",
                cell="B6", created_at=now,
            ))
            connection.execute(insert(conversations).values(
                id=conversation_id, case_id=case_id, created_at=now))
            connection.execute(insert(messages).values(
                id=message_id, conversation_id=conversation_id, role="user",
                content="审查存储", created_at=now))
        created = True
        repo = load_agent_snapshot(engine, case_id, snapshot_id)
        fact = _validate_candidate(FieldCandidate(
            field_key=FieldKey.STORAGE, raw_value="512", raw_unit="GB",
            evidence_refs=[evidence_id],
        ), repo, "vendor-a")
        scanned = (evidence_id,)
        fact_set_id = digest([
            snapshot_id, "vendor-a", "storage", scanned, False,
            [asdict(fact) | {"value": str(fact.value)}],
        ])
        fact_set = FieldFactSet(
            fact_set_id=fact_set_id, snapshot_id=snapshot_id,
            supplier="vendor-a", field_key=FieldKey.STORAGE,
            facts=(fact,), scanned_ids=scanned, complete=False,
            coverage_issues=("test_partial",),
        )
        session = AgentSession(repo, object())
        session.tools.fact_sets[fact_set_id] = fact_set
        with engine.begin() as connection:
            connection.execute(insert(agent_runs).values(
                id=run_id, case_id=case_id, conversation_id=conversation_id,
                message_id=message_id, snapshot_id=snapshot_id, revision=1,
                mode="standard", status="completed", active_attempt_id=attempt_id,
                fence=0, memory_json=serialize_session(session, "old"),
                created_at=now, updated_at=now,
            ))
            connection.execute(insert(run_attempts).values(
                id=attempt_id, run_id=run_id, attempt_no=1, status="completed",
                outcome_unknown=False, created_at=now,
            ))
        repository = CorrectionRepository(engine)
        request = SubmitCorrection(
            case_id=case_id, source_run_id=run_id, fact_set_id=fact_set_id,
            fact_id=fact.fact_id, raw_value="1", raw_unit="TB",
            reason="原文另有 1 TB", expected_revision=1,
            idempotency_key="race-1",
        )
        barrier = Barrier(2)

        def submit(key):
            barrier.wait()
            try:
                return repository.submit(replace(request, idempotency_key=key))
            except CorrectionError as exc:
                return exc.code

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(submit, ("race-1", "race-2")))
        assert sum(isinstance(result, dict) for result in results) == 1
        assert "revision_conflict" in results
        winning_key = "race-1" if isinstance(results[0], dict) else "race-2"
        receipt = repository.submit(replace(request, idempotency_key=winning_key))
        assert receipt["revision"] == 2
        assert repository.get_run_facts(run_id)["is_current"] is False
        with engine.connect() as connection:
            assert connection.scalar(select(cases.c.revision).where(
                cases.c.id == case_id)) == 2
            assert len(connection.execute(select(corrections).where(
                corrections.c.case_id == case_id)).all()) == 1
        rechecker = CorrectionRecheck(engine)
        recheck_barrier = Barrier(2)

        def recheck(_):
            recheck_barrier.wait()
            return rechecker.recheck(case_id, receipt["correction_id"])

        with ThreadPoolExecutor(max_workers=2) as pool:
            reviews = list(pool.map(recheck, range(2)))
        assert reviews[0] == reviews[1]
        assert reviews[0]["review_status"] == "needs_review"
        assert reviews[0]["check_result"]["business_status"] is None
        with engine.connect() as connection:
            assert len(connection.execute(select(correction_reviews).where(
                correction_reviews.c.case_id == case_id)).all()) == 1
    finally:
        try:
            if created:
                with engine.begin() as connection:
                    connection.execute(delete(idempotency_records).where(
                        idempotency_records.c.operation_scope ==
                        f"case:{case_id}:correction"))
                    connection.execute(delete(correction_reviews).where(
                        correction_reviews.c.case_id == case_id))
                    connection.execute(delete(corrections).where(
                        corrections.c.case_id == case_id))
                    document_ids = connection.scalars(select(document_versions.c.id).where(
                        document_versions.c.case_id == case_id)).all()
                    connection.execute(delete(evidence_blocks).where(
                        evidence_blocks.c.document_version_id.in_(document_ids)))
                    connection.execute(delete(document_versions).where(
                        document_versions.c.case_id == case_id))
                    connection.execute(delete(run_attempts).where(
                        run_attempts.c.run_id == run_id))
                    connection.execute(delete(agent_runs).where(
                        agent_runs.c.id == run_id))
                    connection.execute(delete(messages).where(
                        messages.c.conversation_id == conversation_id))
                    connection.execute(delete(conversations).where(
                        conversations.c.id == conversation_id))
                    connection.execute(delete(input_snapshots).where(
                        input_snapshots.c.case_id == case_id))
                    connection.execute(delete(suppliers).where(
                        suppliers.c.case_id == case_id))
                    connection.execute(delete(cases).where(cases.c.id == case_id))
        finally:
            engine.dispose()
