"""Case, conversation and Run reads used by the first HTTP endpoints."""

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import Engine, insert, select

from .models import (
    agent_runs,
    cases,
    conversations,
    input_snapshots,
    run_attempts,
    run_events,
)


class ApiQueryError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _utc(value: datetime) -> str:
    return value.replace(tzinfo=UTC).isoformat().replace("+00:00", "Z")


class ApiQueries:
    def __init__(self, engine: Engine):
        self.engine = engine

    def create_case(self) -> dict:
        case_id, now = uuid4().hex, _now()
        with self.engine.begin() as connection:
            connection.execute(insert(cases).values(
                id=case_id, revision=0, created_at=now, updated_at=now,
            ))
        return {"id": case_id, "revision": 0, "created_at": _utc(now)}

    def create_conversation(self, case_id: str) -> dict:
        conversation_id, now = uuid4().hex, _now()
        with self.engine.begin() as connection:
            exists = connection.scalar(select(cases.c.id).where(cases.c.id == case_id))
            if exists is None:
                raise ApiQueryError("not_found")
            connection.execute(insert(conversations).values(
                id=conversation_id, case_id=case_id, created_at=now,
            ))
        return {"id": conversation_id, "case_id": case_id,
                "created_at": _utc(now)}

    def snapshot_for_conversation(self, conversation_id: str,
                                  expected_revision: int) -> tuple[str, str]:
        with self.engine.connect() as connection:
            row = connection.execute(select(
                conversations.c.case_id, cases.c.revision,
            ).join(cases, conversations.c.case_id == cases.c.id).where(
                conversations.c.id == conversation_id,
            )).first()
            if row is None:
                raise ApiQueryError("not_found")
            if row.revision == 0:
                raise ApiQueryError("snapshot_not_ready")
            snapshot_id = connection.scalar(select(input_snapshots.c.id).where(
                input_snapshots.c.case_id == row.case_id,
                input_snapshots.c.revision == expected_revision,
            ))
            if snapshot_id is None:
                raise ApiQueryError("revision_conflict" if row.revision !=
                                    expected_revision else "snapshot_not_ready")
            return row.case_id, snapshot_id

    def get_run(self, run_id: str) -> dict:
        with self.engine.connect() as connection:
            row = connection.execute(select(
                agent_runs, cases.c.revision.label("current_revision"),
                run_attempts.c.outcome_unknown.label("active_outcome_unknown"),
            ).join(cases, agent_runs.c.case_id == cases.c.id).join(
                run_attempts, agent_runs.c.active_attempt_id == run_attempts.c.id,
            ).where(
                agent_runs.c.id == run_id,
            )).mappings().first()
            if row is None:
                raise ApiQueryError("not_found")
            return {
                "id": row["id"], "case_id": row["case_id"],
                "conversation_id": row["conversation_id"],
                "message_id": row["message_id"],
                "snapshot_id": row["snapshot_id"], "revision": row["revision"],
                "mode": row["mode"], "status": row["status"],
                "active_attempt_id": row["active_attempt_id"],
                "answer_text": row["answer_text"],
                "error_code": row["error_code"],
                "outcome_unknown": row["active_outcome_unknown"],
                "is_current": row["revision"] == row["current_revision"],
                "created_at": _utc(row["created_at"]),
                "updated_at": _utc(row["updated_at"]),
            }

    def events(self, run_id: str, after_seq: int, limit: int = 100) -> dict:
        with self.engine.connect() as connection:
            exists = connection.scalar(select(agent_runs.c.id).where(
                agent_runs.c.id == run_id,
            ))
            if exists is None:
                raise ApiQueryError("not_found")
            rows = connection.execute(select(run_events).where(
                run_events.c.run_id == run_id,
                run_events.c.seq > after_seq,
            ).order_by(run_events.c.seq).limit(limit)).mappings().all()
            events = [{"seq": row["seq"], "attempt_id": row["attempt_id"],
                       "kind": row["kind"], "status": row["status"],
                       "summary": row["summary_json"],
                       "created_at": _utc(row["created_at"])} for row in rows]
            return {"run_id": run_id, "events": events,
                    "next_seq": events[-1]["seq"] if events else after_seq}
