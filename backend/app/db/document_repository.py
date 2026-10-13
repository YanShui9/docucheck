"""Transactional document replacement and immutable Case input snapshots."""

import json
import re
from contextlib import nullcontext
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from uuid import uuid4

from sqlalchemy import Engine, func, insert, select, update
from sqlalchemy.exc import IntegrityError

from .models import (
    cases,
    document_versions,
    idempotency_records,
    input_snapshots,
    suppliers,
)


class DocumentError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class UploadCommand:
    case_id: str
    expected_revision: int
    role: str
    supplier_handle: str | None
    supplier_name: str | None
    idempotency_key: str
    document_id: str
    original_name: str
    storage_key: str
    source_sha256: str
    media_type: str
    pdf_pages: int
    model_name: str | None = None


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _hash(command: UploadCommand) -> str:
    payload = {
        "case_id": command.case_id, "expected_revision": command.expected_revision,
        "role": command.role, "supplier_handle": command.supplier_handle,
        "supplier_name": command.supplier_name, "original_name": command.original_name,
        "model_name": command.model_name,
        "source_sha256": command.source_sha256,
    }
    return sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False,
                             separators=(",", ":")).encode()).hexdigest()


def _validate(command: UploadCommand) -> None:
    if command.expected_revision < 0 or not 1 <= len(command.idempotency_key) <= 255:
        raise DocumentError("invalid_request")
    if command.role == "requirement":
        if (command.supplier_handle is not None or command.supplier_name is not None
                or command.model_name is not None):
            raise DocumentError("invalid_supplier_binding")
    elif command.role in {"quote", "spec"}:
        if (not command.supplier_handle or not command.supplier_name
                or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}",
                                    command.supplier_handle)
                or not 1 <= len(command.supplier_name) <= 255
                or command.supplier_name != command.supplier_name.strip()
                or any(ord(char) < 32 for char in command.supplier_name)
                or (command.model_name is not None and
                    (not 1 <= len(command.model_name) <= 120
                     or command.model_name != command.model_name.strip()
                     or any(ord(char) < 32 for char in command.model_name)))):
            raise DocumentError("invalid_supplier_binding")
    else:
        raise DocumentError("invalid_role")


