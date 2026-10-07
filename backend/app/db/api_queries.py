"""Case, conversation and Run reads used by the first HTTP endpoints."""

from dataclasses import asdict
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

from sqlalchemy import Engine, func, insert, select

from ..agent.fact_memory import decode_fact_sets
from ..agent.session_memory import (
    LEGACY_FIELD_EVALUATORS,
    MAX_RESULTS,
    _binding_fingerprint,
    _repository_fingerprint,
    _result,
    verify_check_result,
)
from ..field_contract import FieldFactSet, FieldKey
from .agent_snapshot import SnapshotLoadError, load_agent_snapshot
from .models import (
    agent_runs,
    cases,
    conversations,
    correction_reviews,
    corrections,
    input_snapshots,
    messages,
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


def _public_usage(value: object) -> dict[str, int] | None:
    if not isinstance(value, dict):
        return None
    keys = {"decisions", "model_calls", "tools", "input_reserved", "output_reserved",
            "actual_input", "actual_output", "unknown_usage_calls"}
    return {key: count for key, count in value.items()
            if key in keys and type(count) is int and 0 <= count <= 10**12}


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
            case = connection.execute(select(
                cases.c.id, cases.c.deletion_requested_at,
            ).where(cases.c.id == case_id).with_for_update()).first()
            if case is None:
                raise ApiQueryError("not_found")
            if case.deletion_requested_at is not None:
                raise ApiQueryError("case_deleting")
            connection.execute(insert(conversations).values(
                id=conversation_id, case_id=case_id, created_at=now,
            ))
        return {"id": conversation_id, "case_id": case_id,
                "created_at": _utc(now)}

    def list_conversations(self, case_id: str, offset: int, limit: int) -> dict:
        with self.engine.connect() as connection:
            if connection.scalar(select(cases.c.id).where(cases.c.id == case_id)) is None:
                raise ApiQueryError("not_found")
            rows = connection.execute(select(conversations).where(
                conversations.c.case_id == case_id,
            ).order_by(conversations.c.created_at, conversations.c.id)
                .offset(offset).limit(limit + 1)).mappings().all()
        return {
            "case_id": case_id,
            "conversations": [
                {"id": row["id"], "case_id": row["case_id"],
                 "created_at": _utc(row["created_at"])}
                for row in rows[:limit]
            ],
            "next_offset": offset + limit if len(rows) > limit else None,
        }

    def list_messages(self, conversation_id: str, offset: int, limit: int) -> dict:
        with self.engine.connect() as connection:
            if connection.scalar(select(conversations.c.id).where(
                    conversations.c.id == conversation_id)) is None:
                raise ApiQueryError("not_found")
            total = connection.scalar(select(func.count()).select_from(messages).where(
                messages.c.conversation_id == conversation_id))
            rows = connection.execute(select(messages).where(
                messages.c.conversation_id == conversation_id,
            ).order_by(messages.c.created_at, messages.c.id)
                .offset(offset).limit(limit + 1)).mappings().all()
            page = rows[:limit]
            message_ids = [row["id"] for row in page]
            run_ids: dict[str, str] = {}
            if message_ids:
                run_ids.update(connection.execute(select(
                    agent_runs.c.message_id, agent_runs.c.id,
                ).where(agent_runs.c.message_id.in_(message_ids))).all())
                run_ids.update(connection.execute(select(
                    run_attempts.c.resume_message_id, run_attempts.c.run_id,
                ).where(run_attempts.c.resume_message_id.in_(message_ids))).all())
        return {
            "conversation_id": conversation_id,
            "total": int(total or 0),
            "messages": [
                {"id": row["id"], "role": row["role"],
                 "content": row["content"], "run_id": run_ids.get(row["id"]),
                 "created_at": _utc(row["created_at"])}
                for row in page
            ],
            "next_offset": offset + limit if len(rows) > limit else None,
        }

    def list_case_runs(self, case_id: str, snapshot_id: str | None,
                       offset: int, limit: int) -> dict:
        with self.engine.connect() as connection:
            if connection.scalar(select(cases.c.id).where(cases.c.id == case_id)) is None:
                raise ApiQueryError("not_found")
            query = select(agent_runs, messages.c.content.label("question")).join(
                messages, agent_runs.c.message_id == messages.c.id,
            ).where(agent_runs.c.case_id == case_id)
            if snapshot_id is not None:
                query = query.where(agent_runs.c.snapshot_id == snapshot_id)
            rows = connection.execute(query.order_by(
                agent_runs.c.created_at.desc(), agent_runs.c.id.desc(),
            ).offset(offset).limit(limit + 1)).mappings().all()
        return {"case_id": case_id, "runs": [{
            "id": row["id"], "conversation_id": row["conversation_id"],
            "snapshot_id": row["snapshot_id"], "revision": row["revision"],
            "mode": row["mode"], "status": row["status"],
            "question": row["question"], "answer_text": row["answer_text"],
            "error_code": row["error_code"], "created_at": _utc(row["created_at"]),
            "updated_at": _utc(row["updated_at"]),
        } for row in rows[:limit]],
                "next_offset": offset + limit if len(rows) > limit else None}

    def snapshot_for_conversation(self, conversation_id: str,
                                  expected_revision: int, *, allow_empty: bool = False) -> tuple[str, str]:
        with self.engine.begin() as connection:
            row = connection.execute(select(
                conversations.c.case_id, cases.c.revision,
                cases.c.deletion_requested_at,
            ).join(cases, conversations.c.case_id == cases.c.id).where(
                conversations.c.id == conversation_id,
            ).with_for_update()).first()
            if row is None:
                raise ApiQueryError("not_found")
            if row.deletion_requested_at is not None:
                raise ApiQueryError("case_deleting")
            if row.revision == 0 and not allow_empty:
                raise ApiQueryError("snapshot_not_ready")
            snapshot_id = connection.scalar(select(input_snapshots.c.id).where(
                input_snapshots.c.case_id == row.case_id,
                input_snapshots.c.revision == expected_revision,
            ).with_for_update())
            if snapshot_id is None and allow_empty and expected_revision == row.revision == 0:
                from ..evidence.repository import digest
                snapshot_id = digest(["assistant-empty-v1", row.case_id])
                connection.execute(insert(input_snapshots).values(id=snapshot_id, case_id=row.case_id,
                    revision=0, created_at=_now()))
            if snapshot_id is None:
                raise ApiQueryError("revision_conflict" if row.revision !=
                                    expected_revision else "snapshot_not_ready")
            correction_id = connection.scalar(select(corrections.c.id).where(
                corrections.c.to_snapshot_id == snapshot_id,
            ))
            if correction_id is not None and connection.scalar(select(
                    correction_reviews.c.correction_id,
            ).where(correction_reviews.c.correction_id == correction_id)) is None:
                raise ApiQueryError("correction_pending_recheck")
            return row.case_id, snapshot_id

    def get_run(self, run_id: str) -> dict:
        from .assistant_repository import AssistantRepository
        from .dynamic_repository import DynamicRepository
        from .models import dynamic_runs
        with self.engine.connect() as connection:
            row = connection.execute(select(
                agent_runs, cases.c.revision.label("current_revision"),
                run_attempts.c.outcome_unknown.label("active_outcome_unknown"),
                run_attempts.c.model_id.label("active_model_id"),
                run_attempts.c.usage_json.label("active_usage"),
            ).join(cases, agent_runs.c.case_id == cases.c.id).join(
                run_attempts, agent_runs.c.active_attempt_id == run_attempts.c.id,
            ).where(
                agent_runs.c.id == run_id,
            )).mappings().first()
            if row is None:
                raise ApiQueryError("not_found")
            correction = connection.execute(select(
                corrections.c.id, correction_reviews.c.review_status,
            ).outerjoin(correction_reviews,
                        correction_reviews.c.correction_id == corrections.c.id).where(
                corrections.c.to_snapshot_id == row["snapshot_id"],
            )).first()
            outgoing_correction_id = connection.scalar(select(corrections.c.id).where(
                corrections.c.source_run_id == run_id,
            ).order_by(corrections.c.created_at.desc()).limit(1))
            binding = connection.execute(select(dynamic_runs).where(dynamic_runs.c.run_id == run_id)).mappings().first()
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
                "model_id": row["active_model_id"],
                "usage": _public_usage(row["active_usage"]),
                "is_current": row["revision"] == row["current_revision"] and DynamicRepository(self.engine).current(run_id) and AssistantRepository(self.engine).current(run_id),
                "checklist_version_id": binding["checklist_version_id"] if binding else None,
                "workflow": ("assistant" if AssistantRepository(self.engine).exists(run_id) else
                             binding["workflow"] if binding else "legacy"),
                "correction_id": correction.id if correction is not None else None,
                "outgoing_correction_id": outgoing_correction_id,
                "correction_review_status": (correction.review_status
                                             if correction is not None else None),
                "created_at": _utc(row["created_at"]),
                "updated_at": _utc(row["updated_at"]),
            }

    def get_run_checks(self, run_id: str) -> dict:
        """Expose verified structured checks without promoting incomplete Runs."""
        from .dynamic_repository import DynamicRepository
        dynamic = DynamicRepository(self.engine)
        if dynamic.exists(run_id):
            return dynamic.checks(run_id)
        from .assistant_repository import AssistantRepository
        assistant = AssistantRepository(self.engine)
        if assistant.exists(run_id):
            return assistant.checks(run_id)
        with self.engine.connect() as connection:
            row = connection.execute(select(
                agent_runs.c.case_id, agent_runs.c.snapshot_id,
                agent_runs.c.revision, agent_runs.c.status,
                agent_runs.c.memory_json, cases.c.revision.label("current_revision"),
                cases.c.deletion_requested_at,
            ).join(cases, agent_runs.c.case_id == cases.c.id).where(
                agent_runs.c.id == run_id,
            )).mappings().first()
            if row is None:
                raise ApiQueryError("not_found")
            if row["deletion_requested_at"] is not None:
                raise ApiQueryError("case_deleting")
        payload = {"run_id": run_id, "case_id": row["case_id"],
                   "snapshot_id": row["snapshot_id"], "revision": row["revision"],
                   "run_status": row["status"],
                   "is_current": row["revision"] == row["current_revision"],
                   "checks": []}
        memory = row["memory_json"]
        if memory is None or row["status"] not in {"completed", "partial", "waiting_user"}:
            return payload
        if (not isinstance(memory, dict) or memory.get("version") not in {1, 2, 3}
                or memory.get("snapshot_id") != row["snapshot_id"]
                or not isinstance(memory.get("check_results"), list)
                or len(memory["check_results"]) > MAX_RESULTS):
            raise ApiQueryError("memory_scope")
        try:
            repo = load_agent_snapshot(self.engine, row["case_id"], row["snapshot_id"])
            if memory.get("repository_fingerprint") != _repository_fingerprint(repo):
                raise ValueError("memory_scope")
            if memory["version"] == 3 and memory.get("binding_fingerprint") != _binding_fingerprint(repo):
                raise ValueError("memory_scope")
            session = SimpleNamespace(repo=repo)
            results = [_result(session, value, allow_legacy=True) for value in memory["check_results"]]
            if len({result.check_result_id for result in results}) != len(results):
                raise ValueError("memory_scope")
            verified_result_ids = set()
            if memory["version"] in {2, 3}:
                available = memory.get("fact_sets_available")
                encoded = memory.get("fact_sets")
                if type(available) is not bool or not isinstance(encoded, list):
                    raise ValueError("memory_scope")
                if not available and encoded:
                    raise ValueError("memory_scope")
                fact_sets = decode_fact_sets(repo, encoded) if available else {}
                by_pair = {(item.supplier, item.field_key if isinstance(
                    item, FieldFactSet) else FieldKey.MEMORY): item
                           for item in fact_sets.values()}
                for result in results:
                    if not available:
                        continue
                    pair = (result.supplier, FieldKey(result.field_key))
                    fact_set = by_pair.get(pair)
                    verify_check_result(repo, result, fact_set, allow_legacy=True)
                    if (result.processing_status == "complete"
                            and result.rule_version not in LEGACY_FIELD_EVALUATORS):
                        verified_result_ids.add(result.check_result_id)
        except (SnapshotLoadError, TypeError, ValueError) as exc:
            raise ApiQueryError("memory_scope") from exc
        # Re-read in a fresh transaction after the potentially slow source/rule
        # verification. A Case revision or deletion can commit while it runs.
        with self.engine.connect() as connection:
            latest = connection.execute(select(
                agent_runs.c.snapshot_id, agent_runs.c.revision,
                agent_runs.c.status, agent_runs.c.memory_json,
                cases.c.revision.label("current_revision"),
                cases.c.deletion_requested_at,
            ).join(cases, agent_runs.c.case_id == cases.c.id).where(
                agent_runs.c.id == run_id,
            )).mappings().first()
            if latest is None:
                raise ApiQueryError("not_found")
            if latest["deletion_requested_at"] is not None:
                raise ApiQueryError("case_deleting")
            if (latest["snapshot_id"] != row["snapshot_id"]
                    or latest["revision"] != row["revision"]
                    or latest["status"] != row["status"]
                    or latest["memory_json"] != memory):
                raise ApiQueryError("run_changed")
            correction = connection.execute(select(
                correction_reviews.c.review_status,
            ).join(corrections,
                   corrections.c.id == correction_reviews.c.correction_id).where(
                corrections.c.to_snapshot_id == row["snapshot_id"],
            )).first()
        current = row["revision"] == latest["current_revision"]
        payload["is_current"] = current
        review_status = correction.review_status if correction else None
        payload["checks"] = [{
            **asdict(result),
            "is_formal": (row["status"] == "completed" and current
                          and result.processing_status == "complete"
                          and result.check_result_id in verified_result_ids
                          and review_status != "needs_review"),
        } for result in results]
        return payload

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
