"""Confirmed item attribution with Case-first transactions and immutable replay."""

import json
import re
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import Engine, insert, select, update
from sqlalchemy.engine import Connection

from ..criteria.item_bindings import (
    ItemBindingEntry,
    validate_bindings,
    validated_from_frozen,
)
from ..criteria.models import ChecklistDefinition
from ..criteria.page_exclusions import PdfPageExclusion, validate_exclusions
from ..evidence.models import Document, ScopedEvidence
from ..evidence.repository import EvidenceRepository, digest
from ..parsing import EvidenceBlock, ParseResult
from .agent_snapshot import MAX_AGENT_BLOCKS, MAX_AGENT_TEXT_BYTES
from .checklist_repository import ChecklistError, _scope, _version
from .models import (
    agent_runs,
    cases,
    checklist_heads,
    checklist_versions,
    document_versions,
    evidence_blocks,
    input_snapshots,
    item_binding_heads,
    item_binding_operations,
    item_binding_versions,
    project_members,
    suppliers,
)


class ItemBindingError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _read(connection: Connection, query, current_read: bool):
    return connection.execute(
        query.with_for_update() if current_read else query
    ).mappings()


def _check_access(
    connection: Connection,
    case_id: str,
    actor: str | None = None,
    *,
    current_read: bool = False,
) -> dict:
    case = _read(
        connection,
        select(cases).where(cases.c.id == case_id),
        actor is not None or current_read,
    ).first()
    if not case:
        raise ItemBindingError("not_found")
    if case["deletion_requested_at"] is not None:
        raise ItemBindingError("case_deleting")
    if actor is not None and case["project_id"]:
        member = _read(
            connection,
            select(project_members.c.role).where(
                project_members.c.project_id == case["project_id"],
                project_members.c.principal_key == actor,
            ),
            True,
        ).first()
        if not member or member["role"] not in {"owner", "editor"}:
            raise ItemBindingError("not_found")
    return dict(case)


def _definition(value: ChecklistDefinition | dict | None) -> dict | None:
    if value is None:
        return None
    return (
        value
        if isinstance(value, ChecklistDefinition)
        else ChecklistDefinition.model_validate(value)
    ).model_dump(mode="json")


def _empty(
    case_id: str,
    snapshot_id: str,
    checklist_version_id: str | None,
    definition: dict | None,
) -> dict:
    return {
        "schema_version": 1,
        "case_id": case_id,
        "snapshot_id": snapshot_id,
        "checklist_version_id": checklist_version_id,
        "checklist_digest": digest(definition),
        "entries": [],
    }


def _context(
    connection: Connection,
    case_id: str,
    snapshot_id: str,
    checklist_version_id: str | None,
    definition: ChecklistDefinition | dict | None,
    *,
    current_read: bool,
) -> dict | None:
    """Authenticate even empty identities against immutable persisted inputs."""
    snapshot = _read(
        connection,
        select(input_snapshots.c.id).where(
            input_snapshots.c.case_id == case_id, input_snapshots.c.id == snapshot_id
        ),
        current_read,
    ).first()
    if snapshot is None:
        raise ItemBindingError("not_found")
    canonical = _definition(definition)
    if checklist_version_id is None:
        if canonical is not None:
            raise ItemBindingError("item_binding_source_changed")
        return None
    try:
        persisted = _version(
            connection, case_id, checklist_version_id, current_read=current_read
        )
    except ChecklistError:
        raise ItemBindingError("item_binding_source_changed") from None
    if persisted["snapshot_id"] != snapshot_id or persisted["definition"] != canonical:
        raise ItemBindingError("item_binding_source_changed")
    return canonical


