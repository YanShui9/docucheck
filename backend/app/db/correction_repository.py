"""Source-validated fact corrections with append-only audit and Case revision fencing."""

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from decimal import Decimal
from hashlib import sha256
from uuid import uuid4

from pydantic import ValidationError
from sqlalchemy import Engine, insert, select, update
from sqlalchemy.exc import IntegrityError

from ..agent.fact_memory import decode_fact_sets, encode_fact_sets
from ..agent.session_memory import _repository_fingerprint
from ..evidence.repository import digest
from ..extraction.fields import FieldCandidate, _validate_candidate
from ..extraction.memory import FactSet
from ..extraction.validation import Candidate, validate_memory
from ..field_contract import FieldFactSet, FieldKey
from .agent_snapshot import SnapshotLoadError, load_agent_snapshot
from .models import (
    agent_runs,
    cases,
    correction_reviews,
    corrections,
    document_versions,
    evidence_blocks,
    idempotency_records,
    input_snapshots,
)


class CorrectionError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class SubmitCorrection:
    case_id: str
    source_run_id: str
    fact_set_id: str
    fact_id: str
    raw_value: str
    raw_unit: str
    reason: str
    expected_revision: int
    idempotency_key: str


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _plain(value):
    if isinstance(value, dict):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    if isinstance(value, Decimal):
        return str(value)
    return value


def _review_fingerprint(repo) -> str:
    """Bind a saved review to complete source text and document coverage."""
    return digest([
        repo.snapshot_id,
        sorted((document.document_id, document.source_hash, document.role,
                document.supplier, document.model, document.parsed.parser_version,
                document.parsed.status, document.parsed.coverage_issues,
                document.parsed.total_pages, document.parsed.processed_pages,
                document.parsed.total_sheets, document.parsed.processed_sheets)
               for document in repo.documents),
        sorted((asdict(block) for block in repo.blocks),
               key=lambda block: block["evidence_id"]),
    ])


def _hash(request: SubmitCorrection) -> str:
    payload = {key: value for key, value in asdict(request).items()
               if key != "idempotency_key"}
    return sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False,
                             separators=(",", ":")).encode()).hexdigest()


def _validate_request(request: SubmitCorrection) -> None:
    if (request.expected_revision < 1
            or not 1 <= len(request.idempotency_key) <= 255
            or not 1 <= len(request.reason) <= 2000
            or not all((request.case_id, request.source_run_id, request.fact_set_id,
                        request.fact_id))
            or not request.reason.strip() or request.reason != request.reason.strip()
            or any(ord(char) < 32 for char in request.reason)):
        raise CorrectionError("invalid_request")


