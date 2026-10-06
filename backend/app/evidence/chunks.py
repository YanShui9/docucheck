"""Versioned original-text chunks with reversible character provenance."""
import re
from collections import defaultdict
from dataclasses import asdict, dataclass

from .repository import EvidenceRepository, digest


@dataclass(frozen=True)
class ChunkPolicy:
    max_bytes: int = 1600
    overlap_bytes: int = 180

    def __post_init__(self):
        if (type(self.max_bytes) is not int or not 256 <= self.max_bytes <= 4096
                or type(self.overlap_bytes) is not int
                or not 0 <= self.overlap_bytes < self.max_bytes // 2):
            raise ValueError("chunk_policy")

    @property
    def revision(self):
        return f"source-offsets-v1-{self.max_bytes}-{self.overlap_bytes}"


@dataclass(frozen=True)
class SourceSpan:
    evidence_id: str
    start: int
    end: int


@dataclass(frozen=True)
class SourceChunk:
    chunk_id: str
    project_id: str
    case_id: str
    snapshot_id: str
    document_id: str
    document_hash: str
    parse_version: str
    binding_version: str
    supplier_id: str
    role: str
    chunk_schema: str
    embedding_revision: str
    dimension: int
    item_ids: tuple[str, ...]
    spans: tuple[SourceSpan, ...]
    text: str
    index_generation: str = ""

    @property
    def evidence_refs(self):
        return tuple(dict.fromkeys(s.evidence_id for s in self.spans))

    @property
    def fingerprint(self):
        return digest(asdict(self))


def _head(text, budget):
    return text.encode("utf-8")[:budget].decode("utf-8", errors="ignore")


def chunk_snapshot(repository: EvidenceRepository, project_id: str, embedding_revision: str,
                   *, dimension: int = 1024, policy: ChunkPolicy | None = None,
                   generations: dict[str, str] | None = None) -> tuple[SourceChunk, ...]:
    for value in (project_id, repository.case_id, repository.snapshot_id, embedding_revision):
        if not isinstance(value, str) or not value or len(value.encode()) > 256:
            raise ValueError("chunk_scope")
    if type(dimension) is not int or not 2 <= dimension <= 32768:
        raise ValueError("chunk_dimension")
    policy = policy or ChunkPolicy()
    generations = generations or {}
    if (not set(generations) <= {d.document_id for d in repository.documents}
            or any(not re.fullmatch(r"[a-f0-9]{32}", value) for value in generations.values())):
        raise ValueError("chunk_generation")
    groups = defaultdict(list)
    first_rows = {}
    for block in repository.blocks:
        if not block.text:
            continue
        key = ((block.document_id, block.sheet, re.search(r"\d+$", block.cell).group())
               if block.sheet is not None and block.cell else (block.evidence_id,))
        groups[key].append(block)
        if block.sheet is not None and block.cell:
            sheet_key = (block.document_id, block.sheet)
            row = int(key[-1])
            current_row, cells = first_rows.get(sheet_key, (row, []))
            if row < current_row:
                current_row, cells = row, []
            if row == current_row:
                cells.append(block)
            first_rows[sheet_key] = current_row, cells
    contexts = {}
    for sheet_key, (row, cells) in first_rows.items():
        budget, pieces = 200, []
        for block in cells:
            part = _head(block.text, max(0, budget - 1))
            if part:
                pieces.append((block, 0, len(part)))
                budget -= len(part.encode()) + 1
        contexts[sheet_key] = row, pieces
    chunks = []
    for group in groups.values():
        first = group[0]
        # First-row context is verbatim; it is context, not an inferred schema/header.
        context = []
        if first.sheet is not None and first.cell:
            first_row, pieces = contexts[first.document_id, first.sheet]
            if int(re.search(r"\d+$", first.cell).group()) != first_row:
                context = pieces
        prefix_bytes = sum(len(b.text[s:e].encode()) + 1 for b, s, e in context)
        row_text = "\n".join(b.text for b in group)
        segments, offset = [], 0
        for block in group:
            segments.append((block, offset, offset + len(block.text)))
            offset += len(block.text) + 1
        start = 0
        while start < len(row_text):
            part = _head(row_text[start:], policy.max_bytes - prefix_bytes)
            end = start + len(part)
            pieces = context + [(b, max(start, left) - left, min(end, right) - left)
                                for b, left, right in segments
                                if max(start, left) < min(end, right)]
            if pieces:
                spans = tuple(SourceSpan(b.evidence_id, s, e) for b, s, e in pieces)
                text = "\n".join(b.text[s:e] for b, s, e in pieces)
                binding = digest({"role": first.role, "supplier": first.supplier, "model": first.model})
                identity = {"project_id": project_id, "case_id": repository.case_id,
                            "snapshot_id": repository.snapshot_id, "document_id": first.document_id,
                            "document_hash": first.source_hash, "parse_version": first.parser_version,
                            "binding_version": binding, "supplier_id": first.supplier or "",
                            "role": first.role, "chunk_schema": policy.revision,
                            "embedding_revision": embedding_revision, "dimension": dimension,
                            "item_ids": (), "spans": spans, "text": text,
                            "index_generation": generations.get(first.document_id, "")}
                chunk_id = digest(identity | {"spans": [asdict(s) for s in spans]})
                chunks.append(SourceChunk(chunk_id=chunk_id, **identity))
            if end >= len(row_text):
                break
            overlap = row_text[start:end].encode()[-policy.overlap_bytes:] if policy.overlap_bytes else b""
            overlap_chars = len(overlap.decode("utf-8", errors="ignore"))
            start = max(start + 1, end - overlap_chars)
    return tuple(chunks)


def restore_text(chunk: SourceChunk, repository: EvidenceRepository) -> str:
    if chunk.case_id != repository.case_id or chunk.snapshot_id != repository.snapshot_id:
        raise ValueError("chunk_source_mismatch")
    blocks = {b.evidence_id: b for b in repository.read(list(chunk.evidence_refs))}
    parts = []
    for span in chunk.spans:
        block = blocks[span.evidence_id]
        binding = digest({"role": block.role, "supplier": block.supplier, "model": block.model})
        if (not 0 <= span.start < span.end <= len(block.text)
                or block.document_id != chunk.document_id or block.source_hash != chunk.document_hash
                or block.parser_version != chunk.parse_version or binding != chunk.binding_version):
            raise ValueError("chunk_source_mismatch")
        parts.append(block.text[span.start:span.end])
    text = "\n".join(parts)
    if text != chunk.text:
        raise ValueError("chunk_source_mismatch")
    return text
