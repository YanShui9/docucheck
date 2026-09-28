"""Publish leased Run events outside the database transaction."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Engine

from ..runtime.lifecycle import RunStatus
from .outbox_repository import OutboxClaim, OutboxRepository
from .run_repository import RepositoryError, RunRepository

PublishRun = Callable[[str, str, str], None]


@dataclass(frozen=True)
class DispatchSummary:
    claimed: int = 0
    published: int = 0
    skipped: int = 0
    retried: int = 0
    lease_lost: int = 0


def _valid_payload(claim: OutboxClaim) -> bool:
    return (claim.event_type == "run.dispatch"
            and claim.payload == {"run_id": claim.run_id,
                                  "attempt_id": claim.attempt_id}
            and claim.event_key == (
                f"run:{claim.run_id}:attempt:{claim.attempt_id}:dispatch"))


def dispatch_once(engine: Engine, publish: PublishRun, *, limit: int = 10,
                  lease_seconds: int = 60,
                  now: datetime | None = None) -> DispatchSummary:
    """A broker success followed by a lost acknowledgement may publish twice."""
    outbox = OutboxRepository(engine)
    runs = RunRepository(engine)
    claims = outbox.claim(limit=limit, lease_seconds=lease_seconds, now=now)
    published = skipped = retried = lease_lost = 0
    for claim in claims:
        if not _valid_payload(claim):
            if outbox.release(claim.id, claim.delivery_attempts,
                              retry_delay_seconds=300, now=now):
                retried += 1
            else:
                lease_lost += 1
            continue
        try:
            run = runs.get(claim.run_id)
        except RepositoryError as exc:
            if exc.code != "run_not_found":
                raise
            run = None
        if (run is None or run.status != RunStatus.QUEUED
                or run.active_attempt_id != claim.attempt_id):
            if outbox.mark_delivered(claim.id, claim.delivery_attempts, now=now):
                skipped += 1
            else:
                lease_lost += 1
            continue
        try:
            publish(claim.run_id, claim.attempt_id, claim.event_key)
        except Exception:  # noqa: BLE001 - broker error details may contain credentials
            delay = min(2 ** min(claim.delivery_attempts, 8), 300)
            if outbox.release(claim.id, claim.delivery_attempts,
                              retry_delay_seconds=delay, now=now):
                retried += 1
            else:
                lease_lost += 1
            continue
        if outbox.mark_delivered(claim.id, claim.delivery_attempts, now=now):
            published += 1
        else:
            lease_lost += 1
    return DispatchSummary(len(claims), published, skipped, retried, lease_lost)
