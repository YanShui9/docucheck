"""Item attribution limits all retrieval channels without rewriting original chunks."""

from dataclasses import replace

import pytest

from backend.app.criteria.item_bindings import validate_bindings
from backend.app.evidence.hybrid import HybridRetriever, build_index
from backend.app.evidence.repository import EvidenceRepository, digest
from backend.app.evidence.retrieval import Retriever
from backend.app.vector_store.store import SearchScope
from backend.tests.test_hybrid_retrieval import Embedding, Store
from backend.tests.test_item_bindings import entry, sample


def fixture(*, pdf=False):
    definition, repo = sample()
    requests = [entry(repo)]
    if pdf:
        original = repo.documents[0]
        blocks = tuple(replace(b, sheet=None, cell=None, page_idx=n)
                       for n, b in enumerate(original.parsed.blocks))
        repo = EvidenceRepository("case", 1, [replace(original, parsed=replace(
            original.parsed, blocks=blocks, total_pages=5, processed_pages=tuple(range(5)),
            total_sheets=0, processed_sheets=()))])
        requests = [entry(repo) | {"selector": {"kind": "pdf", "start_page": 2, "end_page": 3}}]
    payload = {"schema_version": 1, "case_id": repo.case_id, "snapshot_id": repo.snapshot_id,
               "checklist_version_id": "confirmed", "checklist_digest": digest(definition.model_dump(mode="json")),
               "entries": validate_bindings(requests, definition, repo).payload()}
    frozen = {"version_id": "binding-a", "digest": digest(payload), "payload": payload}
    return definition, repo, frozen


def configured(*, pdf=False, hybrid=False):
    definition, repo, frozen = fixture(pdf=pdf)
    model, store = Embedding(), Store()
    if hybrid:
        receipt = build_index(repo, "project", model, store)
        retriever = HybridRetriever(repo, "project", model, store, receipt=receipt)
    else:
        retriever = Retriever(repo)
    retriever.set_item_scope(frozen, definition)
    return retriever, model, store


@pytest.mark.parametrize("hybrid", [False, True])
def test_same_file_other_product_and_header_never_enter_scoped_lexical_or_neighbors(hybrid):
    retriever, model, store = configured(hybrid=hybrid)
    hits = retriever.search("数量", item_id="rice", supplier="A")
    assert hits and all("20件" not in h["text"] and "苹果手机" not in h["text"] for h in hits)
    allowed = {b.evidence_id for b in retriever.repository.blocks[1:3]}
    assert all(set(h["evidence_refs"]) <= allowed for h in hits)
    source = retriever.read_source([retriever.repository.blocks[1].evidence_id], neighbors=1, item_id="rice")
    assert {r["evidence_id"] for r in source} <= allowed
    for block in (retriever.repository.blocks[0], retriever.repository.blocks[-1]):
        with pytest.raises(ValueError, match="item_binding_scope"):
            retriever.read_source([block.evidence_id], item_id="rice")
    if hybrid:
        assert retriever.last_diagnostics["degraded_reason"] == "vector_item_scope_unrepresentable"
        assert retriever.last_diagnostics["complete_coverage"] is False
        assert not store.scopes and len(model.calls) == 1  # Index build only, no query embedding.
    assert "20件" in str(retriever.search("数量"))  # Normal authorized browsing stays separate.


def test_dense_filter_uses_existing_chunk_ids_and_rejects_even_valid_foreign_fingerprint():
    retriever, _, store = configured(pdf=True, hybrid=True)
    before = tuple((c.chunk_id, c.fingerprint) for c in retriever.chunks)
    hits = retriever.search("什么时候到", item_id="rice", supplier="A")
    assert hits and all("20件" not in h["text"] for h in hits)
    scope = store.scopes[-1]
    assert scope.chunk_ids and "chunk_id in" in scope.expression()
    assert tuple((c.chunk_id, c.fingerprint) for c in retriever.chunks) == before
    foreign = next(c for c in retriever.chunks if "20件" in c.text)
    store.search = lambda *args, **kwargs: [{"chunk_id": foreign.chunk_id, "fingerprint": foreign.fingerprint}]
    with pytest.raises(ValueError, match="vector_hit_scope"):
        retriever.search("数量", item_id="rice")


