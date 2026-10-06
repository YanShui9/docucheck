"""SQL leases and generation fencing for asynchronous document vector indexes."""
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from sqlalchemy import Connection, Engine, and_, insert, or_, select, update

from ..evidence.repository import digest
from ..vector_store.target import IndexTarget
from .models import cases, document_versions, input_snapshots
from .models import document_index_jobs as jobs
from .models import index_manifests as manifests


class IndexError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _now(value: datetime | None = None) -> datetime:
    value = value or datetime.now(UTC)
    return value.astimezone(UTC).replace(tzinfo=None) if value.tzinfo else value


def enqueue_document(connection: Connection, document_id: str, *, now: datetime | None = None) -> bool:
    """Caller holds the Case lock; registration shares the parsing transaction."""
    current = _now(now)
    row = connection.execute(select(document_versions).where(
        document_versions.c.id == document_id)).mappings().first()
    if row is None or row["parse_status"] not in {"complete", "partial"}:
        raise IndexError("index_document_not_ready")
    if connection.scalar(select(jobs.c.document_id).where(jobs.c.document_id == document_id)):
        return False
    connection.execute(insert(jobs).values(
        document_id=document_id, case_id=row["case_id"], snapshot_id=row["snapshot_id"],
        status="pending", available_at=current, created_at=current, updated_at=current))
    return True


def _retire(connection: Connection, manifest_id: str | None, current: datetime) -> None:
    if manifest_id:
        connection.execute(update(manifests).where(manifests.c.id == manifest_id).values(
            status="deleting", cleanup_claim_id=None, cleanup_lease_until=None,
            next_cleanup_at=current, updated_at=current))


def invalidate_case_indexes(connection: Connection, case_id: str, *, now: datetime | None = None) -> None:
    current = _now(now)
    connection.execute(update(jobs).where(jobs.c.case_id == case_id).values(
        status="deleting", claim_id=None, lease_until=None, fence=jobs.c.fence + 1, updated_at=current))
    connection.execute(update(manifests).where(manifests.c.case_id == case_id).values(
        status="deleting", cleanup_claim_id=None, cleanup_lease_until=None,
        next_cleanup_at=current, updated_at=current))


@dataclass(frozen=True)
class IndexClaim:
    case_id: str
    snapshot_id: str
    document_id: str
    revision: int
    claim_id: str
    fence: int
    manifest_id: str
    generation: str
    project_key: str
    target: IndexTarget


@dataclass(frozen=True)
class CleanupClaim:
    case_id: str
    snapshot_id: str
    document_id: str
    manifest_id: str
    claim_id: str
    generation: str
    project_key: str
    target: IndexTarget


