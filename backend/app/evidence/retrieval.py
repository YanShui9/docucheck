"""Snapshot-scoped lexical retrieval; hits never establish complete coverage."""

import re
from collections import defaultdict
from dataclasses import asdict, dataclass

import jieba
from rank_bm25 import BM25Plus

from .chunks import SourceSpan
from .models import ScopedEvidence
from .repository import EvidenceRepository

# A UTF-8 byte is a conservative token allowance for byte-based model tokenizers.
# This bounds source text only; the caller must budget the complete tool envelope.
MAX_TEXT_BYTES = 800
MAX_NEIGHBOR_BYTES = 100
MAX_SOURCE_BLOCKS = 36
ALIASES = {
    "ram": "内存",
    "memory": "内存",
    "ssd": "存储",
    "硬盘": "存储",
    "显示屏": "屏幕",
    "保修": "质保",
    "保修期": "质保",
    "交货": "交付",
}


def _tokens(text: str) -> list[str]:
    result = []
    for word in jieba.cut_for_search(text.casefold()):
        if re.search(r"[\w]", word, re.UNICODE):
            result.append(ALIASES.get(word, word))
    # Keep numbers with units and complete model identifiers as exact terms too.
    result.extend(re.findall(r"[a-z0-9]+(?:[.-][a-z0-9]+)+|\d+(?:\.\d+)?\s*(?:gb|tb|gib)",
                             text.casefold()))
    return result


def _clip(text: str, byte_limit: int) -> tuple[str, bool]:
    raw = text.encode("utf-8")
    return raw[:byte_limit].decode("utf-8", errors="ignore"), len(raw) > byte_limit


@dataclass(frozen=True)
class RetrievalChunk:
    text: str
    evidence_refs: tuple[str, ...]
    truncated: bool
    spans: tuple[SourceSpan, ...] = ()


