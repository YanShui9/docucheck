from dataclasses import replace
from pathlib import Path

import pytest

from backend.app.agent.graph import AgentSession
from backend.app.agent.tools import ToolExecutor
from backend.app.evidence.chunks import chunk_snapshot
from backend.app.evidence.hybrid import HybridRetriever, build_index
from backend.app.evidence.repository import EvidenceRepository, load_case
from backend.app.vector_store.store import SearchScope, VectorUnavailable

PACK = Path(__file__).resolve().parents[2] / "examples/dev-pack-001"


class Embedding:
    revision = "test-vector-model-v1"
    dimension = 3

    def __init__(self):
        self.calls = []

    def embed(self, texts):
        self.calls.append(texts)
        return [[1.0, 0.0, 0.0] for _ in texts]


class Store:
    def __init__(self):
        self.rows = {}
        self.scopes = []
        self.fail = False
        self.forge = False

    def ensure_collection(self):
        pass

    def upsert(self, chunks, vectors):
        self.rows.update({c.chunk_id: c for c in chunks})

    def verify(self, chunks):
        return all(c.chunk_id in self.rows for c in chunks)

    def search(self, vector, scope, limit):
        self.scopes.append(scope)
        if self.fail:
            raise VectorUnavailable("vector_service_unavailable")
        if self.forge:
            return [{"chunk_id": "foreign", "fingerprint": "fake"}]
        ordered = sorted(self.rows.values(), key=lambda c: ("工作日" not in c.text, c.chunk_id))
        return [{"chunk_id": c.chunk_id, "fingerprint": c.fingerprint}
                for c in ordered if scope.allows(c)][:limit]


def setup(repo=None):
    repo = repo or load_case(PACK)
    model, store = Embedding(), Store()
    receipt = build_index(repo, "project", model, store)
    return HybridRetriever(repo, "project", model, store, receipt=receipt), model, store


def test_semantic_only_hit_reads_original_source_and_is_used_by_agent_tool():
    retriever, model, store = setup()
    hits = retriever.search("什么时候能送到", supplier="vendor-a", limit=1)
    assert "工作日" in hits[0]["text"] and "dense" in hits[0]["channels"]
    assert "bm25" not in hits[0]["channels"]
    assert model.calls[-1] == ["什么时候能送到"]
    result = ToolExecutor(retriever.repository, retriever=retriever).execute(
        "search_evidence", {"query": "什么时候能送到", "supplier": "vendor-a", "limit": 1}, None)
    assert result["data"] == hits
    session = AgentSession(retriever.repository, object(), retriever=retriever)
    assert session.tools.retriever is retriever
    assert store.scopes[-1].snapshot_id == retriever.repository.snapshot_id


def test_both_channels_and_rrf_are_observable_without_adding_raw_scores():
    retriever, _, _ = setup()
    hits = retriever.search("工作日", supplier="vendor-a")
    match = next(hit for hit in hits if "工作日" in hit["text"])
    assert set(match["channels"]) == {"bm25", "dense"}
    assert match["score"] == pytest.approx(sum(1 / (60 + r) for r in match["ranks"].values()))
    assert all(hit["retrieval_mode"] == "hybrid" for hit in hits)


def test_hard_disk_alias_recovers_real_ssd_source_when_dense_rank_is_eight():
    repo = load_case(PACK)
    storage = next(block for block in repo.blocks
                   if block.role == "spec" and block.text == "标称 SSD 1 TB")

    class StorageEighthStore(Store):
        def search(self, vector, scope, limit):
            self.scopes.append(scope)
            allowed = [chunk for chunk in self.rows.values() if scope.allows(chunk)]
            target = next(chunk for chunk in allowed
                          if storage.evidence_id in chunk.evidence_refs)
            ordered = [chunk for chunk in allowed if chunk != target]
            ordered.insert(7, target)
            return [{"chunk_id": chunk.chunk_id, "fingerprint": chunk.fingerprint}
                    for chunk in ordered[:limit]]

    model, store = Embedding(), StorageEighthStore()
    receipt = build_index(repo, "project", model, store)
    retriever = HybridRetriever(repo, "project", model, store, receipt=receipt)
    query = "存照片和文件的硬盘有多大？"
    observation = ToolExecutor(repo, retriever=retriever).execute(
        "search_evidence", {"query": query, "supplier": "vendor-a", "limit": 6}, None)
    matches = [hit for hit in observation["data"]
               if storage.evidence_id in hit["evidence_refs"]]
    assert len(matches) == 1
    hit = matches[0]
    assert hit["text"] == storage.text and hit["ranks"] == {"bm25": 1, "dense": 8}
    assert model.calls[-1] == [query]
    assert observation["retrieval"]["complete_coverage"] is False
    source = retriever.read_source([storage.evidence_id])[0]
    assert source["document_id"] == "vendor-a-spec.pdf"
    assert source["role"] == "spec" and source["supplier"] == "vendor-a"
    assert source["page_idx"] == 0 and source["bbox"] == storage.bbox