class IndexRepository:
    def __init__(self, engine: Engine):
        self.engine = engine

    def _case(self, connection: Connection, case_id: str, snapshot_id: str, revision: int | None = None,
              *, skip_locked: bool = False):
        case = connection.execute(select(cases).where(cases.c.id == case_id)
                                  .with_for_update(skip_locked=skip_locked)).mappings().first()
        if case is None:
            raise IndexError("not_found")
        if case["deletion_requested_at"] is not None:
            raise IndexError("case_deleting")
        snapshot_revision = connection.scalar(select(input_snapshots.c.revision).where(
            input_snapshots.c.id == snapshot_id, input_snapshots.c.case_id == case_id))
        if snapshot_revision is None:
            raise IndexError("not_found")
        if snapshot_revision != case["revision"] or (revision is not None and revision != case["revision"]):
            raise IndexError("index_snapshot_changed")
        return case

    def schedule(self, case_id: str, snapshot_id: str, revision: int, *, target: IndexTarget | None = None,
                 retry_failed: bool = False, now: datetime | None = None) -> int:
        current = _now(now)
        with self.engine.begin() as connection:
            self._case(connection, case_id, snapshot_id, revision)
            docs = connection.execute(select(document_versions.c.id).where(
                document_versions.c.case_id == case_id, document_versions.c.snapshot_id == snapshot_id,
                document_versions.c.parse_status.in_(("complete", "partial")))).scalars().all()
            for document_id in docs:
                enqueue_document(connection, document_id, now=current)
                row = connection.execute(select(jobs).where(jobs.c.document_id == document_id)
                                         .with_for_update()).mappings().one()
                changed_target = target is not None and row["target_key"] != target.key
                if changed_target or (retry_failed and row["status"] == "failed"):
                    _retire(connection, row["manifest_id"], current)
                    connection.execute(update(jobs).where(jobs.c.document_id == document_id).values(
                        status="pending", target_key=target.key if target else row["target_key"],
                        manifest_id=None, claim_id=None, lease_until=None, attempts=0,
                        fence=jobs.c.fence + 1, available_at=current, error_code=None, updated_at=current))
        return len(docs)

    def status(self, case_id: str, snapshot_id: str) -> dict[str, Any]:
        with self.engine.begin() as connection:
            case = self._case(connection, case_id, snapshot_id)
            rows = connection.execute(select(document_versions.c.id, document_versions.c.parse_status,
                jobs.c.status, jobs.c.attempts, jobs.c.error_code, manifests.c.expected_count,
                manifests.c.ingested_count).select_from(document_versions.outerjoin(jobs,
                    document_versions.c.id == jobs.c.document_id).outerjoin(manifests,
                    jobs.c.manifest_id == manifests.c.id)).where(document_versions.c.case_id == case_id,
                    document_versions.c.snapshot_id == snapshot_id).order_by(document_versions.c.id)).mappings().all()
        return {"case_id": case_id, "snapshot_id": snapshot_id, "revision": case["revision"],
                "documents": [{"document_id": r["id"], "parse_status": r["parse_status"],
                    "status": r["status"] or ("not_scheduled" if r["parse_status"] in {"complete", "partial"} else "awaiting_parse"),
                    "attempts": r["attempts"] or 0, "error_code": r["error_code"],
                    "expected_count": r["expected_count"] or 0, "ingested_count": r["ingested_count"] or 0}
                    for r in rows]}

    def claim(self, target: IndexTarget, *, now: datetime | None = None,
              lease_seconds: int = 300) -> IndexClaim | None:
        if type(lease_seconds) is not int or not 60 <= lease_seconds <= 3600:
            raise ValueError("invalid_index_lease")
        current = _now(now)
        with self.engine.connect() as connection:
            candidates = connection.execute(select(jobs.c.document_id, jobs.c.case_id, jobs.c.snapshot_id)
                .join(input_snapshots, jobs.c.snapshot_id == input_snapshots.c.id)
                .join(cases, jobs.c.case_id == cases.c.id).where(
                    cases.c.deletion_requested_at.is_(None), cases.c.revision == input_snapshots.c.revision,
                    or_(jobs.c.target_key.is_(None), jobs.c.target_key == target.key),
                    or_(and_(jobs.c.status == "pending", jobs.c.available_at <= current),
                        and_(jobs.c.status == "indexing", jobs.c.lease_until <= current)))
                .order_by(jobs.c.available_at, jobs.c.document_id).limit(100)).mappings().all()
        for candidate in candidates:
            with self.engine.begin() as connection:
                try:
                    case = self._case(connection, candidate["case_id"], candidate["snapshot_id"], skip_locked=True)
                except IndexError:
                    continue
                row = connection.execute(select(jobs).where(jobs.c.document_id == candidate["document_id"])
                                         .with_for_update(skip_locked=True)).mappings().first()
                if (row is None or row["target_key"] not in {None, target.key}
                        or not ((row["status"] == "pending" and row["available_at"] <= current)
                                or (row["status"] == "indexing" and row["lease_until"] <= current))):
                    continue
                _retire(connection, row["manifest_id"], current)
                if row["attempts"] >= 3:
                    connection.execute(update(jobs).where(jobs.c.document_id == row["document_id"]).values(
                        status="failed", claim_id=None, lease_until=None, error_code="index_worker_lost", updated_at=current))
                    continue
                generation, claim_id = uuid4().hex, uuid4().hex
                manifest_id = digest({"document_id": row["document_id"], "generation": generation, "target": target.key})
                project_key = case["project_id"] or "local:" + case["id"]
                connection.execute(insert(manifests).values(
                    id=manifest_id, case_id=row["case_id"], snapshot_id=row["snapshot_id"],
                    document_id=row["document_id"], project_key=project_key, embedding_revision=target.revision,
                    dimension=target.dimension, chunk_schema=target.policy.revision, collection_name=target.collection,
                    generation=generation, status="indexing", created_at=current, updated_at=current))
                connection.execute(update(jobs).where(jobs.c.document_id == row["document_id"],
                    jobs.c.fence == row["fence"], jobs.c.status == row["status"]).values(
                        status="indexing", target_key=target.key, manifest_id=manifest_id, claim_id=claim_id,
                        lease_until=current + timedelta(seconds=lease_seconds), fence=row["fence"] + 1,
                        attempts=row["attempts"] + 1, error_code=None, updated_at=current))
                return IndexClaim(row["case_id"], row["snapshot_id"], row["document_id"], case["revision"],
                                  claim_id, row["fence"] + 1, manifest_id, generation, project_key, target)
        return None

    def _owned(self, connection: Connection, claim: IndexClaim, current: datetime):
        self._case(connection, claim.case_id, claim.snapshot_id, claim.revision)
        row = connection.execute(select(jobs).where(
            jobs.c.document_id == claim.document_id, jobs.c.case_id == claim.case_id,
            jobs.c.snapshot_id == claim.snapshot_id, jobs.c.manifest_id == claim.manifest_id,
            jobs.c.status == "indexing", jobs.c.claim_id == claim.claim_id,
            jobs.c.fence == claim.fence, jobs.c.lease_until > current).with_for_update()).mappings().first()
        if row is None:
            raise IndexError("stale_index_claim")
        return row

    def validate(self, claim: IndexClaim, *, now: datetime | None = None) -> None:
        with self.engine.begin() as connection:
            self._owned(connection, claim, _now(now))

    def renew(self, claim: IndexClaim, *, now: datetime | None = None, lease_seconds: int = 300) -> None:
        if type(lease_seconds) is not int or not 60 <= lease_seconds <= 3600:
            raise ValueError("invalid_index_lease")
        current = _now(now)
        with self.engine.begin() as connection:
            self._owned(connection, claim, current)
            connection.execute(update(jobs).where(jobs.c.document_id == claim.document_id).values(
                lease_until=current + timedelta(seconds=lease_seconds), updated_at=current))

    def finish(self, claim: IndexClaim, fingerprint: str, count: int, *, now: datetime | None = None) -> None:
        if not re.fullmatch(r"[a-f0-9]{64}", fingerprint) or type(count) is not int or count < 1:
            raise ValueError("invalid_index_receipt")
        current = _now(now)
        with self.engine.begin() as connection:
            self._owned(connection, claim, current)
            parsed = connection.scalar(select(document_versions.c.parse_status).where(document_versions.c.id == claim.document_id))
            if parsed not in {"complete", "partial"}:
                raise IndexError("index_document_not_ready")
            connection.execute(update(manifests).where(manifests.c.id == claim.manifest_id).values(
                status="ready", fingerprint=fingerprint, expected_count=count, ingested_count=count, updated_at=current))
            connection.execute(update(jobs).where(jobs.c.document_id == claim.document_id).values(
                status="ready", claim_id=None, lease_until=None, error_code=None, updated_at=current))

    def fail(self, claim: IndexClaim, code: str = "index_processing_failed", *, now: datetime | None = None) -> str:
        if code not in {"index_processing_failed", "index_service_unavailable", "index_source_changed"}:
            raise ValueError("invalid_index_error")
        current = _now(now)
        with self.engine.begin() as connection:
            row = self._owned(connection, claim, current)
            status = "failed" if row["attempts"] >= 3 else "pending"
            _retire(connection, claim.manifest_id, current)
            connection.execute(update(jobs).where(jobs.c.document_id == claim.document_id).values(
                status=status, claim_id=None, lease_until=None,
                available_at=current + timedelta(seconds=30 * 2 ** (row["attempts"] - 1)),
                error_code=code, updated_at=current))
        return status

    def claim_cleanup(self, *, now: datetime | None = None, lease_seconds: int = 300) -> CleanupClaim | None:
        if type(lease_seconds) is not int or not 60 <= lease_seconds <= 3600:
            raise ValueError("invalid_index_lease")
        current = _now(now)
        with self.engine.connect() as connection:
            candidates = connection.execute(select(manifests.c.id, manifests.c.case_id).where(
                manifests.c.status.in_(("deleting", "deleted")), manifests.c.next_cleanup_at <= current,
                or_(manifests.c.cleanup_claim_id.is_(None), manifests.c.cleanup_lease_until <= current))
                .order_by(manifests.c.next_cleanup_at, manifests.c.id).limit(100)).mappings().all()
        for candidate in candidates:
            with self.engine.begin() as connection:
                if connection.scalar(select(cases.c.id).where(cases.c.id == candidate["case_id"])
                                     .with_for_update(skip_locked=True)) is None:
                    continue
                row = connection.execute(select(manifests).where(manifests.c.id == candidate["id"],
                    manifests.c.status.in_(("deleting", "deleted")), manifests.c.next_cleanup_at <= current,
                    or_(manifests.c.cleanup_claim_id.is_(None), manifests.c.cleanup_lease_until <= current))
                    .with_for_update(skip_locked=True)).mappings().first()
                if row is None:
                    continue
                claim_id = uuid4().hex
                connection.execute(update(manifests).where(manifests.c.id == row["id"]).values(
                    cleanup_claim_id=claim_id, cleanup_lease_until=current + timedelta(seconds=lease_seconds),
                    cleanup_attempts=manifests.c.cleanup_attempts + 1, updated_at=current))
                return CleanupClaim(row["case_id"], row["snapshot_id"], row["document_id"], row["id"],
                                    claim_id, row["generation"], row["project_key"], IndexTarget.from_manifest(row))
        return None

    def reconcile_stale(self, *, now: datetime | None = None) -> int:
        """Retire abandoned in-flight generations after snapshot replacement or lost source rows."""
        current = _now(now)
        with self.engine.connect() as connection:
            candidates = connection.execute(select(manifests.c.id, manifests.c.case_id)
                .join(cases, cases.c.id == manifests.c.case_id)
                .outerjoin(input_snapshots, input_snapshots.c.id == manifests.c.snapshot_id)
                .outerjoin(jobs, jobs.c.manifest_id == manifests.c.id).where(manifests.c.status == "indexing",
                    or_(cases.c.deletion_requested_at.is_not(None), input_snapshots.c.id.is_(None),
                        input_snapshots.c.revision != cases.c.revision, jobs.c.document_id.is_(None)))
                .order_by(manifests.c.created_at, manifests.c.id).limit(100)).mappings().all()
        retired = 0
        for candidate in candidates:
            with self.engine.begin() as connection:
                case = connection.execute(select(cases).where(cases.c.id == candidate["case_id"])
                                          .with_for_update(skip_locked=True)).mappings().first()
                if case is None:
                    continue
                job = connection.execute(select(jobs).where(jobs.c.manifest_id == candidate["id"])
                                         .with_for_update()).mappings().first()
                row = connection.execute(select(manifests).where(manifests.c.id == candidate["id"])
                                         .with_for_update()).mappings().one()
                revision = connection.scalar(select(input_snapshots.c.revision).where(
                    input_snapshots.c.id == row["snapshot_id"], input_snapshots.c.case_id == case["id"]))
                if row["status"] != "indexing" or (job is not None and revision == case["revision"]
                                                      and case["deletion_requested_at"] is None):
                    continue
                _retire(connection, row["id"], current)
                if job is not None:
                    connection.execute(update(jobs).where(jobs.c.document_id == job["document_id"]).values(
                        status="deleting" if case["deletion_requested_at"] else "failed", claim_id=None,
                        lease_until=None, fence=jobs.c.fence + 1, error_code="index_snapshot_changed", updated_at=current))
                retired += 1
        return retired

    def finish_cleanup(self, claim: CleanupClaim, *, success: bool = True, now: datetime | None = None) -> None:
        current = _now(now)
        with self.engine.begin() as connection:
            connection.execute(select(cases.c.id).where(cases.c.id == claim.case_id).with_for_update())
            changed = connection.execute(update(manifests).where(
                manifests.c.id == claim.manifest_id, manifests.c.cleanup_claim_id == claim.claim_id,
                manifests.c.cleanup_lease_until > current, manifests.c.status.in_(("deleting", "deleted"))).values(
                    status="deleted" if success else "deleting", cleanup_claim_id=None, cleanup_lease_until=None,
                    next_cleanup_at=current + timedelta(seconds=300 if success else 60),
                    error_code=None if success else "index_cleanup_failed", updated_at=current))
            if changed.rowcount != 1:
                raise IndexError("stale_index_cleanup_claim")
