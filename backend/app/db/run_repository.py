"""Transactional Run persistence and compare-and-swap state transitions.

The API and worker may call this repository; it does not call a model or queue.
"""

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from uuid import uuid4

from sqlalchemy import Engine, func, insert, select, update
from sqlalchemy.exc import IntegrityError

from ..agent.diagnostics import safe_trace_events
from ..runtime.lifecycle import RunState, RunStatus
from .agent_snapshot import SnapshotLoadError, load_agent_snapshot
from .checklist_repository import ChecklistError, ensure_legacy_run_allowed
from .correction_recheck import CorrectionRecheck
from .correction_repository import CorrectionError
from .memory_repository import next_sequence, save_checkpoint, verify_profile
from .models import (
    agent_runs,
    cases,
    conversations,
    correction_reviews,
    corrections,
    idempotency_records,
    input_snapshots,
    messages,
    outbox,
    project_members,
    run_attempts,
    run_events,
)


class RepositoryError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class SubmitRun:
    case_id: str
    conversation_id: str
    snapshot_id: str
    expected_revision: int
    content: str
    mode: str
    idempotency_key: str
    requester_key: str | None = None
    workflow: str = "auto"


@dataclass(frozen=True)
class RunReceipt:
    run_id: str
    message_id: str
    attempt_id: str


@dataclass(frozen=True)
class AttemptReceipt:
    run_id: str
    attempt_id: str


def _id() -> str:
    return uuid4().hex


def _now() -> datetime:
    # MySQL DATETIME and the first migration store UTC without a zone suffix.
    return datetime.now(UTC).replace(tzinfo=None)


def _utc_naive(value: datetime | None) -> datetime:
    if value is None:
        return _now()
    if value.tzinfo is not None:
        return value.astimezone(UTC).replace(tzinfo=None)
    return value


def _hash_request(request: SubmitRun) -> str:
    payload = {"case_id": request.case_id,
               "conversation_id": request.conversation_id,
               "snapshot_id": request.snapshot_id,
               "expected_revision": request.expected_revision,
               "content": request.content, "mode": request.mode}
    if request.workflow != "auto":
        payload["workflow"] = request.workflow
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":")).encode("utf-8")
    return sha256(encoded).hexdigest()


def _hash_attempt(run_id: str, snapshot_id: str, revision: int,
                  operation: str, content: str | None = None) -> str:
    payload = {"run_id": run_id, "snapshot_id": snapshot_id,
               "revision": revision, "operation": operation}
    if content is not None:
        payload["content"] = content
    encoded = json.dumps(payload,
                         sort_keys=True, separators=(",", ":")).encode("utf-8")
    return sha256(encoded).hexdigest()


def _state(row) -> RunState:
    return RunState(row["id"], row["snapshot_id"], row["revision"],
                    RunStatus(row["status"]), row["active_attempt_id"],
                    row["fence"])