class Retriever:
    def __init__(self, repository: EvidenceRepository):
        self.repository = repository
        self._item_scope = None
        self._item_ids = frozenset()
        self.last_diagnostics = {}

    def set_item_scope(self, frozen, definition):
        """Server-only configuration from a Run's frozen SQL authority."""
        from ..criteria.item_bindings import review_bindings
        from ..criteria.models import ChecklistDefinition

        if self._item_scope is not None:
            raise ValueError("item_binding_scope_frozen")
        self._item_scope = review_bindings(frozen, definition, self.repository)
        parsed = ChecklistDefinition.model_validate(definition) if isinstance(definition, dict) else definition
        self._item_ids = frozenset(i.item_id for i in parsed.items)

    def _item_refs(self, item_id):
        if self._item_scope is None:
            raise ValueError("item_binding_not_confirmed")
        if not isinstance(item_id, str) or item_id not in self._item_ids:
            raise ValueError("item_binding_item")
        return self._item_scope.bindings.allowed_refs(item_id)

    def _item_diagnostics(self, item_id, reason, mode="bm25"):
        return {"retrieval_mode": mode, "degraded_reason": reason, "complete_coverage": False,
                "item_id": item_id, **self._item_scope.identity()}

    @staticmethod
    def _neighbors(block: ScopedEvidence, blocks: list[ScopedEvidence]) -> list[ScopedEvidence]:
        document = [b for b in blocks if b.document_id == block.document_id]
        if block.sheet is not None and block.cell is not None:
            sheet = [b for b in document if b.sheet == block.sheet and b.cell]
            row = lambda b: int(re.search(r"\d+$", b.cell).group())
            first_row = min(map(row, sheet))
            same_row = [b for b in sheet if row(b) == row(block) and b != block]
            header = [b for b in sheet if row(b) == first_row and b != block and b not in same_row]
            return same_row + header
        return [b for b in document if b.sheet is None and abs(b.ordinal - block.ordinal) == 1]

    def _chunks(self, blocks: list[ScopedEvidence]) -> list[RetrievalChunk]:
        groups: dict[tuple, list[ScopedEvidence]] = defaultdict(list)
        for block in blocks:
            key = ((block.document_id, block.sheet, re.search(r"\d+$", block.cell).group())
                   if block.sheet is not None and block.cell else (block.evidence_id,))
            groups[key].append(block)
        chunks = []
        for group in groups.values():
            # XLSX first-row context is copied verbatim with original cell refs.
            context = self._neighbors(group[0], blocks) if group[0].sheet is not None else []
            context = [b for b in context if b not in group]
            prefix, prefix_refs, prefix_spans, context_cut = "", [], [], False
            for block in context:
                available = MAX_NEIGHBOR_BYTES - len(prefix.encode("utf-8")) - (1 if prefix else 0)
                part, cut = _clip(block.text, max(0, available))
                if part:
                    prefix += ("\n" if prefix else "") + part
                    prefix_refs.append(block.evidence_id)
                    prefix_spans.append(SourceSpan(block.evidence_id, 0, len(part)))
                context_cut |= cut
            text, refs, partial = prefix, list(prefix_refs), context_cut
            spans = list(prefix_spans)
            for block in group:
                remaining = block.text
                while remaining:
                    available = MAX_TEXT_BYTES - len(text.encode("utf-8")) - (1 if text else 0)
                    part, cut = _clip(remaining, max(0, available))
                    if not part:
                        if text:
                            chunks.append(RetrievalChunk(text, tuple(dict.fromkeys(refs)), True, tuple(spans)))
                        text, refs, partial = prefix, list(prefix_refs), context_cut
                        spans = list(prefix_spans)
                        continue
                    text += ("\n" if text else "") + part
                    refs.append(block.evidence_id)
                    start = len(block.text) - len(remaining)
                    spans.append(SourceSpan(block.evidence_id, start, start + len(part)))
                    remaining = remaining[len(part):]
                    partial |= cut
                    if remaining:
                        chunks.append(RetrievalChunk(text, tuple(dict.fromkeys(refs)), True, tuple(spans)))
                        text, refs, partial = prefix, list(prefix_refs), True
                        spans = list(prefix_spans)
            if text and any(ref not in prefix_refs for ref in refs):
                chunks.append(RetrievalChunk(text, tuple(dict.fromkeys(refs)), partial, tuple(spans)))
        return chunks

    def search(self, query: str, supplier: str | None = None, limit: int = 12,
               *, item_id: str | None = None) -> list[dict]:
        if type(limit) is not int or not 1 <= limit <= 12:
            raise ValueError("retrieval_limit")
        if not isinstance(query, str) or len(query.encode("utf-8")) > 8000:
            raise ValueError("retrieval_query")
        # Filter before chunk construction and BM25 corpus statistics.
        blocks = (self.repository.supplier_blocks(supplier) if supplier is not None
                  else list(self.repository.blocks))
        self.last_diagnostics = {"retrieval_mode": "bm25", "complete_coverage": False}
        if item_id is not None:
            refs = self._item_refs(item_id)
            blocks = [b for b in blocks if b.evidence_id in refs]
            self.last_diagnostics = self._item_diagnostics(item_id, None if blocks else "item_binding_not_confirmed")
        terms = _tokens(query)
        chunks = self._chunks(blocks)
        if not terms or not chunks:
            return []
        corpus = [_tokens(chunk.text) for chunk in chunks]
        if not any(corpus):
            return []
        scores = BM25Plus(corpus).get_scores(terms)
        query_terms = set(terms)
        matches = [index for index, tokens in enumerate(corpus) if query_terms.intersection(tokens)]
        matches.sort(key=lambda index: (-float(scores[index]), index))
        return [{"text": chunks[index].text, "evidence_refs": list(chunks[index].evidence_refs),
                 "score": float(scores[index]), "truncated": chunks[index].truncated,
                 "source_spans": [asdict(span) for span in chunks[index].spans]}
                for index in matches[:limit]]

    def read_source(
        self, ids: list[str], neighbors: int = 0, start_char: int = 0, *, item_id: str | None = None
    ) -> list[dict]:
        if not isinstance(ids, list) or not 1 <= len(ids) <= 12:
            raise ValueError("evidence_limit")
        if type(neighbors) is not int or neighbors not in (0, 1):
            raise ValueError("neighbor_limit")
        if type(start_char) is not int or start_char < 0:
            raise ValueError("source_offset")
        if start_char and (len(ids) != 1 or neighbors):
            raise ValueError("source_offset")
        allowed = self._item_refs(item_id) if item_id is not None else None
        if allowed is not None and not set(ids) <= allowed:
            raise ValueError("item_binding_scope")
        selected = self.repository.read(ids)  # Validate every ID before emitting anything.
        results: dict[str, dict] = {}
        for block in selected:
            if start_char and start_char >= len(block.text):
                raise ValueError("source_offset")
            text, has_more = _clip(block.text[start_char:], MAX_TEXT_BYTES)
            results[block.evidence_id] = {
                **asdict(block),
                "text": text,
                "truncated": bool(start_char) or has_more,
                "start_char": start_char,
                "next_char": start_char + len(text) if has_more else None,
            }
        if neighbors:
            for block in selected:
                allowance = MAX_NEIGHBOR_BYTES
                blocks = [b for b in self.repository.blocks if allowed is None or b.evidence_id in allowed]
                for adjacent in self._neighbors(block, blocks):
                    if adjacent.evidence_id in results:
                        continue
                    text, truncated = _clip(adjacent.text, allowance)
                    if not text or len(results) >= MAX_SOURCE_BLOCKS:
                        results[block.evidence_id]["truncated"] = True
                        continue
                    allowance -= len(text.encode("utf-8"))
                    results[adjacent.evidence_id] = {
                        **asdict(adjacent),
                        "text": text,
                        "truncated": truncated,
                        "start_char": 0,
                        "next_char": len(text) if truncated else None,
                    }
        return list(results.values())

    def read_item_binding(self, item_id: str, *, evidence_ids: list[str], start_char: int = 0):
        """Explicit attribution detail; anchors never widen numeric source reads."""
        self._item_refs(item_id)
        if not isinstance(evidence_ids, list) or not 1 <= len(evidence_ids) <= 4:
            raise ValueError("evidence_limit")
        anchors = set()
        for entry in self._item_scope.bindings.entries:
            if entry.item_id == item_id:
                anchors.update(entry.anchor_refs)
                sku = entry.payload().get("sku_anchor")
                if sku:
                    anchors.update(sku["evidence_refs"])
        if not set(evidence_ids) <= anchors:
            raise ValueError("item_binding_scope")
        # Deliberate separate browsing path; no neighbors and every ref is an anchor.
        rows = self.read_source(evidence_ids, start_char=start_char)
        return [row | {"purpose": "item_attribution_anchor", "item_id": item_id,
                       **self._item_scope.identity()} for row in rows]