class DocumentRepository:
    def __init__(self, engine: Engine):
        self.engine = engine

    def _receipt(self, connection, document_id: str) -> dict:
        row = connection.execute(select(
            document_versions.c.id, document_versions.c.case_id,
            document_versions.c.snapshot_id, document_versions.c.parse_status,
            document_versions.c.source_sha256, input_snapshots.c.revision,
        ).join(input_snapshots, document_versions.c.snapshot_id == input_snapshots.c.id)
            .where(document_versions.c.id == document_id)).mappings().first()
        if row is None:
            raise DocumentError("repository_invariant")
        return {"document_id": row["id"], "case_id": row["case_id"],
                "snapshot_id": row["snapshot_id"], "revision": row["revision"],
                "parse_status": row["parse_status"],
                "source_sha256": row["source_sha256"]}

    def _replay(self, connection, scope: str, key: str,
                request_hash: str) -> dict | None:
        record = connection.execute(select(idempotency_records).where(
            idempotency_records.c.operation_scope == scope,
            idempotency_records.c.key == key,
        )).mappings().first()
        if record is None:
            return None
        if record["request_hash"] != request_hash or record["resource_type"] != "document":
            raise DocumentError("idempotency_conflict")
        return self._receipt(connection, record["resource_id"])

    def upload(self, command: UploadCommand, *, transaction=None) -> dict:
        _validate(command)
        scope = f"case:{command.case_id}:upload"
        if len(scope) > 128:
            raise DocumentError("invalid_request")
        request_hash = _hash(command)
        owns_transaction = transaction is None
        try:
            with (self.engine.begin() if owns_transaction else nullcontext(transaction)) as connection:
                case = connection.execute(select(
                    cases.c.revision, cases.c.deletion_requested_at,
                ).where(
                    cases.c.id == command.case_id).with_for_update()).first()
                if case is None:
                    raise DocumentError("not_found")
                if case.deletion_requested_at is not None:
                    raise DocumentError("case_deleting")
                replay = self._replay(connection, scope, command.idempotency_key,
                                      request_hash)
                if replay is not None:
                    return replay
                if case.revision != command.expected_revision:
                    raise DocumentError("revision_conflict")
                old_docs = []
                if case.revision:
                    snapshot_id = connection.scalar(select(input_snapshots.c.id).where(
                        input_snapshots.c.case_id == command.case_id,
                        input_snapshots.c.revision == case.revision,
                    ))
                    if snapshot_id is None:
                        raise DocumentError("repository_invariant")
                    old_docs = connection.execute(select(document_versions).where(
                        document_versions.c.snapshot_id == snapshot_id,
                    )).mappings().all()

                supplier_id = None
                if command.supplier_handle:
                    existing = connection.execute(select(suppliers).where(
                        suppliers.c.case_id == command.case_id,
                        suppliers.c.handle == command.supplier_handle,
                    )).mappings().first()
                    if existing is None:
                        supplier_id = uuid4().hex
                        connection.execute(insert(suppliers).values(
                            id=supplier_id, case_id=command.case_id,
                            handle=command.supplier_handle,
                            name=command.supplier_name, created_at=_now(),
                        ))
                    else:
                        if existing["name"] != command.supplier_name:
                            raise DocumentError("supplier_conflict")
                        supplier_id = existing["id"]

                retained = [doc for doc in old_docs if not (
                    doc["role"] == command.role and doc["supplier_id"] == supplier_id
                )]
                if len(retained) >= 7:
                    raise DocumentError("case_file_limit")
                active_suppliers = {doc["supplier_id"] for doc in retained
                                    if doc["supplier_id"] is not None}
                if supplier_id:
                    active_suppliers.add(supplier_id)
                if len(active_suppliers) > 3:
                    raise DocumentError("case_supplier_limit")
                pages = command.pdf_pages + sum(
                    (doc["coverage_json"] or {}).get("preflight", {}).get("pdf_pages", 0)
                    for doc in retained
                )
                if pages > 30:
                    raise DocumentError("case_page_limit")

                now = _now()
                snapshot_id = uuid4().hex
                revision = case.revision + 1
                connection.execute(insert(input_snapshots).values(
                    id=snapshot_id, case_id=command.case_id,
                    revision=revision, created_at=now,
                ))
                for doc in retained:
                    preflight = (doc["coverage_json"] or {}).get("preflight", {})
                    connection.execute(insert(document_versions).values(
                        id=uuid4().hex, case_id=command.case_id,
                        snapshot_id=snapshot_id, supplier_id=doc["supplier_id"],
                        model_name=doc["model_name"],
                        role=doc["role"], original_name=doc["original_name"],
                        storage_key=doc["storage_key"],
                        source_sha256=doc["source_sha256"],
                        media_type=doc["media_type"], parser_version=None,
                        parse_status="pending",
                        coverage_json={"preflight": preflight},
                        created_at=now,
                    ))
                connection.execute(insert(document_versions).values(
                    id=command.document_id, case_id=command.case_id,
                    snapshot_id=snapshot_id, supplier_id=supplier_id,
                    model_name=command.model_name,
                    role=command.role, original_name=command.original_name,
                    storage_key=command.storage_key,
                    source_sha256=command.source_sha256,
                    media_type=command.media_type, parser_version=None,
                    parse_status="pending",
                    coverage_json={"preflight": {"pdf_pages": command.pdf_pages}},
                    created_at=now,
                ))
                changed = connection.execute(update(cases).where(
                    cases.c.id == command.case_id,
                    cases.c.revision == command.expected_revision,
                ).values(revision=revision, updated_at=now))
                if changed.rowcount != 1:
                    raise DocumentError("revision_conflict")
                connection.execute(insert(idempotency_records).values(
                    id=uuid4().hex, operation_scope=scope,
                    key=command.idempotency_key, request_hash=request_hash,
                    resource_type="document", resource_id=command.document_id,
                    created_at=now,
                ))
                return self._receipt(connection, command.document_id)
        except IntegrityError:
            if not owns_transaction:
                # The caller owns this transaction and its filesystem fence.
                # Do not escape it to replay on an unrelated connection.
                raise
            with self.engine.connect() as connection:
                replay = self._replay(connection, scope, command.idempotency_key,
                                      request_hash)
                if replay is not None:
                    return replay
                current = connection.scalar(select(cases.c.revision).where(
                    cases.c.id == command.case_id))
                if current != command.expected_revision:
                    raise DocumentError("revision_conflict") from None
            raise

    def list_current(self, case_id: str) -> dict:
        with self.engine.connect() as connection:
            case = connection.execute(select(
                cases.c.revision, cases.c.deletion_cleanup_started_at,
                cases.c.deletion_completed_at,
            ).where(cases.c.id == case_id)).first()
            if case is None:
                raise DocumentError("not_found")
            if case.deletion_completed_at is not None:
                raise DocumentError("case_deleted")
            if case.deletion_cleanup_started_at is not None:
                raise DocumentError("case_deleting")
            revision = case.revision
            if revision == 0:
                return {"case_id": case_id, "revision": 0,
                        "snapshot_id": None, "documents": []}
            snapshot_id = connection.scalar(select(input_snapshots.c.id).where(
                input_snapshots.c.case_id == case_id,
                input_snapshots.c.revision == revision,
            ))
            if snapshot_id is None:
                raise DocumentError("repository_invariant")
            rows = connection.execute(select(
                document_versions.c.id, document_versions.c.original_name,
                document_versions.c.role, document_versions.c.parse_status,
                document_versions.c.source_sha256, document_versions.c.model_name,
                suppliers.c.handle,
                suppliers.c.name,
            ).select_from(document_versions.outerjoin(
                suppliers, document_versions.c.supplier_id == suppliers.c.id,
            )).where(document_versions.c.snapshot_id == snapshot_id)
                .order_by(document_versions.c.role, suppliers.c.handle)).all()
            return {"case_id": case_id, "revision": revision,
                    "snapshot_id": snapshot_id,
                    "documents": [{"id": row.id, "original_name": row.original_name,
                                   "role": row.role, "supplier_handle": row.handle,
                                   "supplier_name": row.name,
                                   "model_name": row.model_name,
                                   "parse_status": row.parse_status,
                                   "source_sha256": row.source_sha256}
                                  for row in rows]}

    def list_snapshots(self, case_id: str, *, before_revision: int | None = None,
                       limit: int = 50) -> dict:
        self.list_current(case_id)
        with self.engine.connect() as connection:
            query = select(
                input_snapshots.c.id, input_snapshots.c.revision,
                input_snapshots.c.created_at,
                func.count(document_versions.c.id).label("document_count"),
            ).select_from(input_snapshots.outerjoin(
                document_versions,
                (document_versions.c.case_id == input_snapshots.c.case_id) &
                (document_versions.c.snapshot_id == input_snapshots.c.id),
            )).where(input_snapshots.c.case_id == case_id)
            if before_revision is not None:
                query = query.where(input_snapshots.c.revision < before_revision)
            rows = connection.execute(query.group_by(
                input_snapshots.c.id, input_snapshots.c.revision,
                input_snapshots.c.created_at,
            ).order_by(input_snapshots.c.revision.desc())
                .limit(limit + 1)).all()
        page = rows[:limit]
        return {"case_id": case_id,
                "snapshots": [{"id": row.id, "revision": row.revision,
                               "created_at": row.created_at.replace(
                                   tzinfo=UTC).isoformat().replace("+00:00", "Z"),
                               "document_count": row.document_count}
                              for row in page],
                "next_before_revision": (page[-1].revision
                                         if len(rows) > limit else None)}

    def list_snapshot(self, case_id: str, snapshot_id: str) -> dict:
        self.list_current(case_id)
        with self.engine.connect() as connection:
            revision = connection.scalar(select(input_snapshots.c.revision).where(
                input_snapshots.c.case_id == case_id,
                input_snapshots.c.id == snapshot_id,
            ))
            if revision is None:
                raise DocumentError("not_found")
            rows = connection.execute(select(
                document_versions.c.id, document_versions.c.original_name,
                document_versions.c.role, document_versions.c.parse_status,
                document_versions.c.source_sha256, document_versions.c.model_name,
                suppliers.c.handle,
                suppliers.c.name,
            ).select_from(document_versions.outerjoin(
                suppliers, document_versions.c.supplier_id == suppliers.c.id,
            )).where(document_versions.c.case_id == case_id,
                     document_versions.c.snapshot_id == snapshot_id)
                .order_by(document_versions.c.role, suppliers.c.handle)).all()
        return {"case_id": case_id, "revision": revision,
                "snapshot_id": snapshot_id,
                "documents": [{"id": row.id, "original_name": row.original_name,
                               "role": row.role, "supplier_handle": row.handle,
                               "supplier_name": row.name,
                               "model_name": row.model_name,
                               "parse_status": row.parse_status,
                               "source_sha256": row.source_sha256}
                              for row in rows]}
