# ruff: noqa: F811 -- Imported pytest fixture is resolved by parameter name.
import pytest
from sqlalchemy import delete, insert, update

from backend.app.db.agent_snapshot import load_agent_snapshot
from backend.app.db.index_repository import IndexRepository
from backend.app.db.index_worker import process_index
from backend.app.db.models import (
    cases,
    conversations,
    document_versions,
    evidence_blocks,
    project_members,
)
from backend.app.db.persisted_retrieval import persisted_retriever
from backend.app.db.run_repository import RunRepository, SubmitRun
from backend.tests.test_hybrid_retrieval import Embedding, Store
from backend.tests.test_index_repository import NOW, TARGET, indexed_db  # noqa: F401
from backend.tests.test_index_worker import prepare


def active_run(engine, actor=None):
    with engine.begin() as conn:
        conn.execute(insert(conversations).values(id="conv", case_id="case", created_at=NOW))
    runs = RunRepository(engine)
    receipt = runs.submit(SubmitRun("case", "conv", "snap", 1, "什么时候交付", "standard", "key", actor))
    return runs.claim(receipt.run_id, receipt.attempt_id)


def test_pending_index_degrades_without_model_factory(indexed_db):
    engine, _ = indexed_db
    prepare(engine)
    claim = active_run(engine)
    def forbidden(target):
        raise AssertionError("must not contact embedding")
    retriever = persisted_retriever(engine, load_agent_snapshot(engine, "case", "snap"), claim,
                                   target=TARGET, embedding_factory=forbidden)
    hits = retriever.search("交付")
    assert hits and retriever.last_diagnostics["degraded_reason"] == "vector_index_not_ready"
    assert retriever.repository.read(["e" * 64])[0].text == "订单确认后十四天内交付"


def test_sql_ready_is_reusable_and_new_generation_fences_existing_retriever(indexed_db):
    engine, _ = indexed_db
    prepare(engine)
    indexes = IndexRepository(engine)
    model, store = Embedding(), Store()
    model.revision = TARGET.revision
    assert process_index(indexes, indexes.claim(TARGET), embedding_factory=lambda t: model,
                         store_factory=lambda t: store) == "ready"
    run = active_run(engine)
    retriever = persisted_retriever(engine, load_agent_snapshot(engine, "case", "snap"), run,
                                    target=TARGET, embedding_factory=lambda t: model, store_factory=lambda t: store)
    hits = retriever.search("何时到货")
    assert hits and retriever.last_diagnostics["retrieval_mode"] == "hybrid"
    assert store.scopes[-1].generations
    indexes.schedule("case", "snap", 1, target=type(TARGET)(TARGET.revision.replace("a" * 64, "b" * 64), 3))
    with pytest.raises(ValueError, match="source_access_changed"):
        retriever.search("交付")


def test_membership_revocation_and_sql_source_tamper_block_disclosure(indexed_db):
    from backend.app.api.auth import Principal
    from backend.app.db.workspace import WorkspaceRepository
    engine, _ = indexed_db
    prepare(engine)
    actor = Principal("local", "alice")
    project = WorkspaceRepository(engine).create_project(actor, "采购")
    with engine.begin() as conn:
        conn.execute(update(cases).values(project_id=project["id"]))
    run = active_run(engine, actor.key)
    retriever = persisted_retriever(engine, load_agent_snapshot(engine, "case", "snap"), run, target=None)
    with engine.begin() as conn:
        conn.execute(update(evidence_blocks).values(source_text="篡改资料"))
    with pytest.raises(ValueError, match="source_access_changed"):
        retriever.search("交付")
    with engine.begin() as conn:
        conn.execute(delete(project_members))
    with pytest.raises(ValueError, match="source_access_changed"):
        retriever.repository.read(["e" * 64])
    from backend.app.db.run_repository import RepositoryError
    from backend.app.runtime.lifecycle import RunStatus
    with pytest.raises(RepositoryError, match="source_access_changed"):
        RunRepository(engine).finish(run.run_id, run.active_attempt_id, run.fence, "snap",
                                     RunStatus.COMPLETED, answer_text="should not persist")
    assert RunRepository(engine).get(run.run_id).status == RunStatus.RUNNING


def test_document_binding_change_blocks_old_original_read(indexed_db):
    engine, _ = indexed_db
    prepare(engine)
    run = active_run(engine)
    retriever = persisted_retriever(engine, load_agent_snapshot(engine, "case", "snap"), run, target=None)
    with engine.begin() as conn:
        conn.execute(update(document_versions).values(model_name="changed"))
    with pytest.raises(ValueError, match="source_access_changed"):
        retriever.repository.read(["e" * 64])


@pytest.mark.parametrize("failed_dependency", ["embedding", "milvus"])
def test_ready_index_dependency_outage_degrades_and_keeps_source_guard(indexed_db, failed_dependency):
    """A ready SQL manifest must not turn a dependency outage into vector success."""
    from backend.app.db.persisted_retrieval import ProtectedLexical

    engine, _ = indexed_db
    prepare(engine)
    indexes = IndexRepository(engine)
    model, store = Embedding(), Store()
    model.revision = TARGET.revision
    assert process_index(indexes, indexes.claim(TARGET), embedding_factory=lambda _: model,
                         store_factory=lambda _: store) == "ready"
    run = active_run(engine)
    closed, factories = [], []
    model.close = lambda: closed.append("embedding")

    def embedding_factory(_):
        factories.append("embedding")
        if failed_dependency == "embedding":
            raise RuntimeError("private SDK connection details")
        return model

    def store_factory(_):
        factories.append("milvus")
        raise RuntimeError("private SDK connection details")

    retriever = persisted_retriever(
        engine, load_agent_snapshot(engine, "case", "snap"), run, target=TARGET,
        embedding_factory=embedding_factory, store_factory=store_factory)
    assert isinstance(retriever, ProtectedLexical)
    assert retriever.search("交付")
    assert retriever.last_diagnostics == {
        "retrieval_mode": "bm25", "degraded_reason": "vector_service_unavailable",
        "complete_coverage": False}
    assert factories == (["embedding"] if failed_dependency == "embedding" else ["embedding", "milvus"])
    assert closed == ([] if failed_dependency == "embedding" else ["embedding"])
    # Degradation still uses SQL-protected originals, including later tampering.
    with engine.begin() as conn:
        conn.execute(update(evidence_blocks).values(source_text="篡改资料"))
    with pytest.raises(ValueError, match="source_access_changed"):
        retriever.search("交付")
    retriever.close()