class CorrectionRepository:
    def __init__(self, engine: Engine):
        self.engine = engine

    @staticmethod
    def _receipt(connection, correction_id: str) -> dict:
        row = connection.execute(select(
            corrections.c.id, corrections.c.case_id, corrections.c.source_run_id,
            corrections.c.from_snapshot_id, corrections.c.to_snapshot_id,
            input_snapshots.c.revision,
        ).join(input_snapshots, corrections.c.to_snapshot_id == input_snapshots.c.id)
            .where(corrections.c.id == correction_id)).mappings().first()
        if row is None:
            raise CorrectionError("repository_invariant")
        return {"correction_id": row["id"], "case_id": row["case_id"],
                "source_run_id": row["source_run_id"],
                "from_snapshot_id": row["from_snapshot_id"],
                "snapshot_id": row["to_snapshot_id"], "revision": row["revision"],
                "review_status": "pending_recheck"}

    def _replay(self, connection, scope: str, key: str, request_hash: str) -> dict | None:
        record = connection.execute(select(idempotency_records).where(
            idempotency_records.c.operation_scope == scope,
            idempotency_records.c.key == key,
        )).mappings().first()
        if record is None:
            return None
        if (record["request_hash"] != request_hash
                or record["resource_type"] != "correction"):
            raise CorrectionError("idempotency_conflict")
        return self._receipt(connection, record["resource_id"])

    def _facts(self, run: dict):
        memory = run["memory_json"]
        if not isinstance(memory, dict) or memory.get("version") != 2:
            return None, {}
        try:
            repo = load_agent_snapshot(self.engine, run["case_id"], run["snapshot_id"])
            if (memory.get("snapshot_id") != repo.snapshot_id
                    or memory.get("repository_fingerprint") != _repository_fingerprint(repo)
                    or type(memory.get("fact_sets_available")) is not bool
                    or not isinstance(memory.get("fact_sets"), list)):
                raise ValueError("memory_scope")
            if not memory["fact_sets_available"]:
                if memory["fact_sets"]:
                    raise ValueError("memory_scope")
                return repo, {}
            return repo, decode_fact_sets(repo, memory["fact_sets"])
        except SnapshotLoadError as exc:
            raise CorrectionError(exc.code) from exc
        except (TypeError, ValueError) as exc:
            raise CorrectionError("memory_scope") from exc

    def get_run_facts(self, run_id: str) -> dict:
        with self.engine.connect() as connection:
            row = connection.execute(select(
                agent_runs, cases.c.revision.label("current_revision"),
            ).join(cases, agent_runs.c.case_id == cases.c.id).where(
                agent_runs.c.id == run_id,
            )).mappings().first()
        if row is None:
            raise CorrectionError("not_found")
        if row["status"] not in {"completed", "partial"}:
            return {"run_id": run_id, "case_id": row["case_id"],
                    "snapshot_id": row["snapshot_id"], "revision": row["revision"],
                    "is_current": row["revision"] == row["current_revision"],
                    "available": False, "fact_sets": []}
        repo, sets = self._facts(row)
        available = (repo is not None and row["memory_json"]["fact_sets_available"])
        return {"run_id": run_id, "case_id": row["case_id"],
                "snapshot_id": row["snapshot_id"], "revision": row["revision"],
                "is_current": row["revision"] == row["current_revision"],
                "available": available,
                "fact_sets": encode_fact_sets(repo, sets) if available else []}

    def get_correction(self, case_id: str, correction_id: str) -> dict:
        with self.engine.connect() as connection:
            row = connection.execute(select(
                corrections, input_snapshots.c.revision,
            ).join(input_snapshots,
                   corrections.c.to_snapshot_id == input_snapshots.c.id).where(
                corrections.c.case_id == case_id,
                corrections.c.id == correction_id,
            )).mappings().first()
            review = (connection.execute(select(correction_reviews).where(
                correction_reviews.c.correction_id == correction_id,
            )).mappings().first() if row is not None else None)
        if row is None:
            raise CorrectionError("not_found")
        if review is not None:
            try:
                repo = load_agent_snapshot(self.engine, case_id, row["to_snapshot_id"])
            except SnapshotLoadError as exc:
                raise CorrectionError(exc.code) from exc
            if _review_fingerprint(repo) != review["repository_fingerprint"]:
                raise CorrectionError("correction_source_changed")
        return {
            "correction_id": row["id"], "case_id": row["case_id"],
            "source_run_id": row["source_run_id"],
            "from_snapshot_id": row["from_snapshot_id"],
            "snapshot_id": row["to_snapshot_id"], "revision": row["revision"],
            "review_status": (review["review_status"] if review is not None
                              else "pending_recheck"),
            "fact_set_id": row["fact_set_id"], "fact_id": row["fact_id"],
            "field_key": row["field_key"], "supplier": row["supplier"],
            "old_fact": row["old_fact_json"],
            "corrected_candidate": row["corrected_candidate_json"],
            "source_evidence_ids": row["source_evidence_ids_json"],
            "target_evidence_ids": row["target_evidence_ids_json"],
            "reason": row["reason"],
            "created_at": row["created_at"].replace(tzinfo=UTC).isoformat(),
            "corrected_fact": (next((fact for fact in review["fact_set_json"]["facts"]
                                     if fact["fact_id"] == review["corrected_fact_id"]), None)
                               if review is not None else None),
            "check_result": (review["check_result_json"] if review is not None else None),
        }

    @staticmethod
    def _corrected_fact(request: SubmitCorrection, fact_set, old_fact, repo):
        try:
            if isinstance(fact_set, FieldFactSet):
                candidate = FieldCandidate(
                    field_key=fact_set.field_key, raw_value=request.raw_value,
                    raw_unit=request.raw_unit,
                    evidence_refs=list(old_fact.evidence_refs),
                )
                corrected = _validate_candidate(candidate, repo, fact_set.supplier)
                stable = ("field_key", "qualifier", "comparator", "role", "supplier",
                          "model", "attributes", "evidence_refs")
            elif isinstance(fact_set, FactSet):
                if request.raw_unit not in {"GB", "GiB", "MiB"}:
                    raise ValueError("candidate_unit")
                candidate = Candidate(
                    raw_value=request.raw_value, raw_unit=request.raw_unit,
                    qualifier=old_fact.qualifier, comparator=old_fact.comparator,
                    evidence_refs=list(old_fact.evidence_refs),
                )
                corrected = validate_memory(candidate, repo, fact_set.supplier)
                stable = ("qualifier", "comparator", "role", "supplier", "model",
                          "evidence_refs")
            else:
                raise TypeError("fact_set_kind")
        except (TypeError, ValueError, ValidationError) as exc:
            raise CorrectionError("unsupported_correction") from exc
        if any(getattr(corrected, key) != getattr(old_fact, key) for key in stable):
            raise CorrectionError("unsupported_correction")
        if corrected.value == old_fact.value and corrected.unit == old_fact.unit:
            raise CorrectionError("correction_unchanged")
        return corrected

    def submit(self, request: SubmitCorrection) -> dict:
        _validate_request(request)
        scope = f"case:{request.case_id}:correction"
        if len(scope) > 128:
            raise CorrectionError("invalid_request")
        request_hash = _hash(request)
        try:
            with self.engine.begin() as connection:
                case = connection.execute(select(
                    cases.c.revision, cases.c.deletion_requested_at,
                ).where(
                    cases.c.id == request.case_id).with_for_update()).first()
                if case is None:
                    raise CorrectionError("not_found")
                if case.deletion_requested_at is not None:
                    raise CorrectionError("case_deleting")
                replay = self._replay(connection, scope, request.idempotency_key,
                                      request_hash)
                if replay is not None:
                    return replay
                if case.revision != request.expected_revision:
                    raise CorrectionError("revision_conflict")
                if connection.scalar(select(agent_runs.c.id).where(
                    agent_runs.c.case_id == request.case_id,
                    agent_runs.c.revision == case.revision,
                    agent_runs.c.status.in_(("queued", "running", "waiting_user")),
                ).limit(1)) is not None:
                    raise CorrectionError("case_busy")
                run = connection.execute(select(agent_runs).where(
                    agent_runs.c.id == request.source_run_id,
                    agent_runs.c.case_id == request.case_id,
                    agent_runs.c.revision == case.revision,
                    agent_runs.c.status.in_(("completed", "partial")),
                )).mappings().first()
                if run is None:
                    raise CorrectionError("source_run_not_current")
                repo, sets = self._facts(run)
                if repo is None or not run["memory_json"]["fact_sets_available"]:
                    raise CorrectionError("facts_unavailable")
                fact_set = sets.get(request.fact_set_id)
                old_fact = next((fact for fact in fact_set.facts
                                 if fact.fact_id == request.fact_id), None) if fact_set else None
                if old_fact is None:
                    raise CorrectionError("fact_not_found")
                corrected = self._corrected_fact(request, fact_set, old_fact, repo)

                source_docs = connection.execute(select(document_versions).where(
                    document_versions.c.snapshot_id == run["snapshot_id"],
                    document_versions.c.case_id == request.case_id,
                ).order_by(document_versions.c.id)).mappings().all()
                source_ids = [item["id"] for item in source_docs]
                source_blocks = connection.execute(select(evidence_blocks).where(
                    evidence_blocks.c.document_version_id.in_(source_ids),
                ).order_by(evidence_blocks.c.document_version_id,
                           evidence_blocks.c.ordinal).limit(25_001)).mappings().all()
                if len(source_blocks) > 25_000:
                    raise CorrectionError("snapshot_capacity")

                now, snapshot_id = _now(), uuid4().hex
                revision = case.revision + 1
                connection.execute(insert(input_snapshots).values(
                    id=snapshot_id, case_id=request.case_id,
                    revision=revision, created_at=now,
                ))
                document_map = {item["id"]: uuid4().hex for item in source_docs}
                for item in source_docs:
                    connection.execute(insert(document_versions).values(
                        id=document_map[item["id"]], case_id=request.case_id,
                        snapshot_id=snapshot_id, supplier_id=item["supplier_id"],
                        role=item["role"], original_name=item["original_name"],
                        storage_key=item["storage_key"],
                        source_sha256=item["source_sha256"],
                        media_type=item["media_type"],
                        parser_version=item["parser_version"],
                        parse_status=item["parse_status"],
                        parse_claim_id=None, parse_lease_until=None,
                        parse_attempts=0, coverage_json=item["coverage_json"],
                        created_at=now,
                    ))
                evidence_map = {item["id"]: digest([snapshot_id, item["id"]])
                                for item in source_blocks}
                if source_blocks:
                    connection.execute(insert(evidence_blocks), [
                        {"id": evidence_map[item["id"]],
                         "document_version_id": document_map[item["document_version_id"]],
                         "ordinal": item["ordinal"],
                         "source_text": item["source_text"],
                         "page_idx": item["page_idx"], "bbox_json": item["bbox_json"],
                         "sheet": item["sheet"], "cell": item["cell"],
                         "created_at": now}
                        for item in source_blocks
                    ])
                try:
                    target_refs = [evidence_map[ref] for ref in old_fact.evidence_refs]
                except KeyError as exc:
                    raise CorrectionError("repository_invariant") from exc
                correction_id = uuid4().hex
                connection.execute(insert(corrections).values(
                    id=correction_id, case_id=request.case_id,
                    source_run_id=run["id"],
                    from_snapshot_id=run["snapshot_id"], to_snapshot_id=snapshot_id,
                    fact_set_id=request.fact_set_id, fact_id=request.fact_id,
                    field_key=(fact_set.field_key.value if isinstance(
                        fact_set, FieldFactSet) else FieldKey.MEMORY.value),
                    supplier=fact_set.supplier,
                    old_fact_json=_plain(asdict(old_fact)),
                    corrected_candidate_json={
                        "raw_value": request.raw_value, "raw_unit": request.raw_unit,
                        "validated_value": str(corrected.value),
                        "validated_unit": corrected.unit,
                    },
                    source_evidence_ids_json=list(old_fact.evidence_refs),
                    target_evidence_ids_json=target_refs,
                    reason=request.reason, created_at=now,
                ))
                changed = connection.execute(update(cases).where(
                    cases.c.id == request.case_id,
                    cases.c.revision == case.revision,
                ).values(revision=revision, updated_at=now))
                if changed.rowcount != 1:
                    raise CorrectionError("revision_conflict")
                connection.execute(insert(idempotency_records).values(
                    id=uuid4().hex, operation_scope=scope,
                    key=request.idempotency_key, request_hash=request_hash,
                    resource_type="correction", resource_id=correction_id,
                    created_at=now,
                ))
                return self._receipt(connection, correction_id)
        except IntegrityError:
            with self.engine.connect() as connection:
                replay = self._replay(connection, scope, request.idempotency_key,
                                      request_hash)
                if replay is not None:
                    return replay
                current = connection.scalar(select(cases.c.revision).where(
                    cases.c.id == request.case_id))
                if current != request.expected_revision:
                    raise CorrectionError("revision_conflict") from None
            raise
