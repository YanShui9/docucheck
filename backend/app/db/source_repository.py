"""Re-read authorized original evidence from SQL before disclosing cached text."""
from collections.abc import Callable

from sqlalchemy import Engine, select

from ..evidence.models import ScopedEvidence
from ..evidence.repository import EvidenceRepository
from .models import document_versions, evidence_blocks, suppliers


class SqlEvidenceRepository(EvidenceRepository):
    engine: Engine
    validate_access: Callable[[], None]

    def read(self, ids: list[str]) -> list[ScopedEvidence]:
        self.validate_access()
        expected = super().read(ids)
        if not ids:
            return []
        with self.engine.connect() as connection:
            rows = connection.execute(select(evidence_blocks, document_versions.c.role,
                document_versions.c.source_sha256, document_versions.c.coverage_json,
                document_versions.c.model_name, suppliers.c.handle.label("supplier_handle")).join(document_versions,
                    evidence_blocks.c.document_version_id == document_versions.c.id)
                    .outerjoin(suppliers, document_versions.c.supplier_id == suppliers.c.id).where(
                    evidence_blocks.c.id.in_(ids), document_versions.c.case_id == self.case_id,
                    document_versions.c.snapshot_id == self.snapshot_id)).mappings().all()
        actual = {row["id"]: row for row in rows}
        for block in expected:
            row = actual.get(block.evidence_id)
            bbox = (row or {}).get("bbox_json") or {}
            if (row is None or row["document_version_id"] != block.document_id
                    or row["source_text"] != block.text or row["source_sha256"] != block.source_hash
                    or row["role"] != block.role or row["model_name"] != block.model
                    or row["supplier_handle"] != block.supplier
                    or (row["coverage_json"] or {}).get("parser_version_full") != block.parser_version
                    or row["page_idx"] != block.page_idx or row["sheet"] != block.sheet or row["cell"] != block.cell
                    or (tuple(bbox["bbox"]) if bbox.get("bbox") else None) != block.bbox
                    or bbox.get("page_width_pt") != block.page_width_pt
                    or bbox.get("page_height_pt") != block.page_height_pt):
                raise ValueError("source_access_changed")
        self.validate_access()
        return expected


def protect_sources(engine: Engine, repo: EvidenceRepository, validate: Callable[[], None]) -> SqlEvidenceRepository:
    protected = SqlEvidenceRepository(repo.case_id, repo.revision, list(repo.documents),
        snapshot_id=repo.snapshot_id, scoped_blocks=repo.blocks,
        document_names={d.document_id: repo.source_name(d.document_id) for d in repo.documents})
    protected.engine, protected.validate_access = engine, validate
    return protected
