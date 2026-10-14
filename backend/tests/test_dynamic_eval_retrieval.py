"""Retrieval protocol tests use doubles, never claim semantic benchmark results."""

import importlib.util
from dataclasses import replace
from pathlib import Path

import pytest

from backend.app.evidence.chunks import chunk_snapshot
from backend.app.evidence.repository import load_case
from backend.app.vector_store.store import VectorUnavailable
from evals.dynamic.contracts import Anchor


def module():
    assert importlib.util.find_spec("evals.dynamic.retrieval"), "dynamic retrieval comparison missing"
    from evals.dynamic import retrieval
    return retrieval


@pytest.fixture
def repo():
    return load_case(Path(__file__).resolve().parents[2] / "examples/dev-pack-001")


class Embedding:
    revision = "test-double-not-semantic-results"
    dimension = 3

    def __init__(self):
        self.calls = []

    def embed(self, texts):
        self.calls.append(texts)
        return [[1.0, 0.0, 0.0]]


class Store:
    def __init__(self, chunks, *, forged=False, unavailable=False):
        self.chunks = chunks
        self.calls = []
        self.forged, self.unavailable = forged, unavailable

    def search(self, vector, scope, limit, *, include_scores=False):
        self.calls.append(scope)
        if self.unavailable:
            raise VectorUnavailable("vector_service_unavailable")
        if self.forged:
            return [{"chunk_id": "unknown", "fingerprint": "fake"}]
        return [{"chunk_id": c.chunk_id, "fingerprint": c.fingerprint}
                for c in self.chunks if scope.allows(c)][:limit]


def query_and_chunks(repo):
    target = next(b for b in repo.blocks if b.role == "spec" and "SSD" in b.text)
    anchor = Anchor(document=target.document_id, role="spec", supplier_handle="vendor-a",
                    item_id="phone", text=target.text, page=target.page_idx + 1)
    query = module().QuerySpec("storage", "dyn-d01", "vendor-a", "phone", "capacity",
                               "供应商A的硬盘多大？", ((anchor,),))
    chunks = chunk_snapshot(repo, "evaluation-project", Embedding.revision, dimension=3)
    selected = tuple(c for c in chunks if c.role == "requirement" or c.supplier_id == "vendor-a")
    return query, selected


def test_fixed_six_queries_use_only_selected_supplier_and_item():
    assert len(module().QUERY_DEFINITIONS) == 6
    assert {row[1] for row in module().QUERY_DEFINITIONS} == {"dyn-d01", "dyn-d04", "dyn-d10"}
    assert len({row[0] for row in module().QUERY_DEFINITIONS}) == 6


def test_rrf_uses_rank_not_incompatible_dense_raw_score():
    result = module().rrf_rank(["a", "b"], ["b", "c"])
    assert result[0] == "b"
    assert result[1:] == ["a", "c"]
    with pytest.raises(ValueError, match="duplicate_ranked_chunk"):
        module().rrf_rank(["a", "a"], [])


def test_all_methods_share_exact_scope_and_dense_sends_query_only(repo):
    query, chunks = query_and_chunks(repo)
    embedding, store = Embedding(), Store(chunks)
    result = module().compare_query(repo, chunks, query, embedding, store)
    assert set(result["methods"]) == {"bm25", "dense", "hybrid"}
    assert embedding.calls == [[query.question]]
    assert set(store.calls[0].chunk_ids) == {c.chunk_id for c in chunks}
    assert all(row["candidate_chunks"] == len(chunks) for row in result["methods"].values())
    assert all(row["cross_supplier_hits"] == 0 for row in result["methods"].values())
    assert result["complete_coverage"] is False


def test_forged_dense_hit_fails_closed_and_is_not_a_semantic_success(repo):
    query, chunks = query_and_chunks(repo)
    result = module().compare_query(repo, chunks, query, Embedding(), Store(chunks, forged=True))
    assert result["methods"]["dense"]["error_code"] == "vector_hit_scope"
    assert result["methods"]["dense"]["hits"] == []
    assert result["methods"]["hybrid"]["degraded_reason"] == "vector_hit_scope"


