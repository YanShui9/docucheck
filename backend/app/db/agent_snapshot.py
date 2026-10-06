"""Load a parsed database snapshot into the existing Agent evidence contract."""

from collections import defaultdict

from sqlalchemy import Engine, select

from ..evidence.models import Document, ScopedEvidence
from ..evidence.repository import EvidenceRepository
from ..parsing import EvidenceBlock, ParseResult
from .models import document_versions, evidence_blocks, input_snapshots, suppliers

MAX_AGENT_BLOCKS = 25_000
MAX_AGENT_TEXT_BYTES = 50 * 1024 * 1024


class SnapshotLoadError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def load_agent_snapshot(engine: Engine, case_id: str,
                        snapshot_id: str, *, document_ids: tuple[str, ...] | None = None,
                        allow_empty: bool = False) -> EvidenceRepository:
    if document_ids is not None and (not 1 <= len(document_ids) <= 7
            or len(set(document_ids)) != len(document_ids)):
        raise SnapshotLoadError("snapshot_document_scope")
    with engine.connect() as connection:
        revision = connection.scalar(select(input_snapshots.c.revision).where(
            input_snapshots.c.case_id == case_id,
            input_snapshots.c.id == snapshot_id,
        ))
        if revision is None:
            raise SnapshotLoadError("snapshot_not_found")
        document_rows = connection.execute(select(
            document_versions, suppliers.c.handle.label("supplier_handle"),
        ).select_from(document_versions.outerjoin(
            suppliers, document_versions.c.supplier_id == suppliers.c.id,
        )).where(
            document_versions.c.case_id == case_id,
            document_versions.c.snapshot_id == snapshot_id,
            document_versions.c.id.in_(document_ids) if document_ids is not None else True,
        ).order_by(document_versions.c.id)).mappings().all()
        if document_ids is not None and {r["id"] for r in document_rows} != set(document_ids):
            raise SnapshotLoadError("snapshot_document_scope")
        if allow_empty and revision == 0 and not document_rows and document_ids is None:
            return EvidenceRepository.empty(case_id, snapshot_id)
        if not document_rows or any(row["parse_status"] not in {"complete", "partial"}
                                    for row in document_rows):
            raise SnapshotLoadError("snapshot_not_ready")
        document_ids = [row["id"] for row in document_rows]
        source_rows = connection.execute(select(evidence_blocks).where(
            evidence_blocks.c.document_version_id.in_(document_ids),
        ).order_by(evidence_blocks.c.document_version_id,
                   evidence_blocks.c.ordinal).limit(MAX_AGENT_BLOCKS + 1)).mappings().all()
    if len(source_rows) > MAX_AGENT_BLOCKS:
        raise SnapshotLoadError("snapshot_capacity")

    grouped: dict[str, list] = defaultdict(list)
    total_bytes = 0
    for row in source_rows:
        total_bytes += len(row["source_text"].encode("utf-8"))
        if total_bytes > MAX_AGENT_TEXT_BYTES:
            raise SnapshotLoadError("snapshot_capacity")
        grouped[row["document_version_id"]].append(row)

    documents: list[Document] = []
    scoped: list[ScopedEvidence] = []
    for row in document_rows:
        coverage = row["coverage_json"] or {}
        version = coverage.get("parser_version_full")
        rows = grouped[row["id"]]
        if not version or not rows or any(item["ordinal"] != index
                                           for index, item in enumerate(rows)):
            raise SnapshotLoadError("snapshot_not_ready")
        if row["role"] == "requirement" and row["supplier_handle"] is not None:
            raise SnapshotLoadError("snapshot_not_ready")
        if row["role"] != "requirement" and not row["supplier_handle"]:
            raise SnapshotLoadError("snapshot_not_ready")
        blocks = []
        for item in rows:
            bbox = item["bbox_json"] or {}
            values = {"evidence_id": item["id"], "document_id": row["id"],
                      "text": item["source_text"], "page_idx": item["page_idx"],
                      "bbox": tuple(bbox["bbox"]) if bbox.get("bbox") else None,
                      "page_width_pt": bbox.get("page_width_pt"),
                      "page_height_pt": bbox.get("page_height_pt"),
                      "sheet": item["sheet"], "cell": item["cell"]}
            blocks.append(EvidenceBlock(**values))
            scoped.append(ScopedEvidence(
                **values, role=row["role"], supplier=row["supplier_handle"],
                model=row["model_name"], source_hash=row["source_sha256"],
                parser_version=version, ordinal=item["ordinal"],
            ))
        parsed = ParseResult(
            document_id=row["id"], parser_version=version,
            status=row["parse_status"], blocks=tuple(blocks),
            total_pages=coverage.get("total_pages"),
            processed_pages=tuple(coverage.get("processed_pages") or ()),
            total_sheets=coverage.get("total_sheets"),
            processed_sheets=tuple(coverage.get("processed_sheets") or ()),
            coverage_issues=tuple(coverage.get("coverage_issues") or ()),
        )
        documents.append(Document(
            document_id=row["id"], source_hash=row["source_sha256"],
            role=row["role"], supplier=row["supplier_handle"],
            model=row["model_name"], parsed=parsed,
        ))
    try:
        return EvidenceRepository(case_id, revision, documents,
                                  snapshot_id=snapshot_id,
                                  scoped_blocks=tuple(scoped),
                                  document_names={row["id"]: row["original_name"]
                                                  for row in document_rows})
    except ValueError as exc:
        raise SnapshotLoadError("snapshot_not_ready") from exc
