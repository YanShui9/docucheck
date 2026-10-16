"""Source-grounded hybrid retrieval. A top-k hit is never a coverage certificate."""
from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
from math import isfinite

from rank_bm25 import BM25Plus

from ..vector_store.embedding import EmbeddingError, validate_vectors
from ..vector_store.store import MAX_CHUNK_FILTER_IDS, SearchScope, VectorUnavailable
from .chunks import ChunkPolicy, chunk_snapshot, restore_text
from .repository import EvidenceRepository, digest
from .retrieval import MAX_TEXT_BYTES, Retriever, _clip, _tokens

MAX_ITEM_SCOPE_BATCHES = 4
MAX_ITEM_FILTER_BYTES = 16_384


@dataclass(frozen=True)
class IndexReceipt:
    """Process-local verified readback, NOT a durable SQL manifest or authorization."""
    fingerprint: str
    chunk_ids: tuple[str, ...]


def _receipt(chunks):
    return IndexReceipt(digest([c.fingerprint for c in chunks]), tuple(c.chunk_id for c in chunks))


def _validate_model(chunks, embedding):
    if chunks and (chunks[0].embedding_revision != embedding.revision
                   or chunks[0].dimension != embedding.dimension):
        raise ValueError("index_model_mismatch")


def build_index(repo, project_id, embedding, store, *, policy: ChunkPolicy | None = None,
                revalidate: Callable[[], None] | None = None, generations=None):
    revalidate = revalidate or (lambda: None)
    revalidate()
    chunks = chunk_snapshot(repo, project_id, embedding.revision,
                            dimension=embedding.dimension, policy=policy, generations=generations)
    if not chunks:
        raise ValueError("vector_empty_scope")
    store.ensure_collection()
    for offset in range(0, len(chunks), 8):
        revalidate()
        _validate_model(chunks, embedding)
        batch = chunks[offset:offset + 8]
        texts = [restore_text(c, repo) for c in batch]
        revalidate()
        vectors = validate_vectors(embedding.embed(texts), count=len(texts), dimension=embedding.dimension)
        revalidate()
        _validate_model(chunks, embedding)
        store.upsert(batch, vectors)
    if not store.verify(chunks):
        raise VectorUnavailable("vector_index_not_visible")
    revalidate()
    return _receipt(chunks)