def _repository(
    connection: Connection,
    case_id: str,
    snapshot_id: str,
    entries: list[dict],
    *,
    current_read: bool,
) -> EvidenceRepository:
    ids = sorted({entry["document_id"] for entry in entries})
    if not 1 <= len(ids) <= 7:
        raise ItemBindingError("item_binding_capacity")
    docs = _read(
        connection,
        select(document_versions, suppliers.c.handle.label("supplier_handle"))
        .outerjoin(suppliers, suppliers.c.id == document_versions.c.supplier_id)
        .where(
            document_versions.c.case_id == case_id,
            document_versions.c.snapshot_id == snapshot_id,
            document_versions.c.id.in_(ids),
        )
        .order_by(document_versions.c.id),
        current_read,
    ).all()
    if {d["id"] for d in docs} != set(ids):
        raise ItemBindingError("item_binding_source")
    rows = _read(
        connection,
        select(evidence_blocks)
        .where(evidence_blocks.c.document_version_id.in_(ids))
        .order_by(evidence_blocks.c.document_version_id, evidence_blocks.c.ordinal)
        .limit(MAX_AGENT_BLOCKS + 1),
        current_read,
    ).all()
    if (
        len(rows) > MAX_AGENT_BLOCKS
        or sum(len(r["source_text"].encode()) for r in rows) > MAX_AGENT_TEXT_BYTES
    ):
        raise ItemBindingError("item_binding_capacity")
    documents, scoped = [], []
    for doc in docs:
        coverage = doc["coverage_json"] or {}
        parser = coverage.get("parser_version_full")
        blocks = [r for r in rows if r["document_version_id"] == doc["id"]]
        if (
            not parser
            or not blocks
            or any(b["ordinal"] != n for n, b in enumerate(blocks))
        ):
            raise ItemBindingError("item_binding_source")
        original = []
        for b in blocks:
            bbox = b["bbox_json"] or {}
            values = {
                "evidence_id": b["id"],
                "document_id": doc["id"],
                "text": b["source_text"],
                "page_idx": b["page_idx"],
                "sheet": b["sheet"],
                "cell": b["cell"],
                "bbox": tuple(bbox["bbox"]) if bbox.get("bbox") else None,
                "page_width_pt": bbox.get("page_width_pt"),
                "page_height_pt": bbox.get("page_height_pt"),
            }
            original.append(EvidenceBlock(**values))
            scoped.append(
                ScopedEvidence(
                    **values,
                    role=doc["role"],
                    supplier=doc["supplier_handle"],
                    model=doc["model_name"],
                    source_hash=doc["source_sha256"],
                    parser_version=parser,
                    ordinal=b["ordinal"],
                )
            )
        parsed = ParseResult(
            document_id=doc["id"],
            parser_version=parser,
            status=doc["parse_status"],
            blocks=tuple(original),
            total_pages=coverage.get("total_pages"),
            processed_pages=tuple(coverage.get("processed_pages") or ()),
            total_sheets=coverage.get("total_sheets"),
            processed_sheets=tuple(coverage.get("processed_sheets") or ()),
            coverage_issues=tuple(coverage.get("coverage_issues") or ()),
        )
        documents.append(
            Document(
                doc["id"],
                doc["source_sha256"],
                doc["role"],
                doc["supplier_handle"],
                doc["model_name"],
                parsed,
            )
        )
    revision = connection.scalar(
        select(input_snapshots.c.revision).where(
            input_snapshots.c.case_id == case_id, input_snapshots.c.id == snapshot_id
        )
    )
    if revision is None:
        raise ItemBindingError("not_found")
    return EvidenceRepository(
        case_id,
        revision,
        documents,
        snapshot_id=snapshot_id,
        scoped_blocks=tuple(scoped),
    )


def _validate_payload(
    connection: Connection,
    payload: dict,
    definition: dict | None,
    *,
    current_read: bool,
) -> None:
    if (
        payload.get("checklist_digest") != digest(definition)
        or payload.get("schema_version") not in {1, 2}
    ):
        raise ItemBindingError("item_binding_source_changed")
    if "continuation" in payload:
        _continuation_origin(connection, payload, definition, current_read=current_read)
    if payload["schema_version"] == 1 and "exclusions" in payload:
        raise ItemBindingError("item_binding_source_changed")
    if payload["schema_version"] == 2 and (not payload.get("exclusions") or "continuation" in payload):
        raise ItemBindingError("item_binding_source_changed")
    if payload["entries"] or payload.get("exclusions"):
        requests = [*payload["entries"], *payload.get("exclusions", [])]
        repo = _repository(
            connection,
            payload["case_id"],
            payload["snapshot_id"],
            requests,
            current_read=current_read,
        )
        validated_from_frozen({"payload": payload, "digest": digest(payload)}, definition, repo)


