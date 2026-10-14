"""Frozen, source-scoped BM25/BGE-M3/Milvus comparison; offline by default.

The PDF reader is a separate readback adapter, not MinerU ingestion acceptance.
All three methods rank the same representable PDF chunks. XLSX scope exclusions
are recorded rather than stripping provenance to make a vector hit look valid.
"""

from __future__ import annotations

import argparse
import json
import re
import time
import unicodedata
from dataclasses import dataclass, replace
from hashlib import sha256
from pathlib import Path
from statistics import median
from uuid import uuid4

from rank_bm25 import BM25Plus

from backend.app.evidence.chunks import ChunkPolicy, chunk_snapshot, restore_text
from backend.app.evidence.hybrid import build_index
from backend.app.evidence.models import Document
from backend.app.evidence.repository import EvidenceRepository, digest
from backend.app.evidence.retrieval import _tokens
from backend.app.parsing import EvidenceBlock, ParseResult
from backend.app.vector_store.embedding import EmbeddingError, validate_vectors
from backend.app.vector_store.store import (
    MAX_CHUNK_FILTER_IDS,
    SearchScope,
    VectorUnavailable,
)

from .contracts import Anchor, contains
from .metrics import read_originals
from .run import AttemptJournal, _write_new

PROTOCOL = "dynamic-retrieval-dev-v2"
METHODS = ("bm25", "dense", "hybrid")
TOP_K = 5
CANDIDATE_K = 20
RRF_K = 60
# Literal questions are fixed before the first send; no question generation from
# ranked hits, and no business gold label is sent to the embedding service.
QUERY_DEFINITIONS = (
    ("phone-capacity", "dyn-d01", "vendor-a", "phone", "capacity", "供应商A这款手机能存多少资料？"),
    ("phone-delivery", "dyn-d01", "vendor-a", "phone", "delivery", "供应商A的手机下单确认后多久能收到？"),
    ("fruit-variety", "dyn-d04", "vendor-b", "fruit", "variety", "供应商B送的苹果是什么品种？"),
    ("fruit-price", "dyn-d04", "vendor-a", "fruit", "price", "供应商A一箱苹果含税要多少钱？"),
    ("mixed-fruit-price", "dyn-d10", "vendor-b", "fruit", "fruit_price", "供应商B的苹果每箱含税多少钱？"),
    ("mixed-shirt-delivery", "dyn-d10", "vendor-a", "clothing", "clothing_delivery", "供应商A的T恤订单确认后多久能送到？"),
    ("phone-condition", "dyn-d01", "vendor-a", "phone", "condition", "供应商A的手机是未激活的新机吗？"),
    ("phone-warranty-material", "dyn-d01", "vendor-a", "phone", "warranty_doc", "供应商A的手机质保承诺书声明和材料正文在哪里？"),
    ("phone-spec-capacity", "dyn-d02", "vendor-a", "phone", "capacity", "仅看供应商A的手机规格文档，存储容量写的是多少？"),
    ("phone-selected-sku", "dyn-d03", "vendor-a", "phone", "capacity", "供应商A采购范围内的DEMO-P512手机存储有多大？不要借用其他型号。"),
    ("fruit-net-weight", "dyn-d05", "vendor-a", "fruit", "net_weight", "供应商A每箱去掉包装实际装了多重的水果？"),
    ("fruit-bag-quantity", "dyn-d05", "vendor-b", "fruit", "quantity", "供应商B承诺提供多少，原文用的是箱还是袋？"),
    ("fruit-discount", "dyn-d05", "vendor-a", "fruit", "discount", "供应商A的水果综合单价折扣比例是多少？"),
    ("fruit-test-material", "dyn-d06", "vendor-a", "fruit", "test", "供应商A这个批次的检测报告声明和材料正文在哪里？"),
    ("clothing-material", "dyn-d07", "vendor-a", "clothing", "material", "供应商A的夹克用什么面料做的？"),
    ("clothing-spec-cotton", "dyn-d08", "vendor-a", "clothing", "cotton", "供应商A的T恤规格文档写了多少棉含量？只找规格里的声明。"),
    ("clothing-size-options", "dyn-d08", "vendor-b", "clothing", "size", "供应商B的T恤尺码有哪些选择？"),
    ("clothing-sample", "dyn-d09", "vendor-a", "clothing", "sample", "供应商A对夹克样品提供和现场验收怎么承诺的？"),
    ("clothing-quantity-basis", "dyn-d09", "vendor-a", "clothing", "quantity_basis", "供应商A写的110件是确定订单数量还是预计结算数量？"),
    ("accessory-tax-context", "dyn-d11", "vendor-b", "equipment", "price", "供应商B的配件单价有没有包含税费？请找到完整限定。"),
    ("missing-phone-authorization", "dyn-d03", "vendor-b", "phone", "authorization", "供应商B采购范围内手机的供货授权材料在哪里？"),
    ("missing-fruit-delivery", "dyn-d04", "vendor-b", "fruit", "delivery", "供应商B承诺苹果订单确认后多久交付？"),
    ("missing-fruit-origin-material", "dyn-d06", "vendor-b", "fruit", "trace", "供应商B这个批次的产地证明材料原文在哪里？"),
    ("missing-mixed-fruit-delivery", "dyn-d10", "vendor-b", "fruit", "fruit_delivery", "在混合采购中，供应商B苹果品目的交期承诺是什么？"),
)
NO_ANSWER_IDS = frozenset({"missing-phone-authorization", "missing-fruit-delivery",
                          "missing-fruit-origin-material", "missing-mixed-fruit-delivery"})