@pytest.mark.parametrize("query", ["内存", "存照片和文件的硬盘有多大？"])
def test_supplier_scope_filters_both_channels_and_shared_requirements_are_allowed(query):
    original = load_case(PACK)
    spec = next(d for d in original.documents if d.role == "spec")
    foreign = replace(spec, document_id="foreign.pdf", supplier="vendor-b",
                      parsed=replace(spec.parsed, document_id="foreign.pdf"))
    repo = EvidenceRepository(original.case_id, 2, [*original.documents, foreign])
    retriever, _, store = setup(repo)
    hits = retriever.search(query, supplier="vendor-a")
    refs = [ref for hit in hits for ref in hit["evidence_refs"]]
    assert all(b.role == "requirement" or b.supplier == "vendor-a" for b in repo.read(refs))
    assert store.scopes[-1].supplier == "vendor-a"
    with pytest.raises(ValueError, match="supplier_scope"):
        retriever.search(query, supplier="unknown")


@pytest.mark.parametrize("query", ["供应商答应免费维修几年？", "这台电脑能防水吗？"])
def test_no_answer_nearest_sources_never_establish_complete_coverage(query):
    retriever, _, _ = setup()
    observation = ToolExecutor(retriever.repository, retriever=retriever).execute(
        "search_evidence", {"query": query, "supplier": "vendor-a", "limit": 6}, None)
    assert len(observation["data"]) == 6
    assert observation["retrieval"]["complete_coverage"] is False
    for hit in observation["data"]:
        sources = retriever.read_source(hit["evidence_refs"])
        assert hit["text"] == "\n".join(source["text"] for source in sources)
        assert all(source["role"] == "requirement" or source["supplier"] == "vendor-a"
                   for source in sources)
        assert all("防水" not in source["text"] and "免费维修" not in source["text"]
                   for source in sources)


def test_unready_and_unavailable_index_degrade_explicitly_and_never_imply_absence():
    retriever, model, store = setup()
    store.fail = True
    hits = retriever.search("内存")
    assert hits and all(h["degraded_reason"] == "vector_service_unavailable" for h in hits)
    assert retriever.last_diagnostics["complete_coverage"] is False
    unready = HybridRetriever(retriever.repository, "project", model, store)
    calls = len(model.calls)
    assert unready.search("nonexistent-zzzz") == []
    assert unready.last_diagnostics["degraded_reason"] == "vector_index_not_ready"
    assert len(model.calls) == calls
    observation = ToolExecutor(unready.repository, retriever=unready).execute(
        "search_evidence", {"query": "nonexistent-zzzz"}, None)
    assert observation["data"] == []
    assert observation["retrieval"]["degraded_reason"] == "vector_index_not_ready"


def test_forged_or_stale_index_cannot_disclose_sources():
    retriever, model, store = setup()
    store.forge = True
    with pytest.raises(ValueError, match="vector_hit_scope"):
        retriever.search("内存")
    old = retriever.receipt
    with pytest.raises(ValueError, match="index_receipt_mismatch"):
        HybridRetriever(retriever.repository, "other-project", model, store, receipt=old)
    other = load_case(PACK)
    with pytest.raises(ValueError, match="retriever_repository"):
        ToolExecutor(other, retriever=retriever)


def test_visibility_failure_cannot_issue_ready_receipt():
    store = Store()
    store.verify = lambda chunks: False
    with pytest.raises(VectorUnavailable, match="vector_index_not_visible"):
        build_index(load_case(PACK), "project", Embedding(), store)


def test_scope_expression_is_server_generated_and_escaped():
    repo = load_case(PACK)
    chunks = chunk_snapshot(repo, 'proj" or true', "revision", dimension=3)
    scope = SearchScope.from_chunks(chunks, supplier="vendor-a")
    expression = scope.expression()
    assert 'proj\\" or true' in expression
    assert "snapshot_id ==" in expression and "document_id in" in expression
    assert 'role == "requirement"' in expression
    assert not scope.allows(replace(chunks[0], snapshot_id="old"))
    assert not scope.allows(replace(chunks[0], project_id="other"))


def test_recheck_authorization_before_and_after_embedding_wait():
    retriever, model, _ = setup()
    checks = []

    def authorize():
        checks.append(1)
        if len(checks) == 2:
            raise ValueError("task_deleted")

    retriever.revalidate = authorize
    with pytest.raises(ValueError, match="task_deleted"):
        retriever.search("内存")
    assert len(checks) == 2 and model.calls[-1] == ["内存"]