class HybridRetriever(Retriever):
    def __init__(self, repository: EvidenceRepository, project_id: str, embedding, store,
                 *, receipt: IndexReceipt | None = None, policy: ChunkPolicy | None = None,
                 revalidate: Callable[[], None] | None = None, generations=None):
        super().__init__(repository)
        self.embedding, self.store, self.receipt = embedding, store, receipt
        self.chunks = chunk_snapshot(repository, project_id, embedding.revision,
                                     dimension=embedding.dimension, policy=policy, generations=generations)
        if receipt is not None and receipt != _receipt(self.chunks):
            raise ValueError("index_receipt_mismatch")
        self.revalidate = revalidate or (lambda: None)
        self.last_diagnostics = {}

    def read_source(self, *args, **kwargs):
        self.revalidate()
        result = super().read_source(*args, **kwargs)
        self.revalidate()
        return result

    def _scope_fallback(self, query, supplier, limit, item_id, reason):
        self.revalidate()
        hits = Retriever.search(self, query, supplier, limit, item_id=item_id)
        self.repository.read(list(dict.fromkeys(ref for h in hits for ref in h["evidence_refs"])))
        self.revalidate()
        self.last_diagnostics["degraded_reason"] = reason
        return [h | {"retrieval_mode": "bm25", "degraded_reason": reason, "channels": ["bm25"]} for h in hits]

    def search(self, query: str, supplier: str | None = None, limit: int = 12,
               *, item_id: str | None = None) -> list[dict]:
        if type(limit) is not int or not 1 <= limit <= 12:
            raise ValueError("retrieval_limit")
        if not isinstance(query, str) or not query.strip() or len(query.encode()) > 8000:
            raise ValueError("retrieval_query")
        self.revalidate()  # Caller binds this to SQL permissions/deletion/version checks.
        _validate_model(self.chunks, self.embedding)
        if supplier is not None:
            self.repository.assert_supplier(supplier)
        chunks = [c for c in self.chunks if supplier is None or c.role == "requirement"
                  or c.supplier_id == supplier]
        if item_id is not None:
            refs = self._item_refs(item_id)
            blocks = (self.repository.supplier_blocks(supplier) if supplier else self.repository.blocks)
            refs = refs.intersection(b.evidence_id for b in blocks if b.text)
            safe = [c for c in chunks if set(c.evidence_refs) <= refs]
            represented = {ref for c in safe for ref in c.evidence_refs}
            reason = ("item_binding_not_confirmed" if not refs else
                      "vector_item_scope_unrepresentable" if represented != refs else
                      "vector_item_scope_budget" if len(safe) > MAX_ITEM_SCOPE_BATCHES * MAX_CHUNK_FILTER_IDS else
                      "vector_index_not_ready" if self.receipt is None else None)
            if reason:
                return self._scope_fallback(query, supplier, limit, item_id, reason)
            chunks = safe
        if not chunks:
            self.last_diagnostics = {"retrieval_mode": "bm25", "degraded_reason": "vector_index_not_ready",
                                     "complete_coverage": False}
            return []
        terms = _tokens(query)
        corpus = [_tokens(c.text) for c in chunks]
        lexical = []
        if terms and any(corpus):
            scores = BM25Plus(corpus).get_scores(terms)
            selected = [i for i, tokens in enumerate(corpus) if set(terms).intersection(tokens)]
            selected.sort(key=lambda i: (-float(scores[i]), chunks[i].chunk_id))
            lexical = [chunks[i].chunk_id for i in selected[:20]]
        dense, reason = [], None
        allowed = {c.chunk_id: c for c in chunks}
        if self.receipt is None:
            reason = "vector_index_not_ready"
        else:
            if self.receipt != _receipt(self.chunks):
                raise ValueError("index_receipt_mismatch")
            try:
                vector = validate_vectors(self.embedding.embed([query]), count=1,
                                          dimension=self.embedding.dimension)[0]
                _validate_model(self.chunks, self.embedding)
                self.revalidate()
                batches = [chunks] if item_id is None else [chunks[i:i + MAX_CHUNK_FILTER_IDS]
                    for i in range(0, len(chunks), MAX_CHUNK_FILTER_IDS)]
                scored = {}
                for batch in batches:
                    self.revalidate()
                    scope = SearchScope.from_chunks(tuple(batch), supplier=supplier)
                    if item_id is not None:
                        scope = replace(scope, chunk_ids=tuple(c.chunk_id for c in batch))
                        if len(scope.expression().encode()) > MAX_ITEM_FILTER_BYTES:
                            return self._scope_fallback(query, supplier, limit, item_id, "vector_item_scope_budget")
                    hits = self.store.search(vector, scope, limit=20,
                                             **({"include_scores": True} if len(batches) > 1 else {}))
                    if not isinstance(hits, list) or len(hits) > 20:
                        raise ValueError("vector_hit_scope")
                    for hit in hits:
                        if (not isinstance(hit, dict) or hit.get("chunk_id") not in allowed
                                or not scope.allows(allowed[hit["chunk_id"]])
                                or hit.get("fingerprint") != allowed[hit["chunk_id"]].fingerprint):
                            raise ValueError("vector_hit_scope")
                        if len(batches) > 1:
                            score = hit.get("distance")
                            if type(score) not in (float, int) or not isfinite(score) or not -1.001 <= score <= 1.001:
                                raise ValueError("vector_hit_scope")
                            scored[hit["chunk_id"]] = score
                        elif hit["chunk_id"] not in dense:
                            dense.append(hit["chunk_id"])
                if len(batches) > 1:
                    dense = sorted(scored, key=lambda cid: (-scored[cid], cid))[:20]
            except VectorUnavailable:
                reason = "vector_service_unavailable"
            except EmbeddingError as exc:
                if str(exc) not in {"embedding_service_unavailable", "embedding_model_not_installed"}:
                    raise
                reason = str(exc)
        if reason:
            return self._scope_fallback(query, supplier, limit, item_id, reason)
        self.revalidate()  # Recheck after network waits, before any source disclosure.
        ranks = {}
        for channel, ids in (("bm25", lexical), ("dense", dense)):
            for rank, chunk_id in enumerate(ids, 1):
                ranks.setdefault(chunk_id, {})[channel] = rank
        ordered = sorted(ranks, key=lambda cid: (-sum(1 / (60 + r) for r in ranks[cid].values()), cid))
        mode = "bm25" if reason else "hybrid"
        self.last_diagnostics = {"retrieval_mode": mode, "degraded_reason": reason,
                                 "complete_coverage": False, "rrf_k": 60,
                                 "bm25_candidates": len(lexical), "dense_candidates": len(dense)}
        if item_id is not None:
            self.last_diagnostics.update(self._item_diagnostics(item_id, reason, mode))
        results = []
        for chunk_id in ordered[:limit]:
            chunk = allowed[chunk_id]
            text, truncated = _clip(restore_text(chunk, self.repository), MAX_TEXT_BYTES)
            results.append({"chunk_id": chunk_id, "text": text,
                            "source_spans": [asdict(span) for span in chunk.spans],
                            "evidence_refs": list(chunk.evidence_refs), "truncated": truncated,
                            "score": sum(1 / (60 + r) for r in ranks[chunk_id].values()),
                            "channels": list(ranks[chunk_id]), "ranks": ranks[chunk_id],
                            "retrieval_mode": mode, "degraded_reason": reason})
        self.revalidate()
        return results