# Questions explicitly restricted to a specification document measure that
# claim only, not a complete cross-file conflict decision.
SELECTED_GROUP_IDS = {"phone-spec-capacity": {"claim-2"}, "clothing-spec-cotton": {"claim-2"}}


class CheckpointError(RuntimeError):
    """Stop sending if a local progress record cannot be persisted."""


@dataclass(frozen=True)
class QuerySpec:
    query_id: str
    pack_id: str
    supplier: str
    item_id: str
    criterion_id: str
    question: str
    groups: tuple[tuple[Anchor, ...], ...]
    allowed_evidence_refs: tuple[str, ...] | None = None
    answerable: bool = True

    def payload(self):
        return {"query_id": self.query_id, "pack_id": self.pack_id,
                "supplier": self.supplier, "item_id": self.item_id,
                "criterion_id": self.criterion_id, "question": self.question,
                "groups": [[anchor.model_dump(mode="json") for anchor in group] for group in self.groups],
                "allowed_evidence_refs": list(self.allowed_evidence_refs or ()), "answerable": self.answerable}


def _text(value):
    return "".join(unicodedata.normalize("NFKC", value).split())


def repository_from_originals(manifest, originals):
    """Hash-verified line/cell readback; deliberately not a production parser cache."""
    texts, errors = read_originals(manifest, originals)
    if errors:
        raise ValueError("retrieval_original_integrity")
    documents = []
    for source in manifest.documents:
        blocks = []
        for (filename, page, sheet, cell), text in sorted(
            texts.items(), key=lambda row: (row[0][0], row[0][1] or 0, row[0][2] or "", row[0][3] or "")
        ):
            if filename != source.filename:
                continue
            lines = text.splitlines() if page is not None else [text]
            for ordinal, line in enumerate(lines):
                if not line.strip():
                    continue
                locator = [filename, page, sheet, cell, ordinal, line]
                blocks.append(EvidenceBlock(digest(locator), filename, line,
                                            page_idx=page - 1 if page is not None else None,
                                            sheet=sheet, cell=cell))
        parsed = ParseResult(source.filename, "evaluation-original-readback-v1", "partial",
                             tuple(blocks), total_pages=source.pages or None,
                             processed_pages=tuple(range(source.pages)),
                             coverage_issues=("readback_is_not_production_ingestion",))
        documents.append(Document(source.filename, source.sha256, source.role,
                                  source.supplier_handle, None, parsed))
    return EvidenceRepository(manifest.pack_id, 1, documents)