def test_service_failure_keeps_dense_query_in_denominator_and_marks_hybrid_degraded(repo):
    query, chunks = query_and_chunks(repo)
    result = module().compare_query(repo, chunks, query, Embedding(), Store(chunks, unavailable=True))
    aggregate = module().aggregate_results([result])
    assert aggregate["queries"] == 1
    assert aggregate["methods"]["dense"]["query_denominator"] == 1
    assert aggregate["methods"]["dense"]["failed_queries"] == 1
    assert aggregate["methods"]["hybrid"]["degraded_queries"] == 1
    assert result["methods"]["hybrid"]["retrieval_mode"] == "bm25"


def test_extra_supplier_chunk_rejected_before_embedding(repo):
    query, chunks = query_and_chunks(repo)
    embedding = Embedding()
    foreign = replace(chunks[0], supplier_id="vendor-b", role="spec")
    with pytest.raises(ValueError, match="comparison_scope"):
        module().compare_query(repo, (*chunks, foreign), query, embedding, Store(chunks))
    assert not embedding.calls


def test_recall_requires_all_evidence_groups_and_exact_source_location(repo):
    query, chunks = query_and_chunks(repo)
    target = next(c for c in chunks if "SSD" in c.text)
    score = module().score_hits(repo, [target], query)
    assert score["recall_at_5"] == 1
    assert score["mrr_at_5"] == 1
    wrong = replace(query, groups=((query.groups[0][0].model_copy(update={"page": 2}),),))
    assert module().score_hits(repo, [target], wrong)["recall_at_5"] == 0


def test_empty_queries_cannot_be_reported_as_perfect():
    report = module().aggregate_results([])
    assert report["queries"] == 0
    assert report["methods"]["dense"]["recall_at_5"] is None


def test_index_failure_preserves_bm25_and_explicit_hybrid_fallback(repo):
    query, chunks = query_and_chunks(repo)
    embedding = Embedding()
    result = module().compare_query(repo, chunks, query, embedding, None,
                                    dense_error="vector_index_not_ready")
    assert not embedding.calls
    assert result["methods"]["bm25"]["hits"]
    assert result["methods"]["dense"]["error_code"] == "vector_index_not_ready"
    assert result["methods"]["hybrid"]["hits"] == result["methods"]["bm25"]["hits"]


@pytest.fixture(scope="module")
def protocol_root(tmp_path_factory):
    from backend.tests.test_candidate_corpus import FONT
    if not FONT.is_file():
        pytest.skip("Chinese test font unavailable")
    from evals.dynamic.corpus import build_corpus
    root = tmp_path_factory.mktemp("retrieval-protocol") / "corpus"
    build_corpus(root, font_path=FONT, pack_ids=["dyn-d01", "dyn-d04", "dyn-d10"])
    return root


def test_preview_freezes_six_queries_without_environment_config_or_network(protocol_root, tmp_path, monkeypatch):
    from backend.app.vector_store.config import MilvusConfig
    from backend.app.vector_store.embedding import OllamaConfig
    fail = lambda *args, **kwargs: pytest.fail("preview loaded external configuration")
    monkeypatch.setattr(MilvusConfig, "from_env", fail)
    monkeypatch.setattr(OllamaConfig, "from_env", fail)
    import json
    output = tmp_path / "preview.json"
    assert module().main(["--root", str(protocol_root), "--output", str(output)]) == 0
    preview = json.loads(output.read_text(encoding="utf-8"))
    assert preview["external_requests"] == 0
    assert preview["protocol"]["query_denominator"] == 6
    assert "expected_business" not in output.read_text(encoding="utf-8")
    assert preview["protocol"]["parser_adapter"].endswith("not-MinerU-acceptance")


def test_preflight_rejects_changed_frozen_plan_and_preserves_existing_report(protocol_root, tmp_path):
    import json
    preview = tmp_path / "preview.json"
    module().main(["--root", str(protocol_root), "--output", str(preview)])
    original = preview.read_bytes()
    with pytest.raises(SystemExit):
        module().main(["--root", str(protocol_root), "--output", str(preview)])
    assert preview.read_bytes() == original
    changed = json.loads(original)
    changed["protocol"]["queries"][0]["question"] = "运行后换题"
    preview.write_text(json.dumps(changed), encoding="utf-8")
    with pytest.raises(SystemExit):
        module().main(["--root", str(protocol_root), "--output", str(tmp_path / "send.json"),
                       "--send", "--plan-file", str(preview)])