def _same_items(left: dict | None, right: dict | None) -> bool:
    """Only exact item identities survive rule edits, regardless of ordering."""
    return bool(left and right) and {
        item["item_id"]: item for item in left["items"]
    } == {item["item_id"]: item for item in right["items"]}


def _identity_continuous(
    connection: Connection, case_id: str, snapshot_id: str,
    previous: dict, version_id: str, definition: dict,
) -> bool:
    """Do not carry across identity changes later reverted to the same strings."""
    target = _read(connection, select(checklist_versions.c.version).where(
        checklist_versions.c.case_id == case_id, checklist_versions.c.id == version_id,
    ), True).one()["version"]
    rows = _read(connection, select(checklist_versions.c.id, checklist_versions.c.snapshot_id).where(
        checklist_versions.c.case_id == case_id,
        checklist_versions.c.version > previous["version"], checklist_versions.c.version <= target,
    ).order_by(checklist_versions.c.version).limit(101), True).all()
    if not 1 <= len(rows) <= 100 or len(rows) != target - previous["version"]:
        return False
    return all(row["snapshot_id"] == snapshot_id and _same_items(
        _version(connection, case_id, row["id"], current_read=True)["definition"], definition,
    ) for row in rows)


def _continuation_origin(
    connection: Connection, payload: dict, definition: dict | None, *, current_read: bool
) -> dict:
    """Resolve exactly one original confirmation; chains and cycles are invalid."""
    origin = payload["continuation"]
    if (
        not isinstance(origin, dict)
        or set(origin) != {"version_id", "digest"}
        or not isinstance(origin["version_id"], str)
        or not re.fullmatch(r"[a-f0-9]{32}", origin["version_id"])
        or not isinstance(origin["digest"], str)
        or not re.fullmatch(r"[a-f0-9]{64}", origin["digest"])
    ):
        raise ItemBindingError("item_binding_source_changed")
    row = _read(connection, select(item_binding_versions).where(
        item_binding_versions.c.case_id == payload["case_id"],
        item_binding_versions.c.id == origin["version_id"],
    ), current_read).first()
    if (
        not row
        or row["snapshot_id"] != payload["snapshot_id"]
        or row["content_digest"] != origin["digest"]
        or "continuation" in row["payload_json"]
        or row["payload_json"].get("schema_version") != 1
        or payload.get("schema_version") != 1
    ):
        raise ItemBindingError("item_binding_source_changed")
    original_definition = _version(connection, payload["case_id"], row["checklist_version_id"],
                                   current_read=current_read)["definition"]
    if not _same_items(original_definition, definition):
        raise ItemBindingError("item_binding_source_changed")
    frozen = load_frozen(connection, payload["case_id"], payload["snapshot_id"],
                         row["checklist_version_id"], original_definition, row["id"],
                         origin["digest"], current_read=current_read)
    if frozen["payload"]["entries"] != payload["entries"]:
        raise ItemBindingError("item_binding_source_changed")
    return dict(row)


def load_frozen(
    connection: Connection,
    case_id: str,
    snapshot_id: str,
    checklist_version_id: str | None,
    definition: ChecklistDefinition | dict | None,
    version_id: str | None,
    binding_digest: str,
    *,
    current_read: bool = False,
) -> dict:
    _check_access(connection, case_id, current_read=current_read)
    try:
        definition = _context(
            connection,
            case_id,
            snapshot_id,
            checklist_version_id,
            definition,
            current_read=current_read,
        )
        if version_id is None:
            payload = _empty(case_id, snapshot_id, checklist_version_id, definition)
        else:
            row = _read(
                connection,
                select(item_binding_versions).where(
                    item_binding_versions.c.case_id == case_id,
                    item_binding_versions.c.id == version_id,
                ),
                current_read,
            ).first()
            if (
                not row
                or row["snapshot_id"] != snapshot_id
                or row["checklist_version_id"] != checklist_version_id
            ):
                raise ItemBindingError("item_binding_source_changed")
            payload = row["payload_json"]
            if digest(payload) != row["content_digest"]:
                raise ItemBindingError("item_binding_source_changed")
        if (
            payload["case_id"] != case_id
            or payload["snapshot_id"] != snapshot_id
            or payload["checklist_version_id"] != checklist_version_id
            or digest(payload) != binding_digest
        ):
            raise ItemBindingError("item_binding_source_changed")
        _validate_payload(connection, payload, definition, current_read=current_read)
    except (ValueError, KeyError, TypeError):
        raise ItemBindingError("item_binding_source_changed") from None
    return {"version_id": version_id, "digest": binding_digest, "payload": payload}