def _anchor_for_block(block, item_id):
    return Anchor(document=block.document_id, role=block.role,
                  supplier_handle=block.supplier, item_id=item_id, text=block.text,
                  page=block.page_idx + 1 if block.page_idx is not None else None,
                  sheet=block.sheet, cell=block.cell)


def _allowed_pdf_refs(manifest, repo, supplier, item_id):
    pages = {anchor.page for anchor in manifest.buyer_anchors.values() if anchor.item_id == item_id}
    result = []
    for block in repo.blocks:
        if block.page_idx is None:
            continue
        if block.role == "requirement":
            if block.page_idx + 1 in pages:
                result.append(block.evidence_id)
            continue
        if block.supplier != supplier:
            continue
        anchor = _anchor_for_block(block, item_id)
        if any(binding.document == block.document_id and binding.item_id == item_id
               and contains(binding.scope, anchor) for binding in manifest.bindings):
            result.append(block.evidence_id)
    if not result:
        raise ValueError("retrieval_empty_item_scope")
    return tuple(result)


def prepare_protocol(root: Path):
    """Read local originals/gold only. No .env, credentials or external services."""
    from .corpus import load_pack

    packs, queries = [], []
    for pack_id in dict.fromkeys(row[1] for row in QUERY_DEFINITIONS):
        manifest, gold, originals = load_pack(Path(root), pack_id)
        repo = repository_from_originals(manifest, originals)
        packs.append({"pack_id": pack_id, "manifest_sha256": sha256(manifest.model_dump_json().encode()).hexdigest(),
                      "gold_sha256": sha256(gold.model_dump_json().encode()).hexdigest(),
                      "originals": {doc.filename: doc.sha256 for doc in manifest.documents}})
        for query_id, _, supplier, item_id, criterion_id, question in (
            row for row in QUERY_DEFINITIONS if row[1] == pack_id
        ):
            cells = [cell for cell in gold.cells if cell.key() == (pack_id, supplier, item_id, criterion_id)]
            if len(cells) != 1:
                raise ValueError("retrieval_query_gold_identity")
            answerable = query_id not in NO_ANSWER_IDS
            if not answerable and cells[0].expected_business != "证据不足":
                raise ValueError("retrieval_no_answer_gold_mismatch")
            selected_groups = SELECTED_GROUP_IDS.get(query_id)
            groups = (tuple(tuple(anchor for anchor in group.alternatives if anchor.page is not None)
                            for group in cells[0].evidence_groups if group.purpose == "supplier_claim"
                            and (selected_groups is None or group.group_id in selected_groups))
                      if answerable else ())
            if answerable and (not groups or any(not group for group in groups)):
                raise ValueError("retrieval_query_not_pdf_representable")
            refs = _allowed_pdf_refs(manifest, repo, supplier, item_id)
            query = QuerySpec(query_id, pack_id, supplier, item_id, criterion_id, question, groups, refs, answerable)
            # Verify gold text and page against the readback before any send.
            for group in groups:
                if not any(_anchor_matches(a, b, b.text) for a in group for b in repo.blocks):
                    raise ValueError("retrieval_gold_source_mismatch")
            queries.append(query.payload())
    plan = {"schema_version": 1, "protocol_id": PROTOCOL,
            "data_class": "ai_authored_seen_development", "independent_sealed_packs": 0,
            "query_denominator": len(QUERY_DEFINITIONS), "top_k": TOP_K,
            "answerable_query_denominator": len(QUERY_DEFINITIONS) - len(NO_ANSWER_IDS),
            "no_answer_query_denominator": len(NO_ANSWER_IDS),
            "candidate_k": CANDIDATE_K, "rrf_k": RRF_K,
            "scope": "supplier-and-item-bound-pdf-only-common-corpus",
            "parser_adapter": "evaluation-original-readback-v1-not-MinerU-acceptance",
            "packs": packs, "queries": queries,
            "notice": "本对照限定PDF；无答案不混入有答案Recall分母，非空检索结果不等于已回答/幻觉；top-k/readback不代表全量覆盖。"}
    return plan | {"protocol_sha256": digest(plan)}


