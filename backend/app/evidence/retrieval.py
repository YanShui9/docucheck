"""Snapshot-scoped lexical retrieval; hits never establish complete coverage."""

import re
from collections import defaultdict
from dataclasses import asdict, dataclass

import jieba
from rank_bm25 import BM25Plus

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


class Retriever:
    def __init__(self, repository: EvidenceRepository):
        self.repository = repository

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
            prefix, prefix_refs, context_cut = "", [], False
            for block in context:
                available = MAX_NEIGHBOR_BYTES - len(prefix.encode("utf-8")) - (1 if prefix else 0)
                part, cut = _clip(block.text, max(0, available))
                if part:
                    prefix += ("\n" if prefix else "") + part
                    prefix_refs.append(block.evidence_id)
                context_cut |= cut
            text, refs, partial = prefix, list(prefix_refs), context_cut
            for block in group:
                remaining = block.text
                while remaining:
                    available = MAX_TEXT_BYTES - len(text.encode("utf-8")) - (1 if text else 0)
                    part, cut = _clip(remaining, max(0, available))
                    if not part:
                        if text:
                            chunks.append(RetrievalChunk(text, tuple(dict.fromkeys(refs)), True))
                        text, refs, partial = prefix, list(prefix_refs), context_cut
                        continue
                    text += ("\n" if text else "") + part
                    refs.append(block.evidence_id)
                    remaining = remaining[len(part):]
                    partial |= cut
                    if remaining:
                        chunks.append(RetrievalChunk(text, tuple(dict.fromkeys(refs)), True))
                        text, refs, partial = prefix, list(prefix_refs), True
            if text and any(ref not in prefix_refs for ref in refs):
                chunks.append(RetrievalChunk(text, tuple(dict.fromkeys(refs)), partial))
        return chunks

    def search(self, query: str, supplier: str | None = None, limit: int = 12) -> list[dict]:
        if type(limit) is not int or not 1 <= limit <= 12:
            raise ValueError("retrieval_limit")
        if not isinstance(query, str) or len(query.encode("utf-8")) > 8000:
            raise ValueError("retrieval_query")
        # Filter before chunk construction and BM25 corpus statistics.
        blocks = (self.repository.supplier_blocks(supplier) if supplier is not None
                  else list(self.repository.blocks))
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
                 "score": float(scores[index]), "truncated": chunks[index].truncated}
                for index in matches[:limit]]

    def read_source(
        self, ids: list[str], neighbors: int = 0, start_char: int = 0
    ) -> list[dict]:
        if not isinstance(ids, list) or not 1 <= len(ids) <= 12:
            raise ValueError("evidence_limit")
        if type(neighbors) is not int or neighbors not in (0, 1):
            raise ValueError("neighbor_limit")
        if type(start_char) is not int or start_char < 0:
            raise ValueError("source_offset")
        if start_char and (len(ids) != 1 or neighbors):
            raise ValueError("source_offset")
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
                for adjacent in self._neighbors(block, list(self.repository.blocks)):
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
