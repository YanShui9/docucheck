"""Transactional checklist edits and immutable user confirmations; never calls a model."""

import json
import re
from datetime import UTC, datetime
from uuid import uuid4

from pydantic import ValidationError
from sqlalchemy import Engine, func, insert, select, update
from sqlalchemy.exc import IntegrityError

from ..criteria.models import ChecklistDefinition
from ..evidence.repository import digest
from .models import (
    agent_runs,
    cases,
    checklist_heads,
    checklist_operations,
    checklist_versions,
    document_versions,
    evidence_blocks,
    input_snapshots,
    project_members,
)


class ChecklistError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def ensure_legacy_run_allowed(connection, case_id):
    """The existing runner has no criterion IDs; never silently execute eight fields."""
    if connection.scalar(
        select(checklist_heads.c.case_id)
        .where(checklist_heads.c.case_id == case_id)
        .with_for_update()
    ):
        # A prior ordinary SELECT may have established a MySQL RR read view.
        # The caller owns the Case lock; this current read sees any head that
        # committed before that lock was acquired.
        raise ChecklistError("checklist_execution_pending")


def _definition(value):
    try:
        result = ChecklistDefinition.model_validate(value)
    except ValidationError:
        raise ChecklistError("invalid_checklist") from None
    if (
        len(json.dumps(result.model_dump(mode="json"), ensure_ascii=False).encode())
        > 128_000
    ):
        raise ChecklistError("checklist_capacity")
    return result


def _scope(
    connection, case_id, snapshot_id=None, *, expected_revision=None, actor=None
):
    query = select(cases).where(cases.c.id == case_id)
    if actor is not None:
        query = query.with_for_update()
    case = connection.execute(query).mappings().first()
    if case is None:
        raise ChecklistError("not_found")
    if case["deletion_requested_at"] is not None:
        raise ChecklistError("case_deleting")
    if actor is not None and case["project_id"] is not None:
        role = connection.scalar(
            select(project_members.c.role)
            .where(
                project_members.c.project_id == case["project_id"],
                project_members.c.principal_key == actor,
            )
            .with_for_update()
        )
        if role not in {"editor", "owner"}:
            raise ChecklistError("not_found")
    if snapshot_id is None:
        return None
    revision = connection.scalar(
        select(input_snapshots.c.revision).where(
            input_snapshots.c.case_id == case_id, input_snapshots.c.id == snapshot_id
        )
    )
    if revision is None:
        raise ChecklistError("not_found")
    if expected_revision is not None and (
        revision != expected_revision or case["revision"] != revision
    ):
        raise ChecklistError("checklist_snapshot_changed")
    return revision


def _bindings(connection, case_id, snapshot_id, definition, *, current_read=False):
    revision = connection.scalar(select(input_snapshots.c.revision).where(
        input_snapshots.c.case_id == case_id, input_snapshots.c.id == snapshot_id))
    if revision == 0 and (any(c.source.kind != "user_defined" or c.source.context is not None for c in definition.criteria)
                         or connection.scalar(select(document_versions.c.id).where(
                             document_versions.c.case_id == case_id, document_versions.c.snapshot_id == snapshot_id).limit(1)) is not None):
        raise ChecklistError("checklist_source_scope")
    bindings = []
    for criterion in definition.criteria:
        context = criterion.source.context
        if context is None:
            continue
        query = (
            select(
                evidence_blocks,
                document_versions.c.source_sha256,
                document_versions.c.parser_version,
                document_versions.c.coverage_json,
            )
            .join(
                document_versions,
                document_versions.c.id == evidence_blocks.c.document_version_id,
            )
            .where(
                evidence_blocks.c.id.in_(context.evidence_refs),
                document_versions.c.case_id == case_id,
                document_versions.c.snapshot_id == snapshot_id,
                document_versions.c.role == "requirement",
                document_versions.c.supplier_id.is_(None),
                document_versions.c.parse_status.in_(("complete", "partial")),
            )
        )
        if current_read:
            query = query.with_for_update()
        rows = connection.execute(query).mappings().all()
        by_id = {r["id"]: r for r in rows}
        if len(by_id) != len(context.evidence_refs):
            raise ChecklistError("checklist_source_scope")
        if criterion.source.kind == "document_extracted" and any(
            r["source_text"].strip() == "" for r in rows
        ):
            raise ChecklistError("checklist_source_scope")
        # Only consecutive cells in one row can prove a joint statement.
        normalize = lambda text: re.sub(r"\s+", " ", text).strip()
        quote = normalize(context.quote)
        texts = [normalize(r["source_text"]) for r in rows]
        if len(rows) > 1:
            from openpyxl.utils.cell import coordinate_to_tuple

            if all(
                r["document_version_id"] == rows[0]["document_version_id"]
                and r["sheet"] == rows[0]["sheet"]
                and r["sheet"]
                and r["cell"]
                for r in rows
            ):
                try:
                    ordered = sorted(rows, key=lambda r: coordinate_to_tuple(r["cell"]))
                    positions = [coordinate_to_tuple(r["cell"]) for r in ordered]
                    if all(
                        row == positions[0][0] and col == positions[0][1] + n
                        for n, (row, col) in enumerate(positions)
                    ):
                        texts.append(
                            normalize(" ".join(r["source_text"] for r in ordered))
                        )
                except ValueError:
                    raise ChecklistError("checklist_source_scope") from None
        if not quote or not any(quote in text for text in texts):
            raise ChecklistError("checklist_source_scope")
        if criterion.source.kind == "document_extracted":
            from ..criteria.validation import compact

            combined = ordered if len(rows) > 1 and len(texts) > len(rows) else rows
            if compact(context.quote) != compact(
                " ".join(r["source_text"] for r in combined)
            ):
                raise ChecklistError("checklist_source_scope")
        bindings.append(
            {
                "criterion_id": criterion.criterion_id,
                "fingerprint": digest(
                    [
                        {k: v for k, v in by_id[ref].items() if k != "created_at"}
                        for ref in context.evidence_refs
                    ]
                ),
            }
        )
    return bindings