def rrf_rank(lexical, dense):
    if any(len(ids) != len(set(ids)) for ids in (lexical, dense)):
        raise ValueError("duplicate_ranked_chunk")
    scores = {}
    for ids in (lexical, dense):
        for rank, chunk_id in enumerate(ids, 1):
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1 / (RRF_K + rank)
    return sorted(scores, key=lambda cid: (-scores[cid], cid))


def _lexical_ids(chunks, question):
    terms = _tokens(question)
    corpus = [_tokens(chunk.text) for chunk in chunks]
    if not terms or not corpus or not any(corpus):
        return []
    scores = BM25Plus(corpus).get_scores(terms)
    selected = [i for i, tokens in enumerate(corpus) if set(terms).intersection(tokens)]
    selected.sort(key=lambda i: (-float(scores[i]), chunks[i].chunk_id))
    return [chunks[i].chunk_id for i in selected[:CANDIDATE_K]]


def _anchor_matches(anchor, block, span_text):
    return (anchor.document == block.document_id and anchor.role == block.role
            and anchor.supplier_handle == block.supplier
            and anchor.page == (block.page_idx + 1 if block.page_idx is not None else None)
            and anchor.sheet == block.sheet and anchor.cell == block.cell
            and _text(anchor.text) in _text(span_text))


def score_hits(repo, chunks, query):
    """Group recall at 5, conventional first-relevant reciprocal rank at 5."""
    groups = query.groups
    if not query.answerable:
        if groups:
            raise ValueError("retrieval_no_answer_has_gold_groups")
        return {"matched_groups": 0, "gold_groups": 0, "recall_at_5": None, "mrr_at_5": None,
                "all_required_groups_at_5": None}
    if not groups or any(not group for group in groups):
        raise ValueError("retrieval_gold_groups")
    satisfied, first_rank = set(), None
    for rank, chunk in enumerate(chunks[:TOP_K], 1):
        for index, group in enumerate(groups):
            for span in chunk.spans:
                block = repo.read([span.evidence_id])[0]
                if any(_anchor_matches(anchor, block, block.text[span.start:span.end]) for anchor in group):
                    satisfied.add(index)
                    first_rank = first_rank or rank
    return {"matched_groups": len(satisfied), "gold_groups": len(groups),
            "recall_at_5": len(satisfied) / len(groups),
            "mrr_at_5": 1 / first_rank if first_rank else 0.0,
            "all_required_groups_at_5": len(satisfied) == len(groups)}