def test_unresolved_embedding_keeps_fixed_denominator_and_real_bm25(protocol_root):
    protocol = module().prepare_protocol(protocol_root)
    embedding = Embedding()
    embedding.revision = None
    output = module().run_comparison(protocol_root, protocol, embedding, None,
                                     startup_error="embedding_service_unavailable")
    assert len(output["results"]) == 6
    assert output["aggregate"]["methods"]["dense"]["failed_queries"] == 6
    assert output["aggregate"]["methods"]["bm25"]["failed_queries"] == 0
    assert output["embedding_revision"] is None
    assert not embedding.calls


class BatchEmbedding(Embedding):
    def embed(self, texts):
        self.calls.append(texts)
        return [[1.0, 0.0, 0.0] for _ in texts]


class RuntimeStore(Store):
    def __init__(self, revision, schema, *, collision=False):
        super().__init__(())
        self.collection = "docucheck_dyn_eval_" + "a" * 32
        self.identity = {"embedding_revision": revision, "chunk_schema": schema}
        self.exists = collision
        self.dropped = False
        self.closed = False

    def _call(self, method):
        import json
        if method == "has_collection":
            return self.exists
        if method == "describe_collection":
            return {"description": json.dumps(self.identity)}
        if method == "drop_collection":
            self.exists = False
            self.dropped = True
            return None
        pytest.fail(f"unexpected operation {method}")

    def ensure_collection(self):
        self.exists = True

    def upsert(self, chunks, vectors):
        self.chunks = (*self.chunks, *chunks)

    def verify(self, chunks):
        return {c.chunk_id for c in chunks} == {c.chunk_id for c in self.chunks}

    def close(self):
        self.closed = True


def test_send_protocol_double_keeps_common_pdf_scope_and_cleans_only_owned_collections(protocol_root):
    plan = module().prepare_protocol(protocol_root)
    embedding, stores = BatchEmbedding(), []
    def factory(revision, schema):
        store = RuntimeStore(revision, schema)
        stores.append(store)
        return store
    result = module().run_comparison(protocol_root, plan, embedding, factory)
    assert len(result["results"]) == 6
    assert all(s.dropped and s.closed for s in stores)
    assert not result["failures"]
    assert all(not h["document"].endswith(".xlsx") for r in result["results"]
               for channel in r["methods"].values() for h in channel["hits"])
    assert all(s.get("xlsx_chunks_excluded", 0) > 0 for s in result["scopes"] if "query_id" in s)
    assert result["embedding_revision"].startswith("test-double")
    assert all("expected_business" not in text for batch in embedding.calls for text in batch)


def test_existing_collection_collision_is_never_dropped(protocol_root):
    plan = module().prepare_protocol(protocol_root)
    stores = []
    def factory(revision, schema):
        store = RuntimeStore(revision, schema, collision=True)
        stores.append(store)
        return store
    result = module().run_comparison(protocol_root, plan, BatchEmbedding(), factory)
    assert all(not s.dropped and s.closed for s in stores)
    assert result["aggregate"]["methods"]["dense"]["failed_queries"] == 6
    assert result["aggregate"]["methods"]["bm25"]["failed_queries"] == 0


def test_later_pack_tampering_preserves_completed_queries_and_frozen_denominator(protocol_root, tmp_path):
    import shutil

    root = tmp_path / "corpus"
    shutil.copytree(protocol_root, root)
    plan = module().prepare_protocol(root)
    stores = []

    class TamperAfterFirstPack(RuntimeStore):
        def close(self):
            super().close()
            if len(stores) == 1:
                # Tamper after a fully executed pack, before loading the next.
                (root / "inputs/dyn-d04/vendor-a-spec.pdf").write_bytes(b"changed original")

    def factory(revision, schema):
        store = TamperAfterFirstPack(revision, schema)
        stores.append(store)
        return store

    result = module().run_comparison(root, plan, BatchEmbedding(), factory)
    assert len(result["results"]) == plan["query_denominator"] == 6
    first = [row for row in result["results"] if row["pack_id"] == "dyn-d01"]
    failed = [row for row in result["results"] if row["pack_id"] == "dyn-d04"]
    assert len(first) == len(failed) == 2
    assert all(row["methods"]["dense"]["error_code"] is None for row in first)
    assert all(row["methods"]["dense"]["error_code"] == "comparison_pack_failed" for row in failed)
    assert result["aggregate"]["methods"]["dense"]["failed_queries"] == 2
    assert all(store.dropped and store.closed for store in stores)