class RunRepository:
    def __init__(self, engine: Engine):
        self.engine = engine

    @staticmethod
    def _check_dynamic_binding(connection, run_id, *, legacy_case=None):
        from .assistant_repository import AssistantRepository
        from .dynamic_repository import DynamicRepository
        from .models import assistant_runs, dynamic_runs
        assistant = connection.execute(select(assistant_runs).where(assistant_runs.c.run_id == run_id).with_for_update()).mappings().first()
        if assistant is not None:
            AssistantRepository.validate_binding(connection, assistant)
        binding = connection.execute(select(dynamic_runs).where(dynamic_runs.c.run_id == run_id).with_for_update()).mappings().first()
        if binding is not None:
            DynamicRepository.validate_binding(connection, binding)
        elif legacy_case and assistant is None:
            try:
                ensure_legacy_run_allowed(connection, legacy_case)
            except ChecklistError as exc:
                raise RepositoryError(exc.code) from None

    def _read_run(self, connection, run_id: str):
        row = connection.execute(select(agent_runs).where(
            agent_runs.c.id == run_id)).mappings().first()
        if row is None:
            raise RepositoryError("run_not_found")
        return row

    def get(self, run_id: str) -> RunState:
        with self.engine.connect() as connection:
            return _state(self._read_run(connection, run_id))

    def is_current(self, run_id: str, attempt_id: str, fence: int) -> bool:
        with self.engine.connect() as connection:
            return connection.scalar(select(agent_runs.c.id).join(
                cases, cases.c.id == agent_runs.c.case_id,
            ).where(
                agent_runs.c.id == run_id,
                agent_runs.c.active_attempt_id == attempt_id,
                agent_runs.c.fence == fence,
                agent_runs.c.status == RunStatus.RUNNING.value,
                agent_runs.c.revision == cases.c.revision,
                cases.c.deletion_requested_at.is_(None),
            )) is not None

    def has_source_access(self, run_id: str, attempt_id: str, fence: int) -> bool:
        with self.engine.connect() as connection:
            return connection.scalar(select(agent_runs.c.id).join(cases,
                cases.c.id == agent_runs.c.case_id).where(
                    agent_runs.c.id == run_id, agent_runs.c.active_attempt_id == attempt_id,
                    agent_runs.c.fence == fence, agent_runs.c.status == RunStatus.RUNNING.value,
                    agent_runs.c.revision == cases.c.revision, cases.c.deletion_requested_at.is_(None),
                    (cases.c.project_id.is_(None) | select(project_members.c.project_id).where(
                        project_members.c.project_id == cases.c.project_id,
                        project_members.c.principal_key == agent_runs.c.requester_key).exists()))) is not None

    def _require_current_case(self, connection, run_id: str) -> None:
        run = self._read_run(connection, run_id)
        case = connection.execute(select(
            cases.c.revision, cases.c.deletion_requested_at,
        ).where(
            cases.c.id == run["case_id"]).with_for_update()).first()
        if case is not None and case.deletion_requested_at is not None:
            raise RepositoryError("case_deleting")
        if case is None or case.revision != run["revision"]:
            raise RepositoryError("snapshot_changed")

    def _append_event(self, connection, run_id: str, attempt_id: str,
                      kind: str, status: RunStatus, now: datetime) -> None:
        seq = connection.scalar(select(func.coalesce(func.max(run_events.c.seq), 0))
                                .where(run_events.c.run_id == run_id)) + 1
        connection.execute(insert(run_events).values(
            id=_id(), run_id=run_id, attempt_id=attempt_id, seq=seq,
            kind=kind, status=status.value, summary_json={}, created_at=now,
        ))

    def _dispatch(self, connection, run_id: str, attempt_id: str,
                  now: datetime) -> None:
        connection.execute(insert(outbox).values(
            id=_id(), event_key=f"run:{run_id}:attempt:{attempt_id}:dispatch",
            run_id=run_id, attempt_id=attempt_id, event_type="run.dispatch",
            payload_json={"run_id": run_id, "attempt_id": attempt_id},
            status="pending", delivery_attempts=0, available_at=now,
            created_at=now,
        ))

    def _replay(self, connection, scope: str, key: str,
                request_hash: str) -> RunReceipt | None:
        record = connection.execute(select(idempotency_records).where(
            idempotency_records.c.operation_scope == scope,
            idempotency_records.c.key == key,
        )).mappings().first()
        if record is None:
            return None
        if (record["request_hash"] != request_hash
                or record["resource_type"] != "run"):
            raise RepositoryError("idempotency_conflict")
        run = self._read_run(connection, record["resource_id"])
        attempt_id = connection.scalar(select(run_attempts.c.id).where(
            run_attempts.c.run_id == run["id"],
            run_attempts.c.attempt_no == 1,
        ))
        if attempt_id is None:
            raise RepositoryError("repository_invariant")
        return RunReceipt(run["id"], run["message_id"], attempt_id)

    def submit(self, request: SubmitRun) -> RunReceipt:
        if (not request.case_id or not request.conversation_id
                or not request.snapshot_id or not request.content.strip()
                or len(request.content) > 2000
                or not 1 <= len(request.idempotency_key) <= 255
                or request.mode not in {"standard", "thorough"}
                or request.expected_revision < (0 if request.workflow == "assistant" else 1)):
            raise RepositoryError("invalid_request")
        scope = f"case:{request.case_id}:message"
        if request.requester_key is not None:
            if not re.fullmatch(r"[a-f0-9]{64}", request.requester_key):
                raise RepositoryError("invalid_request")
            scope += f":actor:{request.requester_key}"
        if len(scope) > 128:
            raise RepositoryError("invalid_request")
        request_hash = _hash_request(request)
        with self.engine.connect() as connection:
            case = connection.execute(select(cases.c.deletion_requested_at).where(
                cases.c.id == request.case_id)).first()
            if case is not None and case.deletion_requested_at is not None:
                raise RepositoryError("case_deleting")
            replay = self._replay(connection, scope, request.idempotency_key,
                                  request_hash)
            if replay is not None:
                return replay
            correction_id = connection.scalar(select(corrections.c.id).where(
                corrections.c.to_snapshot_id == request.snapshot_id,
                corrections.c.case_id == request.case_id,
            ))
        if correction_id is not None:
            try:
                repo = load_agent_snapshot(self.engine, request.case_id,
                                           request.snapshot_id)
                CorrectionRecheck(self.engine).load_for_run(
                    request.case_id, request.snapshot_id, repo,
                )
            except (CorrectionError, SnapshotLoadError) as exc:
                raise RepositoryError(exc.code) from exc
        try:
            with self.engine.begin() as connection:
                existing = self._replay(connection, scope,
                                        request.idempotency_key, request_hash)
                if existing is not None:
                    return existing
                now = _now()
                run_id, message_id, attempt_id = _id(), _id(), _id()
                # The unique key serializes identical concurrent submissions.
                connection.execute(insert(idempotency_records).values(
                    id=_id(), operation_scope=scope, key=request.idempotency_key,
                    request_hash=request_hash, resource_type="run",
                    resource_id=run_id, created_at=now,
                ))
                case = connection.execute(select(
                    cases.c.revision, cases.c.deletion_requested_at,
                ).where(
                    cases.c.id == request.case_id).with_for_update()).first()
                if case is None:
                    raise RepositoryError("scope_mismatch")
                if case.deletion_requested_at is not None:
                    raise RepositoryError("case_deleting")
                if case.revision != request.expected_revision:
                    raise RepositoryError("revision_conflict")
                snapshot = connection.execute(select(input_snapshots.c.id).where(
                    input_snapshots.c.id == request.snapshot_id,
                    input_snapshots.c.case_id == request.case_id,
                    input_snapshots.c.revision == request.expected_revision,
                )).first()
                conversation = connection.execute(select(conversations.c.id).where(
                    conversations.c.id == request.conversation_id,
                    conversations.c.case_id == request.case_id,
                )).first()
                if snapshot is None or conversation is None:
                    raise RepositoryError("scope_mismatch")
                from .assistant_repository import bind_assistant
                from .dynamic_repository import bind_workflow
                assistant_binding = (bind_assistant(connection, request, run_id)
                                     if request.workflow == "assistant" else None)
                binding = (None if assistant_binding is not None else bind_workflow(connection, request, run_id))
                correction_id = connection.scalar(select(corrections.c.id).where(
                    corrections.c.to_snapshot_id == request.snapshot_id,
                ))
                if correction_id is not None and connection.scalar(select(
                        correction_reviews.c.correction_id,
                ).where(correction_reviews.c.correction_id == correction_id)) is None:
                    raise RepositoryError("correction_pending_recheck")
                busy = connection.scalar(select(agent_runs.c.id).where(
                    agent_runs.c.conversation_id == request.conversation_id,
                    agent_runs.c.snapshot_id == request.snapshot_id,
                    agent_runs.c.status.in_((RunStatus.QUEUED.value,
                                            RunStatus.RUNNING.value,
                                            RunStatus.WAITING_USER.value)),
                ).limit(1).with_for_update())
                if busy is not None:
                    raise RepositoryError("conversation_busy")
                connection.execute(insert(messages).values(
                    id=message_id, conversation_id=request.conversation_id,
                    sequence=next_sequence(connection, request.conversation_id),
                    role="user", content=request.content, created_at=now,
                ))
                connection.execute(insert(agent_runs).values(
                    requester_key=request.requester_key,
                    id=run_id, case_id=request.case_id,
                    conversation_id=request.conversation_id,
                    message_id=message_id, snapshot_id=request.snapshot_id,
                    revision=request.expected_revision, mode=request.mode,
                    status=RunStatus.QUEUED.value,
                    active_attempt_id=attempt_id, fence=0,
                    created_at=now, updated_at=now,
                ))
                if binding is not None:
                    from .models import dynamic_runs
                    connection.execute(insert(dynamic_runs).values(**binding))
                if assistant_binding is not None:
                    from .models import assistant_runs
                    connection.execute(insert(assistant_runs).values(**assistant_binding))
                connection.execute(insert(run_attempts).values(
                    id=attempt_id, run_id=run_id, attempt_no=1,
                    status=RunStatus.QUEUED.value, outcome_unknown=False,
                    created_at=now,
                ))
                self._append_event(connection, run_id, attempt_id,
                                   "queued", RunStatus.QUEUED, now)
                self._dispatch(connection, run_id, attempt_id, now)
                return RunReceipt(run_id, message_id, attempt_id)
        except IntegrityError:
            # A competing transaction may have won the idempotency key. Do not
            # misreport unrelated foreign-key/unique errors as a replay.
            with self.engine.connect() as connection:
                existing = self._replay(connection, scope,
                                        request.idempotency_key, request_hash)
                if existing is not None:
                    return existing
            raise

    def claim(self, run_id: str, attempt_id: str) -> RunState:
        now = _now()
        with self.engine.begin() as connection:
            self._require_current_case(connection, run_id)
            case_id = connection.scalar(select(agent_runs.c.case_id).where(agent_runs.c.id == run_id))
            self._check_dynamic_binding(connection, run_id, legacy_case=case_id)
            changed = connection.execute(update(agent_runs).where(
                agent_runs.c.id == run_id,
                agent_runs.c.active_attempt_id == attempt_id,
                agent_runs.c.status == RunStatus.QUEUED.value,
            ).values(status=RunStatus.RUNNING.value,
                     fence=agent_runs.c.fence + 1, updated_at=now))
            if changed.rowcount != 1:
                raise RepositoryError("stale_attempt")
            attempt = connection.execute(update(run_attempts).where(
                run_attempts.c.id == attempt_id,
                run_attempts.c.run_id == run_id,
                run_attempts.c.status == RunStatus.QUEUED.value,
            ).values(status=RunStatus.RUNNING.value, started_at=now))
            if attempt.rowcount != 1:
                raise RepositoryError("repository_invariant")
            self._append_event(connection, run_id, attempt_id,
                               "claimed", RunStatus.RUNNING, now)
            return _state(self._read_run(connection, run_id))

    def heartbeat(self, run_id: str, attempt_id: str, fence: int, *,
                  now: datetime | None = None) -> bool:
        """Refresh only the attempt that still owns a running Run."""
        current = _utc_naive(now)
        with self.engine.begin() as connection:
            changed = connection.execute(update(agent_runs).where(
                agent_runs.c.id == run_id,
                agent_runs.c.active_attempt_id == attempt_id,
                agent_runs.c.fence == fence,
                agent_runs.c.status == RunStatus.RUNNING.value,
                agent_runs.c.updated_at <= current,
            ).values(updated_at=current))
            return changed.rowcount == 1

    def recover_stale(self, *, now: datetime | None = None,
                      stale_after_seconds: int = 120,
                      limit: int = 100) -> int:
        """Fence lost workers and close queued Runs bound to old revisions."""
        if (type(stale_after_seconds) is not int
                or not 30 <= stale_after_seconds <= 86_400):
            raise ValueError("invalid_stale_after")
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("invalid_limit")
        current = _utc_naive(now)
        cutoff = current - timedelta(seconds=stale_after_seconds)
        recovered = 0
        with self.engine.begin() as connection:
            stale_queued = connection.execute(select(
                agent_runs.c.id, agent_runs.c.active_attempt_id,
                agent_runs.c.fence,
            ).join(cases, agent_runs.c.case_id == cases.c.id).where(
                agent_runs.c.status == RunStatus.QUEUED.value,
                agent_runs.c.revision != cases.c.revision,
                cases.c.deletion_requested_at.is_(None),
            ).order_by(agent_runs.c.created_at, agent_runs.c.id).limit(limit)
                .with_for_update(skip_locked=True)).mappings().all()
            for row in stale_queued:
                changed = connection.execute(update(agent_runs).where(
                    agent_runs.c.id == row["id"],
                    agent_runs.c.active_attempt_id == row["active_attempt_id"],
                    agent_runs.c.fence == row["fence"],
                    agent_runs.c.status == RunStatus.QUEUED.value,
                ).values(status=RunStatus.INTERRUPTED.value,
                         fence=agent_runs.c.fence + 1,
                         answer_text=None, error_code="snapshot_changed",
                         updated_at=current))
                if changed.rowcount != 1:
                    continue
                attempt = connection.execute(update(run_attempts).where(
                    run_attempts.c.id == row["active_attempt_id"],
                    run_attempts.c.run_id == row["id"],
                    run_attempts.c.status == RunStatus.QUEUED.value,
                ).values(status=RunStatus.INTERRUPTED.value,
                         finished_at=current, outcome_unknown=False))
                if attempt.rowcount != 1:
                    raise RepositoryError("repository_invariant")
                connection.execute(update(outbox).where(
                    outbox.c.run_id == row["id"],
                    outbox.c.attempt_id == row["active_attempt_id"],
                    outbox.c.status.in_(("pending", "sending")),
                ).values(status="cancelled"))
                self._append_event(connection, row["id"],
                                   row["active_attempt_id"], "interrupted",
                                   RunStatus.INTERRUPTED, current)
                recovered += 1
            rows = connection.execute(select(
                agent_runs.c.id, agent_runs.c.active_attempt_id,
                agent_runs.c.fence,
            ).where(
                agent_runs.c.status == RunStatus.RUNNING.value,
                agent_runs.c.updated_at <= cutoff,
            ).order_by(agent_runs.c.updated_at, agent_runs.c.id).limit(limit - recovered)
                .with_for_update(skip_locked=True)).mappings().all()
            for row in rows:
                changed = connection.execute(update(agent_runs).where(
                    agent_runs.c.id == row["id"],
                    agent_runs.c.active_attempt_id == row["active_attempt_id"],
                    agent_runs.c.fence == row["fence"],
                    agent_runs.c.status == RunStatus.RUNNING.value,
                    agent_runs.c.updated_at <= cutoff,
                ).values(status=RunStatus.INTERRUPTED.value,
                         fence=agent_runs.c.fence + 1,
                         answer_text=None, error_code="worker_lost",
                         updated_at=current))
                if changed.rowcount != 1:
                    continue
                attempt = connection.execute(update(run_attempts).where(
                    run_attempts.c.id == row["active_attempt_id"],
                    run_attempts.c.run_id == row["id"],
                    run_attempts.c.status == RunStatus.RUNNING.value,
                ).values(status=RunStatus.INTERRUPTED.value,
                         finished_at=current, outcome_unknown=True))
                if attempt.rowcount != 1:
                    raise RepositoryError("repository_invariant")
                self._append_event(connection, row["id"],
                                   row["active_attempt_id"], "interrupted",
                                   RunStatus.INTERRUPTED, current)
                recovered += 1
        return recovered

    def finish(self, run_id: str, attempt_id: str, fence: int,
               snapshot_id: str, status: RunStatus,
               answer_text: str | None = None,
               error_code: str | None = None,
               model_id: str | None = None,
               usage_json: dict | None = None,
               outcome_unknown: bool = False,
               memory_json: dict | None = None,
               trace_events: tuple[dict, ...] | list[dict] | None = None,
               context_manifest: dict | None = None,
               dynamic_payload: dict | None = None,
               assistant_payload: dict | None = None) -> RunState:
        if status not in {RunStatus.COMPLETED, RunStatus.PARTIAL,
                          RunStatus.FAILED, RunStatus.WAITING_USER}:
            raise RepositoryError("invalid_transition")
        now = _now()
        with self.engine.begin() as connection:
            self._require_current_case(connection, run_id)
            from .dynamic_repository import DynamicRepository
            DynamicRepository(self.engine).store_terminal(connection, run_id, dynamic_payload)
            from .assistant_repository import AssistantRepository
            AssistantRepository(self.engine).store_terminal(connection, run_id, assistant_payload)
            run = self._read_run(connection, run_id)
            project_id = connection.scalar(select(cases.c.project_id).where(cases.c.id == run["case_id"]))
            if project_id is not None and connection.scalar(select(project_members.c.role).where(
                    project_members.c.project_id == project_id,
                    project_members.c.principal_key == run["requester_key"]).with_for_update()) is None:
                raise RepositoryError("source_access_changed")
            if context_manifest is not None:
                from ..api.auth import Principal
                actor = run["requester_key"] or Principal("local", "local").key
                try:
                    verify_profile(connection, actor, context_manifest["preference_revision"],
                                   context_manifest["preference_fingerprint"], now=now)
                except (ValueError, KeyError, TypeError):
                    raise RepositoryError("memory_context_changed") from None
            changed = connection.execute(update(agent_runs).where(
                agent_runs.c.id == run_id,
                agent_runs.c.active_attempt_id == attempt_id,
                agent_runs.c.fence == fence,
                agent_runs.c.snapshot_id == snapshot_id,
                agent_runs.c.status == RunStatus.RUNNING.value,
            ).values(status=status.value, answer_text=answer_text,
                     error_code=error_code, memory_json=memory_json,
                     context_manifest_json=context_manifest,
                     updated_at=now))
            if changed.rowcount != 1:
                raise RepositoryError("stale_attempt")
            attempt = connection.execute(update(run_attempts).where(
                run_attempts.c.id == attempt_id,
                run_attempts.c.run_id == run_id,
                run_attempts.c.status == RunStatus.RUNNING.value,
            ).values(status=status.value, finished_at=now,
                     model_id=model_id, usage_json=usage_json,
                     outcome_unknown=outcome_unknown))
            if attempt.rowcount != 1:
                raise RepositoryError("repository_invariant")
            diagnostics = safe_trace_events(trace_events)
            if diagnostics:
                first_seq = connection.scalar(select(func.coalesce(func.max(run_events.c.seq), 0))
                                              .where(run_events.c.run_id == run_id)) + 1
                # The owning Run row is already locked by its successful CAS.
                # Diagnostics and terminal result commit or roll back together.
                connection.execute(insert(run_events), [{
                    "id": _id(), "run_id": run_id, "attempt_id": attempt_id,
                    "seq": first_seq + index, "kind": event["kind"],
                    "status": status.value, "summary_json": event["summary"],
                    "created_at": now,
                } for index, event in enumerate(diagnostics)])
            self._append_event(connection, run_id, attempt_id,
                               "finished", status, now)
            save_checkpoint(connection, run_id, now=now)
            return _state(self._read_run(connection, run_id))

    def interrupt(self, run_id: str, attempt_id: str, fence: int, *,
                  usage_json: dict | None = None, model_id: str | None = None,
                  context_manifest: dict | None = None, error_code: str | None = None) -> RunState:
        now = _now()
        with self.engine.begin() as connection:
            values = {"status": RunStatus.INTERRUPTED.value, "updated_at": now}
            if context_manifest is not None:
                values["context_manifest_json"] = context_manifest
            if error_code is not None:
                values["error_code"] = error_code
            changed = connection.execute(update(agent_runs).where(
                agent_runs.c.id == run_id,
                agent_runs.c.active_attempt_id == attempt_id,
                agent_runs.c.fence == fence,
                agent_runs.c.status == RunStatus.RUNNING.value,
            ).values(**values))
            if changed.rowcount != 1:
                raise RepositoryError("stale_attempt")
            attempt_values = {"status": RunStatus.INTERRUPTED.value, "finished_at": now}
            if usage_json is not None:
                attempt_values["usage_json"] = usage_json
                attempt_values["model_id"] = model_id
            connection.execute(update(run_attempts).where(
                run_attempts.c.id == attempt_id,
                run_attempts.c.run_id == run_id,
            ).values(**attempt_values))
            self._append_event(connection, run_id, attempt_id,
                               "interrupted", RunStatus.INTERRUPTED, now)
            return _state(self._read_run(connection, run_id))

    def cancel(self, run_id: str) -> RunState:
        now = _now()
        with self.engine.begin() as connection:
            row = self._read_run(connection, run_id)
            if row["status"] not in {RunStatus.QUEUED.value,
                                     RunStatus.RUNNING.value,
                                     RunStatus.WAITING_USER.value,
                                     RunStatus.INTERRUPTED.value}:
                raise RepositoryError("invalid_transition")
            changed = connection.execute(update(agent_runs).where(
                agent_runs.c.id == run_id,
                agent_runs.c.status == row["status"],
                agent_runs.c.active_attempt_id == row["active_attempt_id"],
                agent_runs.c.fence == row["fence"],
            ).values(status=RunStatus.CANCELLED.value, updated_at=now))
            if changed.rowcount != 1:
                raise RepositoryError("stale_attempt")
            connection.execute(update(run_attempts).where(
                run_attempts.c.id == row["active_attempt_id"],
                run_attempts.c.run_id == run_id,
            ).values(status=RunStatus.CANCELLED.value, finished_at=now))
            self._append_event(connection, run_id, row["active_attempt_id"],
                               "cancelled", RunStatus.CANCELLED, now)
            return _state(self._read_run(connection, run_id))

    def _replay_attempt(self, connection, scope: str, key: str,
                        request_hash: str, run_id: str) -> AttemptReceipt | None:
        record = connection.execute(select(idempotency_records).where(
            idempotency_records.c.operation_scope == scope,
            idempotency_records.c.key == key,
        )).mappings().first()
        if record is None:
            return None
        if (record["request_hash"] != request_hash
                or record["resource_type"] != "attempt"):
            raise RepositoryError("idempotency_conflict")
        attempt = connection.execute(select(run_attempts.c.id).where(
            run_attempts.c.id == record["resource_id"],
            run_attempts.c.run_id == run_id,
        )).first()
        if attempt is None:
            raise RepositoryError("repository_invariant")
        return AttemptReceipt(run_id, record["resource_id"])

    def _new_attempt(self, run_id: str, snapshot_id: str, revision: int,
                     idempotency_key: str, operation: str,
                     allowed: set[RunStatus],
                     content: str | None = None, requester_key: str | None = None) -> AttemptReceipt:
        if not run_id or not snapshot_id or revision < 0 or not (
                1 <= len(idempotency_key) <= 255):
            raise RepositoryError("invalid_request")
        if operation == "resume" and (content is None or not content.strip()
                                      or len(content) > 2000):
            raise RepositoryError("invalid_request")
        scope = f"run:{run_id}:{operation}"
        if requester_key is not None:
            if not re.fullmatch(r"[a-f0-9]{64}", requester_key):
                raise RepositoryError("invalid_request")
            scope += f":actor:{requester_key}"
        request_hash = _hash_attempt(run_id, snapshot_id, revision, operation,
                                     content)
        try:
            with self.engine.begin() as connection:
                row = self._read_run(connection, run_id)
                if revision == 0:
                    from .models import assistant_runs
                    if connection.scalar(select(assistant_runs.c.run_id).where(assistant_runs.c.run_id == run_id).with_for_update()) is None:
                        raise RepositoryError("invalid_request")
                case = connection.execute(select(
                    cases.c.revision, cases.c.deletion_requested_at,
                ).where(cases.c.id == row["case_id"]).with_for_update()).first()
                if case is not None and case.deletion_requested_at is not None:
                    raise RepositoryError("case_deleting")
                existing = self._replay_attempt(
                    connection, scope, idempotency_key, request_hash, run_id,
                )
                if existing is not None:
                    return existing
                now, attempt_id = _now(), _id()
                connection.execute(insert(idempotency_records).values(
                    id=_id(), operation_scope=scope, key=idempotency_key,
                    request_hash=request_hash, resource_type="attempt",
                    resource_id=attempt_id, created_at=now,
                ))
                if (snapshot_id != row["snapshot_id"] or revision != row["revision"]
                        or case is None or case.revision != revision):
                    raise RepositoryError("snapshot_changed")
                self._check_dynamic_binding(connection, run_id, legacy_case=row["case_id"])
                if row["status"] not in {item.value for item in allowed}:
                    raise RepositoryError("invalid_transition")
                busy = connection.scalar(select(agent_runs.c.id).where(
                    agent_runs.c.conversation_id == row["conversation_id"],
                    agent_runs.c.snapshot_id == snapshot_id,
                    agent_runs.c.id != run_id,
                    agent_runs.c.status.in_((RunStatus.QUEUED.value,
                                            RunStatus.RUNNING.value,
                                            RunStatus.WAITING_USER.value)),
                ).limit(1).with_for_update())
                if busy is not None:
                    raise RepositoryError("conversation_busy")
                changed = connection.execute(update(agent_runs).where(
                    agent_runs.c.id == run_id,
                    agent_runs.c.status == row["status"],
                    agent_runs.c.active_attempt_id == row["active_attempt_id"],
                    agent_runs.c.fence == row["fence"],
                    agent_runs.c.snapshot_id == snapshot_id,
                    agent_runs.c.revision == revision,
                ).values(status=RunStatus.QUEUED.value,
                         requester_key=requester_key if requester_key is not None else row["requester_key"],
                         active_attempt_id=attempt_id, answer_text=None,
                         error_code=None, updated_at=now))
                if changed.rowcount != 1:
                    raise RepositoryError("stale_attempt")
                attempt_no = connection.scalar(select(func.max(run_attempts.c.attempt_no))
                                               .where(run_attempts.c.run_id == run_id)) + 1
                resume_message_id = None
                if operation == "resume":
                    resume_message_id = _id()
                    connection.execute(insert(messages).values(
                        id=resume_message_id,
                        conversation_id=row["conversation_id"],
                        sequence=next_sequence(connection, row["conversation_id"]),
                        role="user", content=content, created_at=now))
                connection.execute(insert(run_attempts).values(
                    id=attempt_id, run_id=run_id, attempt_no=attempt_no,
                    status=RunStatus.QUEUED.value, outcome_unknown=False,
                    resume_message_id=resume_message_id, created_at=now,
                ))
                self._append_event(connection, run_id, attempt_id,
                                   "queued", RunStatus.QUEUED, now)
                self._dispatch(connection, run_id, attempt_id, now)
                return AttemptReceipt(run_id, attempt_id)
        except IntegrityError:
            with self.engine.connect() as connection:
                existing = self._replay_attempt(
                    connection, scope, idempotency_key, request_hash, run_id,
                )
                if existing is not None:
                    return existing
            raise

    def retry(self, run_id: str, snapshot_id: str, revision: int,
              idempotency_key: str, *, requester_key: str | None = None) -> AttemptReceipt:
        return self._new_attempt(run_id, snapshot_id, revision, idempotency_key,
                                 "retry", {RunStatus.INTERRUPTED, RunStatus.FAILED,
                                           RunStatus.PARTIAL}, requester_key=requester_key)

    def resume(self, run_id: str, snapshot_id: str, revision: int,
               idempotency_key: str, content: str, *, requester_key: str | None = None) -> AttemptReceipt:
        return self._new_attempt(run_id, snapshot_id, revision, idempotency_key,
                                 "resume", {RunStatus.WAITING_USER}, content, requester_key)