def compare_query(repo, chunks, query, embedding, store, *, clock=time.perf_counter, dense_error=None):
    if not chunks or len(chunks) > MAX_CHUNK_FILTER_IDS:
        raise ValueError("comparison_scope_budget")
    allowed = {chunk.chunk_id: chunk for chunk in chunks}
    if len(allowed) != len(chunks) or any(
        chunk.case_id != repo.case_id or chunk.snapshot_id != repo.snapshot_id
        or (chunk.role != "requirement" and chunk.supplier_id != query.supplier)
        or (query.allowed_evidence_refs is not None
            and not set(chunk.evidence_refs) <= set(query.allowed_evidence_refs))
        for chunk in chunks
    ):
        raise ValueError("comparison_scope")
    for chunk in chunks:
        restore_text(chunk, repo)
    started = clock()
    lexical = _lexical_ids(chunks, query.question)
    lexical_ms = (clock() - started) * 1000
    started = clock()
    dense, error = [], dense_error
    try:
        if error:
            raise VectorUnavailable(error)
        vectors = validate_vectors(embedding.embed([query.question]), count=1, dimension=embedding.dimension)
        scope = SearchScope.from_chunks(tuple(chunks), supplier=query.supplier)
        scope = replace(scope, chunk_ids=tuple(sorted(allowed)))
        hits = store.search(vectors[0], scope, limit=CANDIDATE_K)
        if not isinstance(hits, list) or len(hits) > CANDIDATE_K:
            raise ValueError("vector_hit_scope")
        for hit in hits:
            if (not isinstance(hit, dict) or hit.get("chunk_id") not in allowed
                or not scope.allows(allowed[hit["chunk_id"]])
                or hit.get("fingerprint") != allowed[hit["chunk_id"]].fingerprint
                or hit["chunk_id"] in dense):
                raise ValueError("vector_hit_scope")
            dense.append(hit["chunk_id"])
    except (EmbeddingError, VectorUnavailable, ValueError) as exc:
        code = str(exc)
        error = code if code in {"vector_hit_scope", "embedding_service_unavailable",
                                "embedding_model_not_installed", "embedding_model_changed",
                                "embedding_invalid_vectors", "vector_service_unavailable",
                                "vector_index_not_ready", "embedding_configuration",
                                "milvus_configuration_error", "retrieval_embedding_dimension",
                                "evaluation_collection_collision"} else "dense_failed"
        dense = []
    dense_ms = (clock() - started) * 1000
    fused_started = clock()
    fused = rrf_rank(lexical, dense)
    fusion_ms = (clock() - fused_started) * 1000
    methods = {}
    for method, ids, duration in (("bm25", lexical, lexical_ms), ("dense", dense, dense_ms),
                                  ("hybrid", fused, lexical_ms + dense_ms + fusion_ms)):
        rows = [allowed[cid] for cid in ids[:TOP_K]]
        methods[method] = {"hits": [{"chunk_id": chunk.chunk_id, "document": chunk.document_id,
                                     "text": restore_text(chunk, repo),
                                     "evidence_refs": list(chunk.evidence_refs)} for chunk in rows],
                           **score_hits(repo, rows, query), "candidate_chunks": len(chunks),
                           "latency_ms": duration, "latency_scope": "shared-channel-reused-no-index-build",
                           "error_code": error if method == "dense" else None,
                           "degraded_reason": error if method == "hybrid" else None,
                           "retrieval_mode": "bm25" if method == "hybrid" and error else method,
                           "cross_supplier_hits": sum(chunk.role != "requirement"
                                                      and chunk.supplier_id != query.supplier for chunk in rows),
                           "cross_item_hits": (sum(not set(chunk.evidence_refs) <= set(query.allowed_evidence_refs)
                                                   for chunk in rows)
                                               if query.allowed_evidence_refs is not None else None)}
    return {"query_id": query.query_id, "pack_id": query.pack_id,
            "supplier": query.supplier, "item_id": query.item_id, "question": query.question,
            "answerable": query.answerable,
            "complete_coverage": False, "methods": methods}


def aggregate_results(results):
    count = len(results)
    metrics = {}
    for method in METHODS:
        rows = [row["methods"][method] for row in results]
        positive = [row["methods"][method] for row in results if row.get("answerable", True)]
        negative = [row["methods"][method] for row in results if not row.get("answerable", True)]
        latencies = sorted(row["latency_ms"] for row in rows)
        metrics[method] = {"query_denominator": count,
                           "answerable_query_denominator": len(positive), "no_answer_query_denominator": len(negative),
                           "recall_at_5": sum(row["recall_at_5"] for row in positive) / len(positive) if positive else None,
                           "mrr_at_5": sum(row["mrr_at_5"] for row in positive) / len(positive) if positive else None,
                           "strict_success_queries": sum(bool(row["all_required_groups_at_5"]) for row in positive),
                           "no_answer_nonempty_hit_queries": sum(bool(row["hits"]) for row in negative),
                           "no_answer_metric_notice": "nonempty retrieval is not a supported answer or measured hallucination",
                           "failed_queries": sum(bool(row["error_code"]) for row in rows),
                           "degraded_queries": sum(bool(row["degraded_reason"]) for row in rows),
                           "cross_supplier_hits": sum(row["cross_supplier_hits"] for row in rows),
                           "cross_item_hits": (sum(row["cross_item_hits"] for row in rows)
                                               if all(row["cross_item_hits"] is not None for row in rows) else None),
                           "latency_median_ms": median(latencies) if latencies else None,
                           "latency_max_ms": max(latencies) if latencies else None,
                           "latency_scope": "shared-channel-reused-no-index-build"}
    return {"queries": count, "methods": metrics}