def freeze_current(
    connection: Connection,
    case_id: str,
    snapshot_id: str,
    checklist_version_id: str | None,
    definition: ChecklistDefinition | dict | None,
    *,
    current_read: bool = True,
    allow_continuation: bool = False,
) -> dict:
    if allow_continuation:
        if not current_read:
            raise ItemBindingError("invalid_request")
        continue_current(connection, case_id, snapshot_id, checklist_version_id, definition)
    _check_access(connection, case_id, current_read=current_read)
    definition = _context(
        connection,
        case_id,
        snapshot_id,
        checklist_version_id,
        definition,
        current_read=current_read,
    )
    head = _read(
        connection,
        select(item_binding_heads).where(item_binding_heads.c.case_id == case_id),
        current_read,
    ).first()
    row = (
        _read(
            connection,
            select(item_binding_versions).where(
                item_binding_versions.c.id == head["version_id"],
                item_binding_versions.c.case_id == case_id,
            ),
            current_read,
        ).first()
        if head
        else None
    )
    if (
        row
        and checklist_version_id is not None
        and row["snapshot_id"] == snapshot_id
        and row["checklist_version_id"] == checklist_version_id
    ):
        return load_frozen(
            connection,
            case_id,
            snapshot_id,
            checklist_version_id,
            definition,
            row["id"],
            row["content_digest"],
            current_read=current_read,
        )
    payload = _empty(case_id, snapshot_id, checklist_version_id, definition)
    return {"version_id": None, "digest": digest(payload), "payload": payload}


def continue_current(
    connection: Connection,
    case_id: str,
    snapshot_id: str,
    checklist_version_id: str | None,
    definition: ChecklistDefinition | dict | None,
    *,
    actor: str | None = None,
) -> None:
    """Caller owns a write transaction; append an exact scope under the Case lock.

    Used by explicit checklist confirmations and new Run submissions only. Reads,
    replays and validation never repair a head as a side effect.
    """
    case = _check_access(connection, case_id, actor, current_read=True)
    definition = _context(connection, case_id, snapshot_id, checklist_version_id,
                          definition, current_read=True)
    if checklist_version_id is None:
        return
    snapshot = _read(connection, select(input_snapshots.c.revision).where(
        input_snapshots.c.case_id == case_id, input_snapshots.c.id == snapshot_id,
    ), True).first()
    checklist = _read(connection, select(checklist_heads).where(
        checklist_heads.c.case_id == case_id,
    ), True).first()
    if (case["revision"] != snapshot["revision"] or not checklist
        or checklist["snapshot_id"] != snapshot_id
        or checklist["confirmed_version_id"] != checklist_version_id
        or (checklist["draft_json"] is not None
            and checklist["draft_base_version_id"] != checklist_version_id)):
        return
    head = _read(connection, select(item_binding_heads).where(
        item_binding_heads.c.case_id == case_id,
    ), True).first()
    if not head:
        return
    row = _read(connection, select(item_binding_versions).where(
        item_binding_versions.c.case_id == case_id,
        item_binding_versions.c.id == head["version_id"],
    ), True).first()
    if not row or row["version"] != head["revision"]:
        raise ItemBindingError("item_binding_source_changed")
    if row["snapshot_id"] != snapshot_id or row["checklist_version_id"] == checklist_version_id:
        return
    if row["payload_json"].get("schema_version") == 2:
        return
    previous = _version(connection, case_id, row["checklist_version_id"], current_read=True)
    if (not _same_items(previous["definition"], definition)
        or not _identity_continuous(connection, case_id, snapshot_id, previous,
                                    checklist_version_id, definition)):
        return
    old = load_frozen(connection, case_id, snapshot_id, row["checklist_version_id"],
                      previous["definition"], row["id"], row["content_digest"], current_read=True)
    active = _read(connection, select(agent_runs.c.id).where(
        agent_runs.c.case_id == case_id,
        agent_runs.c.status.in_(("queued", "running", "waiting_user")),
    ).limit(1), True).first()
    if active:
        raise ItemBindingError("case_busy")
    payload = _empty(case_id, snapshot_id, checklist_version_id, definition)
    payload["entries"] = old["payload"]["entries"]
    payload["continuation"] = old["payload"].get("continuation", {
        "version_id": row["id"], "digest": row["content_digest"],
    })
    origin = _continuation_origin(connection, payload, definition, current_read=True)
    _validate_payload(connection, payload, definition, current_read=True)
    version_id, revision = uuid4().hex, head["revision"] + 1
    connection.execute(insert(item_binding_versions).values(
        id=version_id, case_id=case_id, snapshot_id=snapshot_id,
        checklist_version_id=checklist_version_id, version=revision,
        payload_json=payload, content_digest=digest(payload), confirmed_by=origin["confirmed_by"],
        reason=origin["reason"], created_at=datetime.now(UTC).replace(tzinfo=None),
    ))
    changed = connection.execute(update(item_binding_heads).where(
        item_binding_heads.c.case_id == case_id,
        item_binding_heads.c.revision == head["revision"],
        item_binding_heads.c.version_id == row["id"],
    ).values(version_id=version_id, revision=revision))
    if changed.rowcount != 1:
        raise ItemBindingError("item_binding_revision_conflict")


