"""Read-only snapshot repository; the CLI loader uses hash-bound parser fixtures."""
import json
from dataclasses import asdict
from hashlib import sha256
from pathlib import Path

from pypdf import PdfReader

from ..parsing import (
    MAX_FILE_BYTES,
    ParseResult,
    parse_mineru_middle,
    parse_xlsx,
    verify_pdf_source_coverage,
)
from .models import Document, ScopedEvidence


def digest(value: object) -> str:
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def bind_document(parsed: ParseResult, *, source_path: Path, role: str,
                  supplier: str | None = None, model: str | None = None) -> Document:
    if role not in {"requirement", "quote", "spec"}:
        raise ValueError("invalid_role")
    if (role == "requirement") != (supplier is None):
        raise ValueError("invalid_supplier_binding")
    if source_path.stat().st_size > MAX_FILE_BYTES:
        raise ValueError("file_limit")
    if source_path.suffix.lower() == ".pdf":
        parsed = verify_pdf_source_coverage(parsed, source_path)
    return Document(parsed.document_id, sha256(source_path.read_bytes()).hexdigest(),
                    role, supplier, model, parsed)


class EvidenceRepository:
    def __init__(self, case_id: str, revision: int, documents: list[Document],
                 *, snapshot_id: str | None = None,
                 scoped_blocks: tuple[ScopedEvidence, ...] | None = None):
        if (snapshot_id is None) != (scoped_blocks is None):
            raise ValueError("invalid_snapshot")
        if not documents or len(documents) > 7 or revision < 1:
            raise ValueError("invalid_snapshot")
        if len({d.document_id for d in documents}) != len(documents):
            raise ValueError("duplicate_document")
        if any(
            d.document_id.lower().endswith(".pdf")
            and (type(d.parsed.total_pages) is not int or d.parsed.total_pages < 1)
            for d in documents
        ):
            raise ValueError("missing_pdf_page_count")
        if sum(d.parsed.total_pages or 0 for d in documents) > 30:
            raise ValueError("case_page_limit")
        self.case_id, self.revision = case_id, revision
        self.documents = tuple(documents)
        self.snapshot_id = snapshot_id or digest([case_id, revision, [asdict(d) for d in documents]])
        blocks = []
        if scoped_blocks is None:
            for document in documents:
                for ordinal, block in enumerate(document.parsed.blocks):
                    values = asdict(block)
                    values["evidence_id"] = digest([self.snapshot_id, document.document_id,
                                                     document.source_hash, document.parsed.parser_version,
                                                     ordinal, values])
                    blocks.append(ScopedEvidence(**values, role=document.role, supplier=document.supplier,
                                                 model=document.model, source_hash=document.source_hash,
                                                 parser_version=document.parsed.parser_version, ordinal=ordinal))
        else:
            by_document = {document.document_id: document for document in documents}
            for block in scoped_blocks:
                document = by_document.get(block.document_id)
                if (document is None or block.role != document.role
                        or block.supplier != document.supplier
                        or block.source_hash != document.source_hash
                        or block.parser_version != document.parsed.parser_version):
                    raise ValueError("evidence_scope")
            blocks.extend(scoped_blocks)
        if len({block.evidence_id for block in blocks}) != len(blocks):
            raise ValueError("duplicate_evidence")
        self.blocks = tuple(blocks)
        self._by_id = {b.evidence_id: b for b in blocks}

    def read(self, ids: list[str]) -> list[ScopedEvidence]:
        if any(key not in self._by_id for key in ids):
            raise ValueError("evidence_scope")
        return [self._by_id[key] for key in ids]

    def supplier_blocks(self, supplier: str) -> list[ScopedEvidence]:
        self.assert_supplier(supplier)
        return [b for b in self.blocks if b.role == "requirement" or b.supplier == supplier]

    def assert_supplier(self, supplier: str) -> None:
        if supplier not in {d.supplier for d in self.documents if d.supplier}:
            raise ValueError("supplier_scope")

    def coverage(self, supplier: str) -> dict:
        self.assert_supplier(supplier)
        documents = [d for d in self.documents if d.role == "requirement" or d.supplier == supplier]
        roles = [d.role for d in documents]
        return {"complete": sorted(roles) == ["quote", "requirement", "spec"]
                and all(d.parsed.status == "complete" and not d.parsed.coverage_issues for d in documents),
                "documents": [{"document_id": d.document_id, "status": d.parsed.status,
                               "issues": d.parsed.coverage_issues} for d in documents]}

    def sources(self) -> list[dict]:
        return [{"document_id": d.document_id, "role": d.role, "supplier": d.supplier,
                 "model": d.model, "status": d.parsed.status, "coverage_issues": d.parsed.coverage_issues}
                for d in self.documents]


def _within(root: Path, relative: str) -> Path:
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()) or not path.is_file():
        raise ValueError("source_path")
    return path


def load_case(case_dir: Path) -> EvidenceRepository:
    """Load an explicitly mapped development case, never labels or guessed roles.

    Cached MinerU output is only accepted with both source and cache SHA-256 in
    the trusted local manifest. This is not the production upload API.
    """
    manifest = json.loads((case_dir / "manifest.json").read_text(encoding="utf-8"))
    items = manifest["documents"]
    if not isinstance(items, list) or not 1 <= len(items) <= 7:
        raise ValueError("case_file_limit")
    source_items = []
    total_pdf_pages = 0
    for item in items:
        source = _within(case_dir, item["file"])
        if source.stat().st_size > MAX_FILE_BYTES:
            raise ValueError("file_limit")
        if sha256(source.read_bytes()).hexdigest() != item["sha256"]:
            raise ValueError("source_hash_mismatch")
        if source.suffix.lower() == ".pdf":
            try:
                page_count = len(PdfReader(source).pages)
            except Exception as exc:
                raise ValueError("source_pdf_invalid") from exc
            if page_count < 1:
                raise ValueError("source_pdf_invalid")
            total_pdf_pages += page_count
            if total_pdf_pages > 30:
                raise ValueError("case_page_limit")
        elif source.suffix.lower() != ".xlsx":
            raise ValueError("unsupported_file")
        source_items.append((item, source))
    documents = []
    for item, source in source_items:
        if source.suffix.lower() == ".pdf":
            cached = _within(case_dir, item["parsed_file"])
            if sha256(cached.read_bytes()).hexdigest() != item["parsed_sha256"]:
                raise ValueError("parser_cache_hash_mismatch")
            parsed = parse_mineru_middle(cached, document_id=item["file"])
        elif source.suffix.lower() == ".xlsx":
            parsed = parse_xlsx(source, document_id=item["file"])
        else:
            raise ValueError("unsupported_file")
        documents.append(bind_document(parsed, source_path=source, role=item["role"],
                                       supplier=item.get("supplier"), model=item.get("model")))
    return EvidenceRepository(manifest["case_id"], manifest["revision"], documents)
