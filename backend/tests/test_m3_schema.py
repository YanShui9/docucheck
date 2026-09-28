"""The first M3 migration preserves case/snapshot scope and run identity."""

from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, event, func, insert, inspect, select
from sqlalchemy.engine import URL
from sqlalchemy.exc import IntegrityError

from backend.app.db.models import (
    agent_runs,
    cases,
    conversations,
    document_versions,
    idempotency_records,
    input_snapshots,
    messages,
    metadata,
    outbox,
    run_attempts,
    suppliers,
)

ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 9, 28, tzinfo=UTC)


def _engine():
    engine = create_engine("sqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, record):
        connection.execute("PRAGMA foreign_keys=ON")

    metadata.create_all(engine)
    return engine


def _case(connection, case_id: str, snapshot_id: str) -> None:
    connection.execute(insert(cases).values(id=case_id, revision=1,
                                            created_at=NOW, updated_at=NOW))
    connection.execute(insert(input_snapshots).values(
        id=snapshot_id, case_id=case_id, revision=1, created_at=NOW,
    ))


def test_run_attempt_and_outbox_can_share_one_case_snapshot():
    engine = _engine()
    with engine.begin() as connection:
        _case(connection, "case-a", "snapshot-a")
        connection.execute(insert(conversations).values(id="conv-a", case_id="case-a",
                                                        created_at=NOW))
        connection.execute(insert(messages).values(id="msg-a", conversation_id="conv-a",
                                                   role="user", content="审查供应商 A", created_at=NOW))
        connection.execute(insert(agent_runs).values(
            id="run-a", case_id="case-a", conversation_id="conv-a", message_id="msg-a",
            snapshot_id="snapshot-a", revision=1, mode="thorough", status="queued",
            active_attempt_id="attempt-a", fence=0, created_at=NOW, updated_at=NOW,
        ))
        connection.execute(insert(run_attempts).values(
            id="attempt-a", run_id="run-a", attempt_no=1, status="queued",
            outcome_unknown=False, created_at=NOW,
        ))
        connection.execute(insert(outbox).values(
            id="outbox-a", event_key="run-a:attempt-a:dispatch", run_id="run-a",
            attempt_id="attempt-a", event_type="run.dispatch", payload_json={},
            status="pending", delivery_attempts=0, available_at=NOW, created_at=NOW,
        ))
    with engine.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(agent_runs)) == 1
        assert connection.scalar(select(func.count()).select_from(outbox)) == 1


def test_run_rejects_snapshot_from_another_case():
    engine = _engine()
    with engine.begin() as connection:
        _case(connection, "case-a", "snapshot-a")
        _case(connection, "case-b", "snapshot-b")
        connection.execute(insert(conversations).values(id="conv-a", case_id="case-a",
                                                        created_at=NOW))
        connection.execute(insert(messages).values(id="msg-a", conversation_id="conv-a",
                                                   role="user", content="审查", created_at=NOW))
        with pytest.raises(IntegrityError):
            connection.execute(insert(agent_runs).values(
                id="run-a", case_id="case-a", conversation_id="conv-a", message_id="msg-a",
                snapshot_id="snapshot-b", revision=1, mode="standard", status="queued",
                active_attempt_id="attempt-a", fence=0, created_at=NOW, updated_at=NOW,
            ))


def test_document_rejects_supplier_from_another_case():
    engine = _engine()
    with engine.begin() as connection:
        _case(connection, "case-a", "snapshot-a")
        _case(connection, "case-b", "snapshot-b")
        connection.execute(insert(suppliers).values(id="supplier-b", case_id="case-b",
                                                    handle="vendor-b", name="B", created_at=NOW))
        with pytest.raises(IntegrityError):
            connection.execute(insert(document_versions).values(
                id="doc-a", case_id="case-a", snapshot_id="snapshot-a",
                supplier_id="supplier-b", role="quote", original_name="报价.xlsx",
                storage_key="case-a/报价.xlsx", source_sha256="a" * 64,
                media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                parse_status="pending", created_at=NOW,
            ))


def test_idempotency_scope_and_key_are_unique():
    engine = _engine()
    with engine.begin() as connection:
        first = {"id": "idem-a", "operation_scope": "case-a:run", "key": "same-key",
                 "request_hash": "a" * 64, "resource_type": "run", "resource_id": "run-a",
                 "created_at": NOW}
        connection.execute(insert(idempotency_records).values(**first))
        with pytest.raises(IntegrityError):
            connection.execute(insert(idempotency_records).values(
                **(first | {"id": "idem-b", "request_hash": "b" * 64})
            ))


def test_first_migration_upgrades_and_downgrades_offline_sqlite(tmp_path: Path):
    config = Config(str(ROOT / "backend" / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "backend" / "migrations"))
    config.set_main_option("sqlalchemy.url", str(URL.create(
        "sqlite", database=str(tmp_path / "schema.db"),
    )))

    command.upgrade(config, "head")
    engine = create_engine(config.get_main_option("sqlalchemy.url"))
    assert set(metadata.tables).issubset(set(inspect(engine).get_table_names()))
    with engine.connect() as connection:
        assert compare_metadata(MigrationContext.configure(connection), metadata) == []

    command.downgrade(config, "base")
    assert not set(metadata.tables) & set(inspect(engine).get_table_names())