def load_run_bindings(connection: Connection, row, definition: dict | None,
                      *, current_read: bool = False) -> dict | None:
    """NULL/NULL replays pre-0017 semantics; a non-null digest freezes even empty scope."""
    if row.get("item_binding_digest") is None:
        if row.get("item_binding_version_id") is not None:
            raise ItemBindingError("item_binding_source_changed")
        return None
    return load_frozen(connection, row["case_id"], row["snapshot_id"], row["checklist_version_id"] if definition is not None else None,
                       definition, row["item_binding_version_id"], row["item_binding_digest"],
                       current_read=current_read)


def run_bindings_current(connection: Connection, row, definition: dict | None) -> bool:
    if row.get("item_binding_digest") is None:
        return connection.scalar(select(item_binding_heads.c.case_id).where(
            item_binding_heads.c.case_id == row["case_id"])) is None
    current = freeze_current(connection, row["case_id"], row["snapshot_id"],
                             row["checklist_version_id"] if definition is not None else None, definition, current_read=False)
    return current["version_id"] == row["item_binding_version_id"] and current["digest"] == row["item_binding_digest"]


class ItemBindingRepository:
    def __init__(self, engine: Engine):
        self.engine = engine

    def _view(self, connection: Connection, case_id: str, snapshot_id: str) -> dict:
        case = _check_access(connection, case_id)
        revision = connection.scalar(
            select(input_snapshots.c.revision).where(
                input_snapshots.c.case_id == case_id,
                input_snapshots.c.id == snapshot_id,
            )
        )
        if revision is None:
            raise ItemBindingError("not_found")
        head = _read(
            connection,
            select(item_binding_heads).where(item_binding_heads.c.case_id == case_id),
            True,
        ).first()
        current, historical = None, None
        if head:
            row = self._version(connection, case_id, head["version_id"])
            checklist = _read(
                connection,
                select(checklist_heads).where(checklist_heads.c.case_id == case_id),
                True,
            ).first()
            if (
                case["revision"] == revision
                and row["snapshot_id"] == snapshot_id
                and checklist
                and checklist["snapshot_id"] == snapshot_id
                and checklist["confirmed_version_id"] == row["checklist_version_id"]
            ):
                current = row
            else:
                historical = row
        return {
            "case_id": case_id,
            "snapshot_id": snapshot_id,
            "binding_revision": head["revision"] if head else 0,
            "current_version": current,
            "historical_version": historical,
            "state": "confirmed" if current else "stale" if historical else "empty",
        }

    def get(self, case_id: str, snapshot_id: str) -> dict:
        with self.engine.begin() as connection:
            _check_access(connection, case_id, current_read=True)
            return self._view(connection, case_id, snapshot_id)

    def _version(self, connection: Connection, case_id: str, version_id: str) -> dict:
        row = _read(
            connection,
            select(item_binding_versions).where(
                item_binding_versions.c.case_id == case_id,
                item_binding_versions.c.id == version_id,
            ),
            False,
        ).first()
        if not row:
            raise ItemBindingError("not_found")
        definition = _version(connection, case_id, row["checklist_version_id"])[
            "definition"
        ]
        envelope = load_frozen(
            connection,
            case_id,
            row["snapshot_id"],
            row["checklist_version_id"],
            definition,
            row["id"],
            row["content_digest"],
        )
        confirmation = (_continuation_origin(connection, envelope["payload"], definition, current_read=False)
                        if "continuation" in envelope["payload"] else row)
        return {
            "id": row["id"],
            "version": row["version"],
            "snapshot_id": row["snapshot_id"],
            "checklist_version_id": row["checklist_version_id"],
            "digest": envelope["digest"],
            "payload": envelope["payload"],
            "confirmed_by": row["confirmed_by"],
            "reason": row["reason"],
            "confirmed_at": confirmation["created_at"].replace(tzinfo=UTC).isoformat(),
        }

    def version(self, case_id: str, version_id: str) -> dict:
        with self.engine.connect() as connection:
            _check_access(connection, case_id)
            return self._version(connection, case_id, version_id)

    def put(
        self,
        case_id: str,
        snapshot_id: str,
        entries: list[dict],
        *,
        actor: str,
        checklist_version_id: str,
        expected_revision: int,
        expected_checklist_revision: int,
        expected_binding_revision: int,
        confirmed: bool,
        idempotency_key: str,
        reason: str,
        exclusions: list[dict] | None = None,
        exclusions_confirmed: bool | None = None,
    ) -> dict:
        if confirmed is not True:
            raise ItemBindingError("item_binding_confirmation_required")
        if (
            not isinstance(reason, str)
            or not reason.strip()
            or len(reason) > 1000
            or not idempotency_key.strip()
            or len(idempotency_key) > 255
        ):
            raise ItemBindingError("invalid_request")
        try:
            requests = [
                ItemBindingEntry.model_validate(e).model_dump(mode="json")
                for e in entries
            ]
            exclusion_requests = ([PdfPageExclusion.model_validate(e).model_dump(mode="json") for e in exclusions]
                                  if isinstance(exclusions, list) else [])
            if exclusions is not None and not isinstance(exclusions, list):
                raise ItemBindingError("item_exclusion_invalid")
            if exclusion_requests and exclusions_confirmed is not True:
                raise ItemBindingError("item_exclusion_confirmation_required")
            if (
                len(requests) > 140
                or len(exclusion_requests) > 30
                or len(json.dumps([requests, exclusion_requests], ensure_ascii=False).encode()) > 128_000
            ):
                raise ItemBindingError("item_binding_capacity")
        except ItemBindingError:
            raise
        except (ValueError, TypeError):
            raise ItemBindingError("item_binding_invalid") from None
        identity = [
                case_id,
                snapshot_id,
                requests,
                checklist_version_id,
                expected_revision,
                expected_checklist_revision,
                expected_binding_revision,
                confirmed,
                reason,
            ]
        if exclusions is not None or exclusions_confirmed is not None:
            identity.append({"exclusions": exclusion_requests if exclusions is not None else None,
                             "exclusions_confirmed": exclusions_confirmed})
        request_digest = digest(identity)
        with self.engine.begin() as connection:
            _check_access(connection, case_id, actor)
            receipt = _read(
                connection,
                select(item_binding_operations).where(
                    item_binding_operations.c.case_id == case_id,
                    item_binding_operations.c.principal_key == actor,
                    item_binding_operations.c.idempotency_key == idempotency_key,
                ),
                True,
            ).first()
            if receipt:
                if receipt["request_digest"] != request_digest:
                    raise ItemBindingError("idempotency_conflict")
                return receipt["response_json"]
            try:
                _scope(
                    connection,
                    case_id,
                    snapshot_id,
                    expected_revision=expected_revision,
                    actor=actor,
                )
                checklist = _read(
                    connection,
                    select(checklist_heads).where(checklist_heads.c.case_id == case_id),
                    True,
                ).first()
                if (
                    not checklist
                    or checklist["snapshot_id"] != snapshot_id
                    or checklist["revision"] != expected_checklist_revision
                    or checklist["confirmed_version_id"] != checklist_version_id
                    or (
                        checklist["draft_json"] is not None
                        and checklist["draft_base_version_id"] != checklist_version_id
                    )
                ):
                    raise ItemBindingError("item_binding_checklist_changed")
                definition = _version(
                    connection, case_id, checklist_version_id, current_read=True
                )["definition"]
            except ChecklistError as exc:
                raise ItemBindingError(exc.code) from None
            active = _read(
                connection,
                select(agent_runs.c.id)
                .where(
                    agent_runs.c.case_id == case_id,
                    agent_runs.c.status.in_(("queued", "running")),
                )
                .limit(1),
                True,
            ).first()
            if active:
                raise ItemBindingError("case_busy")
            head = _read(
                connection,
                select(item_binding_heads).where(
                    item_binding_heads.c.case_id == case_id
                ),
                True,
            ).first()
            if (head["revision"] if head else 0) != expected_binding_revision:
                raise ItemBindingError("item_binding_revision_conflict")
            previous = (_read(connection, select(item_binding_versions.c.payload_json).where(
                item_binding_versions.c.id == head["version_id"], item_binding_versions.c.case_id == case_id), True).first()
                if head else None)
            if previous and previous["payload_json"].get("schema_version") == 2 and (
                    exclusions is None or exclusions_confirmed is not True):
                raise ItemBindingError("item_exclusion_confirmation_required")
            payload = _empty(case_id, snapshot_id, checklist_version_id, definition)
            if requests or exclusion_requests:
                try:
                    repo = _repository(
                        connection, case_id, snapshot_id, [*requests, *exclusion_requests], current_read=True
                    )
                    bound = validate_bindings(requests, definition, repo)
                    payload["entries"] = bound.payload()
                    if exclusion_requests:
                        excluded = validate_exclusions(exclusion_requests, ChecklistDefinition.model_validate(definition), repo, bound)
                        payload.update(schema_version=2, exclusions=[e.payload() for e in excluded])
                except ItemBindingError:
                    raise
                except (ValueError, KeyError, TypeError) as exc:
                    if str(exc).startswith("item_exclusion_"):
                        raise ItemBindingError(str(exc)) from None
                    raise ItemBindingError("item_binding_invalid") from None
            now, version_id = datetime.now(UTC).replace(tzinfo=None), uuid4().hex
            revision = expected_binding_revision + 1
            connection.execute(
                insert(item_binding_versions).values(
                    id=version_id,
                    case_id=case_id,
                    snapshot_id=snapshot_id,
                    checklist_version_id=checklist_version_id,
                    version=revision,
                    payload_json=payload,
                    content_digest=digest(payload),
                    confirmed_by=actor,
                    reason=reason,
                    created_at=now,
                )
            )
            if head:
                connection.execute(
                    update(item_binding_heads)
                    .where(item_binding_heads.c.case_id == case_id)
                    .values(version_id=version_id, revision=revision)
                )
            else:
                connection.execute(
                    insert(item_binding_heads).values(
                        case_id=case_id, version_id=version_id, revision=revision
                    )
                )
            response = self._view(connection, case_id, snapshot_id)
            connection.execute(
                insert(item_binding_operations).values(
                    id=uuid4().hex,
                    case_id=case_id,
                    principal_key=actor,
                    idempotency_key=idempotency_key,
                    request_digest=request_digest,
                    response_json=response,
                    created_at=now,
                )
            )
            return response
