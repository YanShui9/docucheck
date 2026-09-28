"""Database Outbox delivery ownership and crash recovery."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, event, insert, select

from backend.app.db.models import (
    cases,
    conversations,
    input_snapshots,
    metadata,
    outbox,
)
from backend.app.db.outbox_repository import OutboxRepository
from backend.app.db.run_repository import RunRepository, SubmitRun


@pytest.fixture
def dispatchable(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'outbox.db'}")

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, record):
        connection.execute("PRAGMA foreign_keys=ON")

    metadata.create_all(engine)
    now = datetime.now(UTC).replace(tzinfo=None)
    with engine.begin() as connection:
        connection.execute(insert(cases).values(
            id="case-a", revision=1, created_at=now, updated_at=now,
        ))
        connection.execute(insert(input_snapshots).values(
            id="snapshot-a", case_id="case-a", revision=1, created_at=now,
        ))
        connection.execute(insert(conversations).values(
            id="conversation-a", case_id="case-a", created_at=now,
        ))
    receipt = RunRepository(engine).submit(SubmitRun(
        case_id="case-a", conversation_id="conversation-a",
        snapshot_id="snapshot-a", expected_revision=1,
        content="完整审查供应商 A", mode="thorough", idempotency_key="message-1",
    ))
    with engine.connect() as connection:
        due = connection.scalar(select(outbox.c.available_at))
    yield OutboxRepository(engine), engine, receipt, due + timedelta(seconds=1)
    engine.dispose()


def test_claim_ack_is_terminal_and_keeps_run_identity(dispatchable):
    repository, engine, receipt, now = dispatchable
    claims = repository.claim(now=now, lease_seconds=30)
    assert len(claims) == 1
    claim = claims[0]
    assert claim.run_id == receipt.run_id
    assert claim.attempt_id == receipt.attempt_id
    assert claim.event_key == f"run:{receipt.run_id}:attempt:{receipt.attempt_id}:dispatch"
    assert claim.event_type == "run.dispatch"
    assert claim.payload == {"run_id": receipt.run_id,
                             "attempt_id": receipt.attempt_id}
    assert claim.delivery_attempts == 1
    assert repository.claim(now=now + timedelta(seconds=29)) == []
    assert repository.mark_delivered(claim.id, claim.delivery_attempts,
                                     now=now + timedelta(seconds=2))
    with engine.connect() as connection:
        row = connection.execute(select(outbox).where(
            outbox.c.id == claim.id)).mappings().one()
    assert row["status"] == "delivered"
    assert row["delivered_at"] == now + timedelta(seconds=2)
    assert repository.claim(now=now + timedelta(minutes=2)) == []


def test_expired_lease_reclaims_and_stale_owner_cannot_finish(dispatchable):
    repository, engine, _, now = dispatchable
    first = repository.claim(now=now, lease_seconds=30)[0]
    second = repository.claim(now=now + timedelta(seconds=31),
                              lease_seconds=30)[0]
    assert second.id == first.id
    assert second.delivery_attempts == 2
    assert not repository.mark_delivered(first.id, first.delivery_attempts,
                                         now=now + timedelta(seconds=32))
    assert not repository.release(first.id, first.delivery_attempts,
                                  retry_delay_seconds=10,
                                  now=now + timedelta(seconds=32))
    with engine.connect() as connection:
        row = connection.execute(select(outbox).where(
            outbox.c.id == first.id)).mappings().one()
    assert row["status"] == "sending"
    assert row["delivery_attempts"] == 2
    assert repository.mark_delivered(second.id, second.delivery_attempts,
                                     now=now + timedelta(seconds=33))


def test_release_waits_for_retry_delay_and_validates_bounds(dispatchable):
    repository, engine, _, now = dispatchable
    first = repository.claim(now=now)[0]
    assert repository.release(first.id, first.delivery_attempts,
                              retry_delay_seconds=10, now=now)
    assert repository.claim(now=now + timedelta(seconds=9)) == []
    second = repository.claim(now=now + timedelta(seconds=10))[0]
    assert second.delivery_attempts == 2
    with engine.connect() as connection:
        row = connection.execute(select(outbox).where(
            outbox.c.id == first.id)).mappings().one()
    assert row["status"] == "sending"
    assert row["available_at"] == now + timedelta(seconds=70)
    with pytest.raises(ValueError, match="invalid_limit"):
        repository.claim(limit=0, now=now)
    with pytest.raises(ValueError, match="invalid_lease"):
        repository.claim(lease_seconds=0, now=now)
    with pytest.raises(ValueError, match="invalid_event_type"):
        repository.claim(event_type="", now=now)
    with pytest.raises(ValueError, match="invalid_retry_delay"):
        repository.release(second.id, second.delivery_attempts,
                           retry_delay_seconds=-1, now=now)