def _queries(plan, pack_id):
    return [QuerySpec(row["query_id"], row["pack_id"], row["supplier"], row["item_id"],
                      row["criterion_id"], row["question"],
                      tuple(tuple(Anchor.model_validate(anchor) for anchor in group) for group in row["groups"]),
                      tuple(row["allowed_evidence_refs"]), row["answerable"])
            for row in plan["queries"] if row["pack_id"] == pack_id]


def run_comparison(root, plan, embedding, store_factory, *, startup_error=None, checkpoint=None):
    """Explicit send boundary, injectable adapters for offline failure tests."""
    from .corpus import load_pack

    if prepare_protocol(root) != plan:
        raise ValueError("retrieval_protocol_changed")
    results, scopes, failures = [], [], []

    def save(stage, query_id=None):
        if checkpoint is None:
            return
        done = {row["query_id"] for row in results}
        progress = {"mode": "sending", "protocol": plan, "stage": stage,
                    "results": results, "scopes": scopes, "failures": failures,
                    "planned_queries": plan["query_denominator"], "completed_queries": len(results),
                    "unfinished_query_ids": [row["query_id"] for row in plan["queries"] if row["query_id"] not in done],
                    "current_query_id": query_id, "outcome_unknown": True, "automatic_retries": False,
                    "embedding_requests": getattr(embedding, "embedding_requests", None),
                    "external_requests": None, "actual_cost": None, "complete_coverage": False}
        try:
            checkpoint(progress)
        except OSError:
            raise CheckpointError("retrieval_checkpoint_failed") from None

    for pack in plan["packs"]:
        pack_id = pack["pack_id"]
        queries = _queries(plan, pack_id)
        store = None
        start = time.perf_counter()
        try:
            # Inputs are revalidated per pack after earlier queries may already
            # have been sent. A changed/deleted later pack must not erase those
            # results or remove its own frozen questions from the denominator.
            manifest, _, originals = load_pack(Path(root), pack_id)
            repo = repository_from_originals(manifest, originals)
            # Offline lexical scoring still runs if the real dense adapter fails.
            # This placeholder identifies only local chunks, never a measured model.
            revision = embedding.revision or "unresolved-embedding-not-semantic-measurement"
            chunks = chunk_snapshot(repo, "dynamic-evaluation-" + pack_id, revision,
                                    dimension=embedding.dimension)
            index_error = startup_error
            index_started = time.perf_counter()
            if not index_error:
                try:
                    store = store_factory(embedding.revision, ChunkPolicy().revision)
                    if store._call("has_collection"):
                        raise ValueError("evaluation_collection_collision")
                    store._evaluation_owned = True
                    scopes.append({"pack_id": pack_id, "temporary_collection": store.collection,
                                   "collection_identity": store.identity, "ownership": "this_attempt"})
                    save("index_sending")
                    build_index(repo, "dynamic-evaluation-" + pack_id, embedding, store)
                except (ValueError, EmbeddingError, VectorUnavailable) as exc:
                    code = str(exc)
                    index_error = (code if code in {"embedding_service_unavailable", "embedding_model_changed",
                                                   "embedding_model_not_installed", "vector_service_unavailable",
                                                   "evaluation_collection_collision"}
                                   else "vector_index_not_ready")
            scopes.append({"pack_id": pack_id, "index_build_ms": (time.perf_counter() - index_started) * 1000,
                           "index_error": index_error})
            if index_error:
                failures.append({"pack_id": pack_id, "error_code": index_error})
            save("index_recorded")
            for query in queries:
                selected = tuple(c for c in chunks if set(c.evidence_refs) <= set(query.allowed_evidence_refs)
                                 and (c.role == "requirement" or c.supplier_id == query.supplier))
                represented = {ref for c in selected for ref in c.evidence_refs}
                scopes.append({"query_id": query.query_id, "selected_chunks": len(selected),
                               "snapshot_chunks": len(chunks), "allowed_pdf_refs": len(query.allowed_evidence_refs),
                               "represented_refs": len(represented),
                               "unrepresented_refs": sorted(set(query.allowed_evidence_refs) - represented),
                               "xlsx_chunks_excluded": sum(c.document_id.endswith(".xlsx") for c in chunks),
                               "comparison_source_scope": "pdf_only"})
                save("query_sending", query.query_id)
                results.append(compare_query(repo, selected, query, embedding, store, dense_error=index_error))
                save("query_recorded", query.query_id)
        except (OSError, ValueError, EmbeddingError, VectorUnavailable):
            failures.append({"pack_id": pack_id, "error_code": "comparison_pack_failed"})
            known = {row["query_id"] for row in results}
            for query in queries:
                if query.query_id in known:
                    continue
                methods = {}
                for method in METHODS:
                    methods[method] = {"hits": [], "matched_groups": 0, "gold_groups": len(query.groups),
                                       "recall_at_5": 0.0 if query.answerable else None,
                                       "mrr_at_5": 0.0 if query.answerable else None,
                                       "all_required_groups_at_5": False if query.answerable else None, "candidate_chunks": 0,
                                       "latency_ms": 0.0, "error_code": "comparison_pack_failed",
                                       "degraded_reason": None, "retrieval_mode": method,
                                       "cross_supplier_hits": 0, "cross_item_hits": 0}
                results.append({"query_id": query.query_id, "pack_id": pack_id,
                                "supplier": query.supplier, "item_id": query.item_id,
                                "answerable": query.answerable,
                                "question": query.question, "complete_coverage": False, "methods": methods})
        finally:
            cleanup = "not_created"
            if store is not None:
                try:
                    if getattr(store, "_evaluation_owned", False) and store._call("has_collection"):
                        details = store._call("describe_collection")
                        if (json.loads(details["description"]) != store.identity
                            or not re.fullmatch(r"docucheck_dyn_eval_[a-f0-9]{32}", store.collection)):
                            raise ValueError("evaluation_cleanup_identity")
                        store._call("drop_collection")
                        cleanup = "dropped" if not store._call("has_collection") else "failed"
                except (ValueError, KeyError, TypeError, VectorUnavailable):
                    cleanup = "failed"
                try:
                    store.close()
                except VectorUnavailable:
                    cleanup = "failed"
            scopes.append({"pack_id": pack_id, "setup_and_queries_ms": (time.perf_counter() - start) * 1000,
                           "temporary_collection_cleanup": cleanup})
        save("pack_recorded")
    if {r["query_id"] for r in results} != {row[0] for row in QUERY_DEFINITIONS}:
        raise ValueError("retrieval_result_denominator")
    return {"mode": "send", "protocol": plan, "results": results,
            "aggregate": aggregate_results(results), "scopes": scopes, "failures": failures,
            "embedding_revision": embedding.revision,
            "embedding_requests": getattr(embedding, "embedding_requests", None),
            "embedding_requests_scope": "Ollama /api/embed POST attempts; metadata GET and Milvus calls excluded",
            "external_requests": None,
            "actual_cost": None, "complete_coverage": False,
            "runner_sha256": sha256(Path(__file__).read_bytes()).hexdigest(),
            "notice": "24个作者已见开发问题，20有答案/4无答案，PDF readback检索对照；不是独立RAG质量或生产PDF解析验收。"}


