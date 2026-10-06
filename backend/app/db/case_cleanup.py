"""Retryable Case cleanup with a durable file ledger and leased ownership."""

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from sqlalchemy import Engine, delete, func, insert, or_, select, update

from .models import (
    agent_runs,
    assistant_runs,
    case_cleanup_files,
    cases,
    checklist_heads,
    checklist_operations,
    checklist_versions,
    conversation_summaries,
    conversations,
    correction_reviews,
    corrections,
    document_versions,
    dynamic_batches,
    dynamic_corrections,
    dynamic_runs,
    evidence_blocks,
    idempotency_records,
    input_snapshots,
    messages,
    outbox,
    run_attempts,
    run_events,
    suppliers,
    task_memory,
)

_MANAGED_KEY = re.compile(r"uploads/[0-9a-f]{2}/[0-9a-f]{32}\.(?:pdf|xlsx)\Z")


class CleanupError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class CleanupClaim:
    case_id: str
    claim_id: str


def _now(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(UTC).replace(tzinfo=None)
    return value.astimezone(UTC).replace(tzinfo=None) if value.tzinfo else value


class CaseCleanup:
    def __init__(self, engine: Engine, data_root: Path):
        self.engine = engine
        self.data_root = data_root.resolve()

    def claim(self, *, case_id: str | None = None,
              lease_seconds: int = 300,
              now: datetime | None = None) -> CleanupClaim | None:
        if type(lease_seconds) is not int or not 30 <= lease_seconds <= 3600:
            raise ValueError("invalid_cleanup_lease")
        current = _now(now)
        with self.engine.begin() as connection:
            statement = select(cases.c.id).where(
                cases.c.deletion_requested_at.is_not(None),
                cases.c.deletion_completed_at.is_(None),
                or_(cases.c.deletion_lease_until.is_(None),
                    cases.c.deletion_lease_until <= current),
            )
            if case_id is not None:
                statement = statement.where(cases.c.id == case_id)
            row = connection.execute(statement.order_by(
                cases.c.deletion_requested_at, cases.c.id,
            ).limit(1).with_for_update(skip_locked=True)).first()
            if row is None:
                return None
            claim_id = uuid4().hex
            changed = connection.execute(update(cases).where(
                cases.c.id == row.id,
                cases.c.deletion_completed_at.is_(None),
                or_(cases.c.deletion_lease_until.is_(None),
                    cases.c.deletion_lease_until <= current),
            ).values(deletion_claim_id=claim_id,
                     deletion_lease_until=current + timedelta(seconds=lease_seconds),
                     deletion_attempts=cases.c.deletion_attempts + 1,
                     deletion_error_code=None))
            return CleanupClaim(row.id, claim_id) if changed.rowcount == 1 else None

    @staticmethod
    def _require_claim(connection, claim: CleanupClaim,
                       now: datetime | None) -> None:
        current = _now(now)
        row = connection.execute(select(
            cases.c.deletion_claim_id, cases.c.deletion_lease_until,
            cases.c.deletion_completed_at,
        ).where(cases.c.id == claim.case_id).with_for_update()).first()
        if (row is None or row.deletion_claim_id != claim.claim_id
                or row.deletion_completed_at is not None
                or row.deletion_lease_until is None
                or row.deletion_lease_until <= current):
            raise CleanupError("stale_cleanup_claim")

    def _prepare(self, claim: CleanupClaim, now: datetime | None) -> None:
        with self.engine.begin() as connection:
            self._require_claim(connection, claim, now)
            current = _now(now)
            connection.execute(update(cases).where(
                cases.c.id == claim.case_id,
                cases.c.deletion_cleanup_started_at.is_(None),
            ).values(deletion_cleanup_started_at=current))
            keys = set(connection.scalars(select(
                document_versions.c.storage_key,
            ).where(document_versions.c.case_id == claim.case_id)).all())
            existing = set(connection.scalars(select(
                case_cleanup_files.c.storage_key,
            ).where(case_cleanup_files.c.case_id == claim.case_id)).all())
            for key in sorted(keys - existing):
                connection.execute(insert(case_cleanup_files).values(
                    id=uuid4().hex, case_id=claim.case_id,
                    storage_key=key, created_at=current,
                ))
            document_ids = select(document_versions.c.id).where(
                document_versions.c.case_id == claim.case_id)
            connection.execute(delete(evidence_blocks).where(
                evidence_blocks.c.document_version_id.in_(document_ids)))
            connection.execute(delete(document_versions).where(
                document_versions.c.case_id == claim.case_id))

    def _managed_path(self, key: str) -> Path:
        if not isinstance(key, str) or _MANAGED_KEY.fullmatch(key) is None:
            raise CleanupError("invalid_storage_key")
        path = self.data_root / key
        try:
            uploads_root = (self.data_root / "uploads").resolve()
            if (not uploads_root.is_relative_to(self.data_root)
                    or path.is_symlink()
                    or not path.resolve().is_relative_to(uploads_root)):
                raise CleanupError("invalid_storage_key")
        except (OSError, RuntimeError) as exc:
            raise CleanupError("invalid_storage_key") from exc
        return path

    def _process_file(self, claim: CleanupClaim, file_row,
                      now: datetime | None) -> tuple[int, int]:
        with self.engine.begin() as connection:
            self._require_claim(connection, claim, now)
            shared = connection.scalar(select(document_versions.c.id).where(
                document_versions.c.storage_key == file_row.storage_key,
                document_versions.c.case_id != claim.case_id,
            ).limit(1)) is not None
            if shared:
                connection.execute(update(case_cleanup_files).where(
                    case_cleanup_files.c.id == file_row.id,
                    case_cleanup_files.c.processed_at.is_(None),
                ).values(disposition="retained", processed_at=_now(now)))
                return 0, 1

        path = self._managed_path(file_row.storage_key)
        try:
            existed = path.is_file()
            path.unlink(missing_ok=True)
        except OSError as exc:
            raise CleanupError("unlink_failed") from exc
        with self.engine.begin() as connection:
            self._require_claim(connection, claim, now)
            changed = connection.execute(update(case_cleanup_files).where(
                case_cleanup_files.c.id == file_row.id,
                case_cleanup_files.c.processed_at.is_(None),
            ).values(disposition="removed", processed_at=_now(now)))
            if changed.rowcount != 1:
                raise CleanupError("stale_cleanup_claim")
        return int(existed), 0

    def _release(self, claim: CleanupClaim, now: datetime | None,
                 error_code: str | None = None) -> None:
        with self.engine.begin() as connection:
            self._require_claim(connection, claim, now)
            current = _now(now)
            connection.execute(update(cases).where(
                cases.c.id == claim.case_id,
                cases.c.deletion_claim_id == claim.claim_id,
            ).values(deletion_claim_id=None,
                     deletion_lease_until=(current + timedelta(seconds=60)
                                           if error_code else None),
                     deletion_error_code=error_code))

    def _finish(self, claim: CleanupClaim, now: datetime | None) -> bool:
        with self.engine.begin() as connection:
            self._require_claim(connection, claim, now)
            current = _now(now)
            pending = connection.scalar(select(func.count()).select_from(
                case_cleanup_files).where(
                    case_cleanup_files.c.case_id == claim.case_id,
                    case_cleanup_files.c.processed_at.is_(None),
                ))
            if pending:
                connection.execute(update(cases).where(
                    cases.c.id == claim.case_id,
                    cases.c.deletion_claim_id == claim.claim_id,
                ).values(deletion_claim_id=None, deletion_lease_until=None))
                return False
            if connection.scalar(select(func.count()).select_from(
                    document_versions).where(
                    document_versions.c.case_id == claim.case_id)):
                raise CleanupError("cleanup_invariant")

            run_ids = select(agent_runs.c.id).where(
                agent_runs.c.case_id == claim.case_id)
            attempt_ids = select(run_attempts.c.id).where(
                run_attempts.c.run_id.in_(run_ids))
            conversation_ids = select(conversations.c.id).where(
                conversations.c.case_id == claim.case_id)
            connection.execute(delete(idempotency_records).where(or_(
                idempotency_records.c.operation_scope.in_((
                    f"case:{claim.case_id}:upload",
                    f"case:{claim.case_id}:message",
                    f"case:{claim.case_id}:correction",
                )),
                (idempotency_records.c.resource_type == "attempt")
                & idempotency_records.c.resource_id.in_(attempt_ids),
                (idempotency_records.c.resource_type == "run")
                & idempotency_records.c.resource_id.in_(run_ids),
            )))
            connection.execute(delete(correction_reviews).where(
                correction_reviews.c.case_id == claim.case_id))
            dynamic_ids = select(dynamic_runs.c.run_id).where(dynamic_runs.c.case_id == claim.case_id)
            for table in (dynamic_batches, dynamic_corrections):
                connection.execute(delete(table).where(table.c.run_id.in_(dynamic_ids)))
            connection.execute(delete(dynamic_runs).where(dynamic_runs.c.case_id == claim.case_id))
            connection.execute(delete(assistant_runs).where(assistant_runs.c.case_id == claim.case_id))
            connection.execute(delete(checklist_operations).where(checklist_operations.c.case_id == claim.case_id))
            connection.execute(delete(checklist_heads).where(checklist_heads.c.case_id == claim.case_id))
            connection.execute(delete(checklist_versions).where(checklist_versions.c.case_id == claim.case_id))
            connection.execute(delete(conversation_summaries).where(conversation_summaries.c.case_id == claim.case_id))
            connection.execute(delete(task_memory).where(task_memory.c.case_id == claim.case_id))
            connection.execute(delete(corrections).where(
                corrections.c.case_id == claim.case_id))
            connection.execute(delete(run_events).where(
                run_events.c.run_id.in_(run_ids)))
            connection.execute(delete(outbox).where(
                outbox.c.run_id.in_(run_ids)))
            connection.execute(delete(run_attempts).where(
                run_attempts.c.run_id.in_(run_ids)))
            connection.execute(delete(agent_runs).where(
                agent_runs.c.case_id == claim.case_id))
            connection.execute(delete(messages).where(
                messages.c.conversation_id.in_(conversation_ids)))
            connection.execute(delete(conversations).where(
                conversations.c.case_id == claim.case_id))
            connection.execute(delete(input_snapshots).where(
                input_snapshots.c.case_id == claim.case_id))
            connection.execute(delete(suppliers).where(
                suppliers.c.case_id == claim.case_id))
            connection.execute(delete(case_cleanup_files).where(
                case_cleanup_files.c.case_id == claim.case_id))
            connection.execute(update(cases).where(
                cases.c.id == claim.case_id,
                cases.c.deletion_claim_id == claim.claim_id,
            ).values(deletion_claim_id=None, deletion_lease_until=None,
                     deletion_error_code=None, deletion_completed_at=current,
                     updated_at=current))
            return True

    def process(self, claim: CleanupClaim, *, now: datetime | None = None,
                max_files: int = 20) -> dict:
        if type(max_files) is not int or not 1 <= max_files <= 100:
            raise ValueError("invalid_cleanup_file_limit")
        self._prepare(claim, now)
        with self.engine.connect() as connection:
            files = connection.execute(select(case_cleanup_files).where(
                case_cleanup_files.c.case_id == claim.case_id,
                case_cleanup_files.c.processed_at.is_(None),
            ).order_by(case_cleanup_files.c.storage_key).limit(max_files)).all()
        removed = retained = 0
        for file_row in files:
            try:
                file_removed, file_retained = self._process_file(
                    claim, file_row, now)
            except CleanupError as exc:
                if exc.code == "stale_cleanup_claim":
                    raise
                self._release(claim, now, exc.code)
                return {"case_id": claim.case_id, "status": "deleting",
                        "files_removed": removed, "files_retained": retained,
                        "error_code": exc.code}
            removed += file_removed
            retained += file_retained
        finished = self._finish(claim, now)
        return {"case_id": claim.case_id,
                "status": "deleted" if finished else "deleting",
                "files_removed": removed, "files_retained": retained,
                "error_code": None}

    def process_once(self, *, case_id: str | None = None,
                     now: datetime | None = None,
                     max_files: int = 20) -> dict | None:
        claim = self.claim(case_id=case_id, now=now)
        return (self.process(claim, now=now, max_files=max_files)
                if claim is not None else None)
