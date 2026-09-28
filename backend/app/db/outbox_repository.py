"""Lease pending Outbox events without sending broker messages in a DB transaction."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import Engine, select, update

from .models import outbox


@dataclass(frozen=True)
class OutboxClaim:
    id: str
    event_key: str
    run_id: str
    attempt_id: str
    event_type: str
    payload: dict
    delivery_attempts: int


def _now(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(UTC).replace(tzinfo=None)
    if value.tzinfo is not None:
        return value.astimezone(UTC).replace(tzinfo=None)
    return value


class OutboxRepository:
    def __init__(self, engine: Engine):
        self.engine = engine

    def claim(self, *, limit: int = 10, lease_seconds: int = 60,
              event_type: str = "run.dispatch",
              now: datetime | None = None) -> list[OutboxClaim]:
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("invalid_limit")
        if type(lease_seconds) is not int or not 1 <= lease_seconds <= 3600:
            raise ValueError("invalid_lease")
        if not isinstance(event_type, str) or not 1 <= len(event_type) <= 40:
            raise ValueError("invalid_event_type")
        current = _now(now)
        lease_until = current + timedelta(seconds=lease_seconds)
        claims: list[OutboxClaim] = []
        with self.engine.begin() as connection:
            rows = connection.execute(select(outbox).where(
                outbox.c.event_type == event_type,
                outbox.c.status.in_(("pending", "sending")),
                outbox.c.available_at <= current,
            ).order_by(outbox.c.available_at, outbox.c.id).limit(limit)
                .with_for_update(skip_locked=True)).mappings().all()
            for row in rows:
                attempt = row["delivery_attempts"] + 1
                changed = connection.execute(update(outbox).where(
                    outbox.c.id == row["id"],
                    outbox.c.event_type == event_type,
                    outbox.c.status == row["status"],
                    outbox.c.delivery_attempts == row["delivery_attempts"],
                    outbox.c.available_at <= current,
                ).values(status="sending", delivery_attempts=attempt,
                         available_at=lease_until))
                if changed.rowcount != 1:
                    continue
                claims.append(OutboxClaim(
                    id=row["id"], event_key=row["event_key"],
                    run_id=row["run_id"], attempt_id=row["attempt_id"],
                    event_type=row["event_type"], payload=dict(row["payload_json"]),
                    delivery_attempts=attempt,
                ))
        return claims

    def mark_delivered(self, outbox_id: str, delivery_attempts: int,
                       *, now: datetime | None = None) -> bool:
        if not outbox_id or type(delivery_attempts) is not int or delivery_attempts < 1:
            raise ValueError("invalid_claim")
        with self.engine.begin() as connection:
            changed = connection.execute(update(outbox).where(
                outbox.c.id == outbox_id,
                outbox.c.status == "sending",
                outbox.c.delivery_attempts == delivery_attempts,
            ).values(status="delivered", delivered_at=_now(now)))
            return changed.rowcount == 1

    def release(self, outbox_id: str, delivery_attempts: int, *,
                retry_delay_seconds: int, now: datetime | None = None) -> bool:
        if not outbox_id or type(delivery_attempts) is not int or delivery_attempts < 1:
            raise ValueError("invalid_claim")
        if (type(retry_delay_seconds) is not int
                or not 0 <= retry_delay_seconds <= 86_400):
            raise ValueError("invalid_retry_delay")
        available_at = _now(now) + timedelta(seconds=retry_delay_seconds)
        with self.engine.begin() as connection:
            changed = connection.execute(update(outbox).where(
                outbox.c.id == outbox_id,
                outbox.c.status == "sending",
                outbox.c.delivery_attempts == delivery_attempts,
            ).values(status="pending", available_at=available_at))
            return changed.rowcount == 1