def _validated(
    connection,
    case_id,
    snapshot_id,
    payload,
    bindings,
    fingerprint,
    *,
    current_read=False,
):
    definition = _definition(payload)
    if digest([payload, bindings]) != fingerprint:
        raise ChecklistError("checklist_source_changed")
    try:
        actual = _bindings(
            connection, case_id, snapshot_id, definition, current_read=current_read
        )
    except ChecklistError:
        raise ChecklistError("checklist_source_changed") from None
    if actual != bindings:
        raise ChecklistError("checklist_source_changed")
    return definition


def _version(connection, case_id, version_id, *, current_read=False):
    query = select(checklist_versions).where(
        checklist_versions.c.case_id == case_id, checklist_versions.c.id == version_id
    )
    if current_read:
        query = query.with_for_update()
    row = connection.execute(query).mappings().first()
    if row is None:
        raise ChecklistError("not_found")
    definition = _validated(
        connection,
        case_id,
        row["snapshot_id"],
        row["definition_json"],
        row["source_bindings_json"],
        row["content_digest"],
        current_read=current_read,
    )
    return {
        "id": row["id"],
        "version": row["version"],
        "snapshot_id": row["snapshot_id"],
        "revision": row["input_revision"],
        "definition": definition.model_dump(mode="json"),
        "confirmed_by": row["confirmed_by"],
        "reason": row["reason"],
        "manual_review_ids": [
            c.criterion_id for c in definition.criteria if c.requires_manual_review
        ],
    }


def _view(connection, case_id, snapshot_id, input_revision):
    is_current_snapshot = (
        connection.scalar(select(cases.c.revision).where(cases.c.id == case_id))
        == input_revision
    )
    row = (
        connection.execute(
            select(checklist_heads).where(checklist_heads.c.case_id == case_id)
        )
        .mappings()
        .first()
    )
    draft, current, issues = None, None, []
    state = "empty"
    if row is not None:
        if row["snapshot_id"] != snapshot_id:
            state = "needs_reconfirmation"
        elif row["draft_json"] is not None:
            definition = _validated(
                connection,
                case_id,
                snapshot_id,
                row["draft_json"],
                row["source_bindings_json"],
                row["draft_digest"],
            )
            draft, issues = definition.model_dump(mode="json"), definition.issues()
            state = "needs_resolution" if issues else "draft"
        if row["confirmed_version_id"] is not None:
            version = _version(connection, case_id, row["confirmed_version_id"])
            if version["snapshot_id"] == snapshot_id:
                current = version
                if draft is None:
                    state = "confirmed"
                elif row["draft_base_version_id"] == row["confirmed_version_id"]:
                    state = "partially_confirmed"
            elif draft is None:
                state = "needs_reconfirmation"
    if not is_current_snapshot:
        state = "historical"
    return {
        "case_id": case_id,
        "snapshot_id": snapshot_id,
        "revision": input_revision,
        "is_current_snapshot": is_current_snapshot,
        "checklist_revision": row["revision"] if row else 0,
        "state": state,
        "draft": draft,
        "confirmed_version": current,
        "previous_version_id": row["confirmed_version_id"] if row else None,
        "issues": issues,
        "execution_available": bool(
            is_current_snapshot
            and current is not None
            and state in {"confirmed", "partially_confirmed"}
        ),
    }