def test_indexing_checks_permission_after_read_before_embedding():
    repo = load_case(PACK)
    model, store = Embedding(), Store()
    revoked = False
    original = repo.read

    def read(ids):
        nonlocal revoked
        result = original(ids)
        revoked = True
        return result

    def authorize():
        if revoked:
            raise ValueError("task_deleted")

    repo.read = read
    with pytest.raises(ValueError, match="task_deleted"):
        build_index(repo, "project", model, store, revalidate=authorize)
    assert model.calls == [] and store.rows == {}


def test_model_revision_change_cannot_query_previous_index():
    retriever, model, _ = setup()
    model.revision = "new-model"
    with pytest.raises(ValueError, match="index_model_mismatch"):
        retriever.search("内存")


def test_langgraph_executes_hybrid_search_then_grounded_source_answer():
    import json

    from langchain_core.messages import AIMessage

    retriever, model, _ = setup()

    class Provider:
        def decide(self, messages, tools, **kwargs):
            if messages[-1].type == "tool":
                observation = json.loads(messages[-1].content)
                name, args = "submit_answer", {"kind": "evidence", "check_result_ids": [],
                           "evidence_ids": observation["data"][0]["evidence_refs"]}
            else:
                name, args = "search_evidence", {"query": "什么时候能送到", "supplier": "vendor-a", "limit": 1}
            return AIMessage(content="", tool_calls=[{"id": "call", "name": name, "args": args}],
                             usage_metadata={"input_tokens": 100, "output_tokens": 20, "total_tokens": 120})

        def extract(self, *args, **kwargs):
            pytest.fail("source question does not need full fact extraction")

    result = AgentSession(retriever.repository, Provider(), retriever=retriever).ask("供应商 A 什么时候能送到？")
    assert result.status == "completed" and "工作日" in result.answer and "B9" in result.answer
    assert model.calls[-1] == ["什么时候能送到"]


def test_agent_cannot_submit_cached_source_after_authorization_revoked():
    from langchain_core.messages import AIMessage

    retriever, _, _ = setup()
    revoked = False

    def authorize():
        if revoked:
            raise ValueError("task_deleted")

    retriever.revalidate = authorize

    class Provider:
        steps = 0

        def decide(self, messages, tools, **kwargs):
            nonlocal revoked
            self.steps += 1
            if self.steps == 1:
                name, args = "search_evidence", {"query": "什么时候能送到", "supplier": "vendor-a", "limit": 1}
            else:
                revoked = True
                name, args = "submit_answer", {"kind": "evidence", "check_result_ids": [],
                           "evidence_ids": []}
                args["evidence_ids"] = list(next(c for c in retriever.chunks if "工作日" in c.text).evidence_refs)
            return AIMessage(content="", tool_calls=[{"id": "call", "name": name, "args": args}],
                             usage_metadata={"input_tokens": 100, "output_tokens": 20, "total_tokens": 120})

    result = AgentSession(retriever.repository, Provider(), retriever=retriever).ask("供应商 A 什么时候能送到？")
    assert result.status != "completed" and "工作日" not in (result.answer or "")


def test_retrieval_cannot_return_text_if_permission_changes_during_source_read():
    retriever, _, _ = setup()
    original_read = retriever.repository.read
    revoked = False

    def read(ids):
        nonlocal revoked
        blocks = original_read(ids)
        revoked = True
        return blocks

    def authorize():
        if revoked:
            raise ValueError("task_deleted")

    retriever.repository.read = read
    retriever.revalidate = authorize
    with pytest.raises(ValueError, match="task_deleted"):
        retriever.search("内存", limit=1)


def test_agent_cannot_send_cached_observation_to_provider_after_permission_change():
    from langchain_core.messages import AIMessage

    retriever, _, _ = setup()
    revoked = False
    original = retriever.search

    def search(*args, **kwargs):
        nonlocal revoked
        result = original(*args, **kwargs)
        revoked = True  # Permission changes after search has returned to the Agent.
        return result

    def authorize():
        if revoked:
            raise ValueError("task_deleted")

    retriever.search = search
    retriever.revalidate = authorize

    class Provider:
        calls = 0

        def decide(self, *args, **kwargs):
            self.calls += 1
            return AIMessage(content="", tool_calls=[{"id": "call", "name": "search_evidence",
                        "args": {"query": "什么时候能送到", "supplier": "vendor-a", "limit": 1}}],
                             usage_metadata={"input_tokens": 100, "output_tokens": 20, "total_tokens": 120})

    provider = Provider()
    result = AgentSession(retriever.repository, provider, retriever=retriever).ask("供应商 A 什么时候能送到？")
    assert result.status != "completed" and provider.calls == 1


def test_agent_checks_access_before_unknown_supplier_early_response():
    retriever, _, _ = setup()
    retriever.revalidate = lambda: (_ for _ in ()).throw(ValueError("task_deleted"))
    session = AgentSession(retriever.repository, object(), retriever=retriever)
    with pytest.raises(ValueError, match="task_deleted"):
        session.ask("供应商 Z 的内存是多少？")
