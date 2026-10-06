# ruff: noqa: F811 -- Imported pytest fixture is resolved by parameter name.
from sqlalchemy import select

from backend.app.db.index_repository import IndexRepository
from backend.app.db.index_worker import process_index
from backend.app.db.ingestion import DocumentIngestion
from backend.app.db.models import evidence_blocks
from backend.app.db.persisted_retrieval import persisted_retriever
from backend.app.db.run_executor import execute_run
from backend.tests.test_hybrid_retrieval import Embedding, Store
from backend.tests.test_index_repository import TARGET
from backend.tests.test_persisted_agent_run import (  # noqa: F401
    SourceProvider,
    _submit_question,
    uploaded_case,
)


def test_uploaded_document_indexes_and_agent_uses_current_sql_bound_dense_hits(uploaded_case):
    client, engine, storage, case_id, uploaded = uploaded_case
    DocumentIngestion(engine, storage).process_document(case_id, uploaded["snapshot_id"], uploaded["document_id"])
    with engine.connect() as conn:
        source_id = conn.scalar(select(evidence_blocks.c.id).where(
            evidence_blocks.c.document_version_id == uploaded["document_id"], evidence_blocks.c.cell == "B6"))
    model, store = Embedding(), Store()
    model.revision = TARGET.revision
    indexes = IndexRepository(engine)
    assert process_index(indexes, indexes.claim(TARGET), embedding_factory=lambda t: model,
                         store_factory=lambda t: store) == "ready"
    submitted, _ = _submit_question(client, case_id)
    def retrieval(engine, repo, run):
        return persisted_retriever(engine, repo, run, target=TARGET, embedding_factory=lambda t: model,
                                   store_factory=lambda t: store)
    report = execute_run(engine, submitted["run_id"], submitted["attempt_id"], SourceProvider(source_id),
                         retriever_factory=retrieval)
    assert report.status == "completed" and report.model_calls == 3
    assert "16 GB" in report.answer_text and "B6" in report.answer_text
    assert store.scopes and all(s.snapshot_id == uploaded["snapshot_id"] and s.generations for s in store.scopes)
    assert client.get(f"/api/v1/agent-runs/{report.run_id}").json()["answer_text"] == report.answer_text
