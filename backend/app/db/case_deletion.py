"""Atomically fence work when a Case is marked for later cleanup."""

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import Engine, func, insert, select, update

from .models import (
    agent_runs,
    cases,
    document_versions,
    outbox,
    run_attempts,
    run_events,
)


class CaseDeletionError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _utc(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.replace(tzinfo=UTC).isoformat().replace("+00:00", "Z")


def _receipt(case_id: str, revision: int, requested_at: datetime | None,
             completed_at: datetime | None = None,
             error_code: str | None = None) -> dict:
    return {
        "case_id": case_id,
        "revision": revision,
        "status": ("deleted" if completed_at is not None else
                   "deleting" if requested_at is not None else "active"),
        "requested_at": _utc(requested_at),
        "completed_at": _utc(completed_at),
        "error_code": error_code,
    }


class CaseDeletion:
    def __init__(self, engine: Engine):
        self.engine = engine

    def get(self, case_id: str) -> dict:
        with self.engine.connect() as connection:
            case = connection.execute(select(cases).where(
                cases.c.id == case_id)).mappings().first()
        if case is None:
            raise CaseDeletionError("not_found")
        return _receipt(case_id, case["revision"],
                        case["deletion_requested_at"],
                        case["deletion_completed_at"],
                        case["deletion_error_code"])

    def request(self, case_id: str, expected_revision: int) -> dict:
        if not case_id or type(expected_revision) is not int or expected_revision < 0:
            raise CaseDeletionError("invalid_request")
        with self.engine.begin() as connection:
            case = connection.execute(select(cases).where(
                cases.c.id == case_id).with_for_update()).mappings().first()
            if case is None:
                raise CaseDeletionError("not_found")
            if case["deletion_requested_at"] is not None:
                return _receipt(case_id, case["revision"],
                                case["deletion_requested_at"],
                                case["deletion_completed_at"],
                                case["deletion_error_code"])
            if case["revision"] != expected_revision:
                raise CaseDeletionError("revision_conflict")

            now = _now()
            connection.execute(update(cases).where(
                cases.c.id == case_id,
                cases.c.deletion_requested_at.is_(None),
            ).values(deletion_requested_at=now, updated_at=now))
            active = connection.execute(select(
                agent_runs.c.id, agent_runs.c.active_attempt_id,
                agent_runs.c.status,
            ).where(
                agent_runs.c.case_id == case_id,
                agent_runs.c.status.in_(("queued", "running", "waiting_user",
                                        "interrupted")),
            ).order_by(agent_runs.c.id).with_for_update()).all()
            for run in active:
                connection.execute(update(agent_runs).where(
                    agent_runs.c.id == run.id,
                ).values(status="cancelled", fence=agent_runs.c.fence + 1,
                         answer_text=None, error_code="case_deleting",
                         updated_at=now))
                values = {"status": "cancelled", "finished_at": now}
                if run.status == "running":
                    values["outcome_unknown"] = True
                connection.execute(update(run_attempts).where(
                    run_attempts.c.id == run.active_attempt_id,
                    run_attempts.c.run_id == run.id,
                ).values(**values))
                seq = connection.scalar(select(func.coalesce(func.max(
                    run_events.c.seq), 0)).where(run_events.c.run_id == run.id)) + 1
                connection.execute(insert(run_events).values(
                    id=uuid4().hex, run_id=run.id,
                    attempt_id=run.active_attempt_id, seq=seq,
                    kind="case_delete_requested", status="cancelled",
                    summary_json={}, created_at=now,
                ))
            run_ids = select(agent_runs.c.id).where(
                agent_runs.c.case_id == case_id)
            connection.execute(update(outbox).where(
                outbox.c.run_id.in_(run_ids),
                outbox.c.status.in_(("pending", "sending")),
            ).values(status="cancelled"))
            connection.execute(update(document_versions).where(
                document_versions.c.case_id == case_id,
                document_versions.c.parse_status.in_(("pending", "processing")),
            ).values(parse_status="cancelled", parse_claim_id=None,
                     parse_lease_until=None))
            return _receipt(case_id, case["revision"], now)