def test_anchor_is_available_only_via_explicit_binding_detail_read():
    retriever, _, _ = configured()
    anchor = retriever.repository.blocks[0].evidence_id
    rows = retriever.read_item_binding("rice", evidence_ids=[anchor])
    assert rows[0]["text"] == "苹果手机" and rows[0]["purpose"] == "item_attribution_anchor"
    assert rows[0]["item_binding_version_id"] == "binding-a"
    with pytest.raises(ValueError, match="item_binding_scope"):
        retriever.read_item_binding("rice", evidence_ids=[retriever.repository.blocks[1].evidence_id])


@pytest.mark.parametrize("item_id", ["shirt", "missing"])
def test_unconfirmed_item_does_not_fall_back_to_whole_case(item_id):
    retriever, _, _ = configured(pdf=True, hybrid=True)
    if item_id == "missing":
        with pytest.raises(ValueError, match="item_binding_item"):
            retriever.search("数量", item_id=item_id)
    else:
        assert retriever.search("数量", item_id=item_id) == []
        assert retriever.last_diagnostics["degraded_reason"] == "item_binding_not_confirmed"


def test_chunk_id_filter_is_typed_bounded_and_checked_by_allows():
    _, repo, _ = fixture(pdf=True)
    model, store = Embedding(), Store()
    build_index(repo, "project", model, store)
    chunks = tuple(store.rows.values())
    scope = replace(SearchScope.from_chunks(chunks), chunk_ids=(chunks[1].chunk_id,))
    assert scope.allows(chunks[1]) and not scope.allows(chunks[-1])
    for ids in ((), ("injection\"",), tuple("a"*64 for _ in range(101))):
        with pytest.raises(ValueError, match="vector_chunk_scope"):
            replace(scope, chunk_ids=ids)


def test_assistant_scoped_search_and_followup_read_keep_same_item_boundary():
    from backend.tests.test_assistant_agent import Decisions, agent

    retriever, _, _ = configured()
    ref = retriever.repository.blocks[1].evidence_id
    provider = Decisions(("search_evidence", {"query": "数量", "item_id": "rice"}),
                         ("read_source", {"evidence_ids": [ref], "item_id": "rice"}),
                         ("answer_evidence", {"answer": '数量10台', "citations": [{"evidence_id": ref, "quote": "数量10台"}]}))
    result = agent(retriever.repository, provider, retriever=retriever).ask("苹果手机数量是多少？")
    assert result["status"] == "completed", result.get("error")
    assert result["payload"]["retrieval"]["item_id"] == "rice"
    assert result["payload"]["retrieval"]["item_binding_version_id"] == "binding-a"
    assert "20件" not in str(result["payload"]["source_reads"])


def test_assistant_cannot_widen_scoped_search_through_an_unscoped_followup():
    from backend.tests.test_assistant_agent import Decisions, agent

    retriever, _, _ = configured()
    foreign = retriever.repository.blocks[-1].evidence_id
    provider = Decisions(("search_evidence", {"query": "数量", "item_id": "rice"}),
                         ("search_evidence", {"query": "数量20件"}),
                         ("answer_evidence", {"answer": '数量20件', "citations": [{"evidence_id": foreign, "quote": "数量20件"}]}))
    result = agent(retriever.repository, provider, retriever=retriever).ask("苹果手机数量是多少？")
    assert result["status"] != "completed"
    assert not result["payload"]["citations"]