class ChecklistRepository:
    def __init__(self, engine: Engine):
        self.engine = engine

    def get(self, case_id, snapshot_id):
        with self.engine.connect() as connection:
            revision = _scope(connection, case_id, snapshot_id)
            return _view(connection, case_id, snapshot_id, revision)

    def version(self, case_id, version_id):
        with self.engine.connect() as connection:
            _scope(connection, case_id)
            return _version(connection, case_id, version_id)

    def put(
        self,
        case_id,
        snapshot_id,
        definition,
        *,
        actor,
        expected_revision,
        expected_checklist_revision,
        idempotency_key,
        reason,
    ):
        body = _definition(definition).model_dump(mode="json")
        return self._edit(
            "save",
            case_id,
            snapshot_id,
            body,
            actor,
            expected_revision,
            expected_checklist_revision,
            idempotency_key,
            reason,
        )

    def confirm(
        self,
        case_id,
        snapshot_id,
        criterion_ids,
        *,
        actor,
        expected_revision,
        expected_checklist_revision,
        confirmed,
        idempotency_key,
        reason,
    ):
        if confirmed is not True:
            raise ChecklistError("checklist_confirmation_required")
        if (
            not isinstance(criterion_ids, list)
            or not 1 <= len(criterion_ids) <= 100
            or any(not isinstance(i, str) for i in criterion_ids)
            or len(set(criterion_ids)) != len(criterion_ids)
        ):
            raise ChecklistError("invalid_checklist_selection")
        return self._edit(
            "confirm",
            case_id,
            snapshot_id,
            criterion_ids,
            actor,
            expected_revision,
            expected_checklist_revision,
            idempotency_key,
            reason,
        )

    def _replay(self, connection, case_id, actor, operation, key, fingerprint):
        row = (
            connection.execute(
                select(checklist_operations).where(
                    checklist_operations.c.case_id == case_id,
                    checklist_operations.c.principal_key == actor,
                    checklist_operations.c.operation == operation,
                    checklist_operations.c.idempotency_key == key,
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            return None
        if row["request_digest"] != fingerprint:
            raise ChecklistError("idempotency_conflict")
        return row["response_json"]

    def _edit(
        self,
        operation,
        case_id,
        snapshot_id,
        body,
        actor,
        input_revision,
        revision,
        key,
        reason,
    ):
        if (
            not isinstance(actor, str)
            or not re.fullmatch(r"[a-f0-9]{64}", actor)
            or type(input_revision) is not int
            or input_revision < 0
            or type(revision) is not int
            or revision < 0
            or not isinstance(key, str)
            or not 1 <= len(key) <= 255
            or not isinstance(reason, str)
            or not reason.strip()
            or len(reason) > 1000
        ):
            raise ChecklistError("invalid_checklist")
        fingerprint = digest(
            [operation, case_id, snapshot_id, body, input_revision, revision, reason]
        )
        try:
            with self.engine.begin() as connection:
                _scope(
                    connection,
                    case_id,
                    snapshot_id,
                    expected_revision=input_revision,
                    actor=actor,
                )
                replay = self._replay(
                    connection, case_id, actor, operation, key, fingerprint
                )
                if replay is not None:
                    return replay
                head = (
                    connection.execute(
                        select(checklist_heads).where(
                            checklist_heads.c.case_id == case_id
                        )
                    )
                    .mappings()
                    .first()
                )
                if (head["revision"] if head else 0) != revision:
                    raise ChecklistError("checklist_revision_conflict")
                busy = connection.scalar(
                    select(agent_runs.c.id)
                    .where(
                        agent_runs.c.case_id == case_id,
                        agent_runs.c.status.in_(("queued", "running", "waiting_user")),
                    )
                    .limit(1)
                    .with_for_update()
                )
                if busy is not None:
                    raise ChecklistError("case_busy")
                now = datetime.now(UTC).replace(tzinfo=None)
                if operation == "save":
                    definition = _definition(body)
                    bindings = _bindings(connection, case_id, snapshot_id, definition)
                    values = {
                        "draft_json": body,
                        "source_bindings_json": bindings,
                        "draft_digest": digest([body, bindings]),
                        "draft_base_version_id": None,
                    }
                else:
                    if (
                        head is None
                        or head["snapshot_id"] != snapshot_id
                        or head["draft_json"] is None
                    ):
                        raise ChecklistError("checklist_draft_missing")
                    definition = _validated(
                        connection,
                        case_id,
                        snapshot_id,
                        head["draft_json"],
                        head["source_bindings_json"],
                        head["draft_digest"],
                    )
                    allowed = {c.criterion_id for c in definition.criteria if c.enabled}
                    if not set(body).issubset(allowed):
                        raise ChecklistError("invalid_checklist_selection")
                    selected = definition.selected(body)
                    if head["draft_base_version_id"] is not None:
                        base = _version(
                            connection, case_id, head["draft_base_version_id"]
                        )
                        if base["snapshot_id"] != snapshot_id:
                            raise ChecklistError("checklist_snapshot_changed")
                        prior = _definition(base["definition"])
                        merged_items = {item.item_id: item for item in prior.items}
                        merged_items.update(
                            {item.item_id: item for item in selected.items}
                        )
                        selected = ChecklistDefinition(
                            items=list(merged_items.values()),
                            criteria=[*prior.criteria, *selected.criteria],
                        )
                    if selected.issues():
                        raise ChecklistError("checklist_needs_resolution")
                    selected_json = selected.model_dump(mode="json")
                    bindings = _bindings(connection, case_id, snapshot_id, selected)
                    version_id = uuid4().hex
                    connection.execute(
                        insert(checklist_versions).values(
                            id=version_id,
                            case_id=case_id,
                            snapshot_id=snapshot_id,
                            input_revision=input_revision,
                            version=(
                                connection.scalar(
                                    select(
                                        func.coalesce(
                                            func.max(checklist_versions.c.version), 0
                                        )
                                    ).where(checklist_versions.c.case_id == case_id)
                                )
                                + 1
                            ),
                            definition_json=selected_json,
                            source_bindings_json=bindings,
                            content_digest=digest([selected_json, bindings]),
                            confirmed_by=actor,
                            reason=reason,
                            created_at=now,
                        )
                    )
                    remaining = [
                        c.criterion_id
                        for c in definition.criteria
                        if c.criterion_id not in body
                    ]
                    rest = definition.selected(remaining) if remaining else None
                    rest_json = rest.model_dump(mode="json") if rest else None
                    rest_bindings = (
                        _bindings(connection, case_id, snapshot_id, rest)
                        if rest
                        else None
                    )
                    values = {
                        "draft_json": rest_json,
                        "source_bindings_json": rest_bindings,
                        "draft_digest": digest([rest_json, rest_bindings])
                        if rest
                        else None,
                        "confirmed_version_id": version_id,
                        "draft_base_version_id": version_id if rest else None,
                    }
                values.update(
                    revision=revision + 1,
                    snapshot_id=snapshot_id,
                    updated_by=actor,
                    updated_at=now,
                )
                if head is None:
                    connection.execute(
                        insert(checklist_heads).values(case_id=case_id, **values)
                    )
                else:
                    changed = connection.execute(
                        update(checklist_heads)
                        .where(
                            checklist_heads.c.case_id == case_id,
                            checklist_heads.c.revision == revision,
                        )
                        .values(**values)
                    )
                    if changed.rowcount != 1:
                        raise ChecklistError("checklist_revision_conflict")
                response = _view(connection, case_id, snapshot_id, input_revision)
                if operation == "confirm":
                    response["excluded_criterion_ids"] = remaining
                connection.execute(
                    insert(checklist_operations).values(
                        id=uuid4().hex,
                        case_id=case_id,
                        principal_key=actor,
                        operation=operation,
                        idempotency_key=key,
                        request_digest=fingerprint,
                        response_json=response,
                        created_at=now,
                    )
                )
                return response
        except IntegrityError:
            with self.engine.begin() as connection:
                _scope(
                    connection,
                    case_id,
                    snapshot_id,
                    expected_revision=input_revision,
                    actor=actor,
                )
                replay = self._replay(
                    connection, case_id, actor, operation, key, fingerprint
                )
                if replay is not None:
                    return replay
            raise ChecklistError("checklist_revision_conflict") from None
