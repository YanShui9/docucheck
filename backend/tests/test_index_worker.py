# ruff: noqa: F811 -- Imported pytest fixture is resolved by parameter name.
from sqlalchemy import insert, select, update

from backend.app.db.index_repository import IndexRepository
from backend.app.db.index_worker import process_cleanup, process_index
from backend.app.db.models import (
    document_index_jobs,
    document_versions,
    evidence_blocks,
    index_manifests,
)
from backend.tests.test_hybrid_retrieval import Embedding, Store
from backend.tests.test_index_repository import (  # noqa: F401
    NOW,
    TARGET,
    indexed_db,
    queue,
)


def prepare(engine):
    with engine.begin() as conn:
        conn.execute(update(document_versions).values(coverage_json={"parser_version_full": "parser"}))
        conn.execute(insert(evidence_blocks).values(id="e" * 64, document_version_id="doc", ordinal=0,
                     source_text="订单确认后十四天内交付", page_idx=0, created_at=NOW))
    queue(engine)


def test_worker_verifies_and_persists_ready_without_repeat_calls(indexed_db):
    engine, _ = indexed_db
    prepare(engine)
    service = IndexRepository(engine)
    claim = service.claim(TARGET)
    model, store = Embedding(), Store()
    model.revision = TARGET.revision
    result = process_index(service, claim, embedding_factory=lambda t: model, store_factory=lambda t: store,
                           heartbeat_seconds=100)
    assert result == "ready" and len(model.calls) == 1
    assert all(c.index_generation == claim.generation for c in store.rows.values())
    with engine.connect() as conn:
        manifest = conn.execute(select(index_manifests)).mappings().one()
    assert manifest["fingerprint"] and manifest["ingested_count"] == 1
    assert service.claim(TARGET) is None


def test_delete_during_embedding_cannot_publish_and_cleanup_is_generation_scoped(indexed_db):
    from backend.app.db.case_deletion import CaseDeletion
    engine, _ = indexed_db
    prepare(engine)
    service = IndexRepository(engine)
    claim = service.claim(TARGET)
    model, store = Embedding(), Store()
    model.revision = TARGET.revision
    original = model.embed
    def embed(texts):
        values = original(texts)
        CaseDeletion(engine).request("case", 1)
        return values
    model.embed = embed
    assert process_index(service, claim, embedding_factory=lambda t: model, store_factory=lambda t: store) == "stale"
    assert not store.rows
    scopes = []
    store.cleanup_scope = lambda scope: scopes.append(scope) or True
    cleanup = service.claim_cleanup()
    assert process_cleanup(service, cleanup, store_factory=lambda t: store) == "deleted"
    assert scopes[0].generations == (("doc", claim.generation),)


def test_service_failure_sanitizes_credentials_and_retries(indexed_db):
    engine, _ = indexed_db
    prepare(engine)
    service = IndexRepository(engine)
    claim = service.claim(TARGET)
    def broken(target):
        raise RuntimeError("password=private")
    assert process_index(service, claim, embedding_factory=broken) == "pending"
    with engine.connect() as conn:
        row = conn.execute(select(document_index_jobs)).mappings().one()
    assert row["error_code"] == "index_processing_failed" and "private" not in str(row)


def test_worker_preview_never_contacts_services(monkeypatch, capsys):
    from backend.app.index_worker import main
    monkeypatch.setenv("DOCUCHECK_VECTOR_RETRIEVAL_ENABLED", "0")
    assert main([]) == 0
    output = capsys.readouterr().out
    assert '"mode": "preview"' in output and '"embedding_requests": 0' in output
    assert main(["--once"]) == 2