def many_blocks(count):
    definition, repo, _ = fixture(pdf=True)
    doc = repo.documents[0]
    originals = doc.parsed.blocks
    numbers = tuple(replace(originals[1], evidence_id=f"qty-{n}", text=f"数量{n + 10}台") for n in range(count))
    repo = EvidenceRepository("case", 1, [replace(doc, parsed=replace(doc.parsed,
        blocks=(originals[0], *numbers, *originals[2:])) )])
    request = entry(repo) | {"selector": {"kind": "pdf", "start_page": 2, "end_page": 3}}
    payload = {"schema_version": 1, "case_id": "case", "snapshot_id": repo.snapshot_id,
        "checklist_version_id": "confirmed", "checklist_digest": digest(definition.model_dump(mode="json")),
        "entries": validate_bindings([request], definition, repo).payload()}
    return definition, repo, {"version_id": "many", "digest": digest(payload), "payload": payload}


def test_dense_batches_are_bounded_and_merged_by_cosine_score():
    definition, repo, frozen = many_blocks(121)

    class ScoredStore(Store):
        def search(self, vector, scope, limit, *, include_scores=False):
            self.scopes.append(scope)
            chunks = [c for c in self.rows.values() if scope.allows(c)]
            return [{"chunk_id": c.chunk_id, "fingerprint": c.fingerprint, "distance": 1.0 if "数量130台" == c.text else 0.0}
                    for c in sorted(chunks, key=lambda c: (c.text != "数量130台", c.chunk_id))[:limit]]

    model, store = Embedding(), ScoredStore()
    receipt = build_index(repo, "project", model, store)
    retriever = HybridRetriever(repo, "project", model, store, receipt=receipt)
    retriever.set_item_scope(frozen, definition)
    hits = retriever.search("什么时候到", item_id="rice")
    assert hits[0]["text"] == "数量130台" and len(store.scopes) == 2
    assert all(len(s.chunk_ids) <= 100 for s in store.scopes)
    assert retriever.last_diagnostics["retrieval_mode"] == "hybrid"


def test_over_budget_and_vector_failure_stay_within_lexical_item_range():
    definition, repo, frozen = many_blocks(401)
    model, store = Embedding(), Store()
    receipt = build_index(repo, "project", model, store)
    retriever = HybridRetriever(repo, "project", model, store, receipt=receipt)
    retriever.set_item_scope(frozen, definition)
    hits = retriever.search("数量", item_id="rice")
    assert hits and not store.scopes
    assert retriever.last_diagnostics["degraded_reason"] == "vector_item_scope_budget"
    assert all("20件" not in h["text"] for h in hits)
    smaller, _, failed = configured(pdf=True, hybrid=True)
    failed.fail = True
    assert smaller.search("数量", item_id="rice")
    assert smaller.last_diagnostics["degraded_reason"] == "vector_service_unavailable"
    assert smaller.last_diagnostics["complete_coverage"] is False


def test_unscoped_lexical_browsing_does_not_inherit_item_diagnostics():
    retriever, _, _ = configured()
    retriever.search("数量", item_id="rice")
    retriever.search("数量20件")
    assert "item_id" not in retriever.last_diagnostics


def test_persisted_answer_replay_cannot_claim_item_scope_for_foreign_source():
    from backend.app.agent.assistant_evidence import verify_windows

    definition, repo, frozen = fixture()
    foreign = repo.blocks[-1]
    payload = {"citation_offsets_version": 1, "source_reads": {foreign.evidence_id: foreign.text},
        "source_windows": [{"evidence_id": foreign.evidence_id, "start_char": 0, "text": foreign.text}],
        "citations": [], "retrieval": {"item_id": "rice", "item_binding_version_id": frozen["version_id"],
        "item_binding_digest": frozen["digest"]}}
    with pytest.raises(ValueError, match="assistant_source_changed"):
        verify_windows(repo, payload, item_bindings=frozen, definition=definition)


@pytest.mark.parametrize("reason,expected", [
    ("item_binding_not_confirmed", "确认资料与品目的对应"),
    ("vector_item_scope_unrepresentable", "向量块包含范围外原文"),
    ("vector_item_scope_budget", "范围过滤超过本次预算"),
])
def test_item_scope_failure_explains_actual_reason_without_claiming_index_is_down(reason, expected):
    from backend.app.agent.assistant import render_evidence

    text = render_evidence([], {"item_id": "rice", "degraded_reason": reason})
    assert expected in text and "语义索引尚不可用" not in text