def test_pdf_readback_adapter_cannot_claim_production_parse_complete(protocol_root):
    from evals.dynamic.corpus import load_pack
    manifest, _, originals = load_pack(protocol_root, "dyn-d01")
    repo = module().repository_from_originals(manifest, originals)
    assert all(d.parsed.status == "partial" for d in repo.documents)
    assert not repo.coverage("vendor-a")["complete"]


def test_send_reserves_attempt_file_before_external_call(protocol_root, tmp_path, monkeypatch):
    import json

    from backend.app.vector_store.config import MilvusConfig
    from backend.app.vector_store.embedding import OllamaEmbedding
    retrieval = module()
    preview, output = tmp_path / "preview.json", tmp_path / "attempt.json"
    retrieval.main(["--root", str(protocol_root), "--output", str(preview)])
    def resolve(self):
        assert output.is_file(), "attempt must be reserved before sending"
        assert json.loads(output.read_text(encoding="utf-8"))["mode"] == "sending"
        self.revision = "test-reserved-attempt"
    monkeypatch.setattr(OllamaEmbedding, "resolve", resolve)
    monkeypatch.setattr(MilvusConfig, "from_env", lambda: type("Config", (), {"dimension": 1024})())
    monkeypatch.setattr(retrieval, "run_comparison", lambda *args, **kwargs: {
        "mode": "send", "aggregate": {"methods": {"dense": {"failed_queries": 0}}},
        "scopes": [], "embedding_requests": 0,
    })
    assert retrieval.main(["--root", str(protocol_root), "--output", str(output),
                           "--send", "--plan-file", str(preview)]) == 0
    assert json.loads(output.read_text(encoding="utf-8"))["mode"] == "send"


def test_exit_in_later_query_preserves_prior_query_and_unfinished_ids(protocol_root, tmp_path, monkeypatch):
    import json

    retrieval = module()
    preview, output = tmp_path / "preview.json", tmp_path / "attempt.json"
    retrieval.main(["--root", str(protocol_root), "--output", str(preview)])

    class StopDuringSecondQuery(RuntimeStore):
        def search(self, *args, **kwargs):
            if self.calls:
                raise SystemExit("simulated_process_exit")
            return super().search(*args, **kwargs)

    def send_with_double(root, plan, env_file, *, checkpoint=None):
        return retrieval.run_comparison(root, plan, BatchEmbedding(), StopDuringSecondQuery,
                                        **({"checkpoint": checkpoint} if checkpoint is not None else {}))

    monkeypatch.setattr(retrieval, "_send", send_with_double)
    with pytest.raises(SystemExit, match="simulated_process_exit"):
        retrieval.main(["--root", str(protocol_root), "--output", str(output),
                        "--send", "--plan-file", str(preview)])
    saved = json.loads(output.read_bytes())
    assert saved["mode"] == "sending"
    assert saved["planned_queries"] == 6
    assert saved["completed_queries"] == 1
    assert saved["results"][0]["query_id"] == "phone-capacity"
    assert saved["current_query_id"] == "phone-delivery"
    assert saved["current_query_id"] in saved["unfinished_query_ids"]
    assert len(saved["unfinished_query_ids"]) == 5
    assert saved["outcome_unknown"] is True
    assert saved["needs_reconciliation"] is True
    assert "aggregate" not in saved, "partial results must not look like a smaller completed benchmark"


def test_checkpoint_failure_stops_before_embedding_and_closes_owned_store(protocol_root):
    retrieval = module()
    plan = retrieval.prepare_protocol(protocol_root)
    embedding, stores = BatchEmbedding(), []

    def factory(revision, schema):
        store = RuntimeStore(revision, schema)
        stores.append(store)
        return store

    def local_disk_failure(progress):
        raise OSError("local_failure_must_not_be_treated_as_a_pack_failure")

    with pytest.raises(retrieval.CheckpointError, match="retrieval_checkpoint_failed"):
        retrieval.run_comparison(protocol_root, plan, embedding, factory, checkpoint=local_disk_failure)
    assert not embedding.calls
    assert len(stores) == 1 and stores[0].closed
    assert not stores[0].exists