def _send(root, plan, env_file, *, checkpoint=None):
    if env_file is not None:
        from dotenv import load_dotenv
        load_dotenv(env_file, override=False)
    from backend.app.vector_store.config import MilvusConfig
    from backend.app.vector_store.embedding import OllamaConfig, OllamaEmbedding
    from backend.app.vector_store.store import MilvusIndex

    embedding = OllamaEmbedding(OllamaConfig.from_env())
    startup_error = None
    try:
        config = None
        try:
            embedding.resolve()
            config = MilvusConfig.from_env()
            if config.dimension != embedding.dimension:
                raise ValueError("retrieval_embedding_dimension")
        except (EmbeddingError, ValueError) as exc:
            code = str(exc)
            startup_error = (code if code in {"embedding_service_unavailable", "embedding_model_not_installed",
                                             "embedding_model_changed", "milvus_configuration_error",
                                             "retrieval_embedding_dimension"} else "dense_failed")
        return run_comparison(
            root, plan, embedding,
            lambda revision, schema: MilvusIndex(config, revision, schema,
                                                 collection="docucheck_dyn_eval_" + uuid4().hex),
            startup_error=startup_error,
            checkpoint=checkpoint,
        )
    finally:
        embedding.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--send", action="store_true")
    parser.add_argument("--plan-file", type=Path)
    parser.add_argument("--env-file", type=Path)
    args = parser.parse_args(argv)
    if args.output.exists() or args.output.is_symlink():
        parser.error("output_exists")
    if not args.send and (args.plan_file is not None or args.env_file is not None):
        parser.error("send_options_require_explicit_send")
    plan = prepare_protocol(args.root)
    if args.send:
        if args.plan_file is None or args.plan_file.stat().st_size > 1_000_000:
            parser.error("frozen_plan_required")
        frozen = json.loads(args.plan_file.read_text(encoding="utf-8"))
        if frozen.get("mode") != "preview" or frozen.get("protocol") != plan:
            parser.error("retrieval_protocol_changed")
    result = {"mode": "sending" if args.send else "preview", "protocol": plan,
              "external_requests": None if args.send else 0, "actual_cost": None,
              "notice": ("此 attempt 已保留；若进程中断不能自动重发。" if args.send else
                         "仅校验原件及冻结查询；未读环境配置、未调用Embedding/Milvus。")}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    # Reserve exclusively, then persist per-query progress with synced atomic
    # replacement. No completed subset aggregate is published after interruption.
    if not args.send:
        _write_new(args.output, result)
    else:
        result.update(outcome_unknown=True, automatic_retries=False)
        journal = AttemptJournal(args.output, result)
        try:
            result = _send(args.root, plan, args.env_file, checkpoint=journal.update)
        except CheckpointError:
            print(json.dumps({"error": "retrieval_checkpoint_failed", "automatic_retries": False}))
            return 2
        except (OSError, ValueError, EmbeddingError, VectorUnavailable):
            result = json.loads(args.output.read_bytes()) | {
                "mode": "failed", "error_code": "comparison_startup_failed",
                "external_requests": None, "actual_cost": None,
                "notice": "保留本轮失败及已有问题结果；请求数未知，不自动重发，不输出连接参数。"}
        journal.update(result, finished=True)
    print(json.dumps({"mode": result["mode"], "queries": plan["query_denominator"],
                      "output": str(args.output), "external_requests": result.get("external_requests"),
                      "embedding_requests": result.get("embedding_requests")},
                     ensure_ascii=False))
    return int(result["mode"] == "failed" or result["mode"] == "send" and (
        result["aggregate"]["methods"]["dense"]["failed_queries"] > 0
        or any(scope.get("temporary_collection_cleanup") == "failed" for scope in result["scopes"])
    ))


if __name__ == "__main__":
    raise SystemExit(main())
