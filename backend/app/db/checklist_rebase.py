"""Conservative mapping of confirmed requirement evidence into a new input snapshot."""

from copy import deepcopy

from sqlalchemy import Connection, select

from ..evidence.repository import digest
from .models import document_versions, evidence_blocks


def rebind_definition(
    connection: Connection, case_id: str, snapshot_id: str, version: dict
) -> dict:
    from .checklist_repository import ChecklistError

    definition = deepcopy(version["definition"])
    refs = {ref for criterion in definition["criteria"]
            for ref in (criterion["source"].get("context") or {}).get("evidence_refs", [])}
    if not refs:
        return definition
    query = select(evidence_blocks, document_versions.c.source_sha256,
                   document_versions.c.original_name, document_versions.c.media_type,
                   document_versions.c.parser_version, document_versions.c.coverage_json).join(
        document_versions, document_versions.c.id == evidence_blocks.c.document_version_id
    ).where(document_versions.c.case_id == case_id,
            document_versions.c.role == "requirement", document_versions.c.supplier_id.is_(None),
            document_versions.c.parse_status.in_(("complete", "partial")))
    old_rows = connection.execute(query.where(
        document_versions.c.snapshot_id == version["snapshot_id"], evidence_blocks.c.id.in_(refs)
    )).mappings().all()
    if len(old_rows) != len(refs):
        raise ChecklistError("checklist_rebase_unresolved")
    # A checklist references at most 400 blocks. Restrict the new read to their
    # document hashes and ordinals, rather than loading the complete document set.
    new_rows = connection.execute(query.where(
        document_versions.c.snapshot_id == snapshot_id,
        document_versions.c.source_sha256.in_({row["source_sha256"] for row in old_rows}),
        evidence_blocks.c.ordinal.in_({row["ordinal"] for row in old_rows}),
    )).mappings().all()

    def identity(row) -> str:
        return digest({key: value for key, value in row.items()
                       if key not in {"id", "document_version_id", "created_at"}})

    candidates: dict[str, list[str]] = {}
    for row in new_rows:
        candidates.setdefault(identity(row), []).append(row["id"])
    mapping: dict[str, str] = {}
    for row in old_rows:
        matches = candidates.get(identity(row), [])
        if len(matches) != 1:
            # No fuzzy match and no automatic conversion to a user requirement.
            raise ChecklistError("checklist_rebase_unresolved")
        mapping[row["id"]] = matches[0]
    for criterion in definition["criteria"]:
        context = criterion["source"].get("context")
        if context:
            context["evidence_refs"] = [mapping[ref] for ref in context["evidence_refs"]]
    return definition
