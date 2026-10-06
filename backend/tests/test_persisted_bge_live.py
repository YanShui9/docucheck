"""Explicit local BGE/Milvus check through SQL index manifests and Run scope."""

import os
from pathlib import Path
from uuid import uuid4

import pytest
from dotenv import load_dotenv

from backend.app.db.agent_snapshot import load_agent_snapshot
from backend.app.db.index_repository import IndexRepository
from backend.app.db.index_worker import process_index
from backend.app.db.ingestion import DocumentIngestion
from backend.app.db.persisted_retrieval import persisted_retriever
from backend.app.db.run_repository import RunRepository
from backend.app.runtime.lifecycle import RunStatus
from backend.app.vector_store.config import MilvusConfig
from backend.app.vector_store.embedding import OllamaConfig, OllamaEmbedding
from backend.app.vector_store.store import MilvusIndex
from backend.app.vector_store.target import IndexTarget
from backend.tests import test_document_ingestion
from backend.tests.test_dynamic_workflow import workbook

ingestion_case = test_document_ingestion.ingestion_case

pytestmark = pytest.mark.skipif(os.getenv("DOCUCHECK_RUN_BACKEND_RAG_LIVE") != "1",
                                reason="explicit local BGE/Milvus persisted-retrieval opt-in")


def test_real_bge_query_uses_ready_sql_manifest_and_original_source(ingestion_case):
    client, engine, storage, case_id = ingestion_case
    load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=False)
    config = MilvusConfig.from_env()
    assert config.uri == "http://127.0.0.1:19530" and config.database == "docucheck_probe"
    # Keep the secret outside an assertion expression: pytest's assertion
    # rewriting can otherwise print the full credential on a failed check.
    scoped_account = config.token.get_secret_value().startswith("docucheck_probe:")
    assert scoped_account, "dedicated_milvus_account_required"
    collection = "docucheck_backend_accept_" + uuid4().hex

    class ProbeTarget(IndexTarget):
        @property
        def collection(self):
            return collection

    model = index = retriever = None
    owned_collection = False
    try:
        model = OllamaEmbedding(OllamaConfig.from_env())
        model.resolve()
        target = ProbeTarget(model.revision, model.dimension)

        def embedding_factory(_):
            embedding = OllamaEmbedding(OllamaConfig.from_env())
            try:
                embedding.resolve()
            except BaseException:
                embedding.close()
                raise
            return embedding

        def store_factory(_):
            return MilvusIndex(config, target.revision, target.policy.revision, collection=collection)

        index = MilvusIndex(config, target.revision, target.policy.revision, collection=collection)
        assert not index._call("has_collection")
        # This uniquely named collection belongs only to this explicit test.
        # Mark ownership before creation: loading can fail after create succeeds.
        owned_collection = True
        index.ensure_collection()
        response = client.post(f"/api/v1/cases/{case_id}/documents",
                               headers={"Idempotency-Key": "live-index-upload"},
                               data={"role": "spec", "supplier_handle": "vendor-a",
                                     "supplier_name": "甲供应商", "model_name": "电脑",
                                     "expected_revision": "0"},
                               files={"file": ("合成电脑规格.xlsx", workbook("电脑 标称 SSD 1 TB"),
                                               "application/octet-stream")})
        assert response.status_code == 201, response.text
        uploaded = response.json()
        assert DocumentIngestion(engine, storage).process_document(
            case_id, uploaded["snapshot_id"], uploaded["document_id"])["parse_status"] == "complete"
        indexes = IndexRepository(engine)
        assert indexes.schedule(case_id, uploaded["snapshot_id"], 1, target=target) == 1
        claim = indexes.claim(target)
        assert claim.case_id == case_id
        assert process_index(indexes, claim, embedding_factory=embedding_factory,
                             store_factory=store_factory) == "ready"
        assert indexes.status(case_id, uploaded["snapshot_id"])["documents"][0]["status"] == "ready"
        conversation = client.post(f"/api/v1/cases/{case_id}/conversations").json()["id"]
        response = client.post(f"/api/v1/conversations/{conversation}/messages",
                               headers={"Idempotency-Key": "live-index-question"},
                               json={"content": "存照片和文件的硬盘有多大？", "expected_revision": 1})
        assert response.status_code == 202, response.text
        receipt = response.json()
        runs = RunRepository(engine)
        current = runs.claim(receipt["run_id"], receipt["attempt_id"])
        repo = load_agent_snapshot(engine, case_id, uploaded["snapshot_id"])
        # Build fresh clients from committed manifests, as a later Run does.
        retriever = persisted_retriever(
            engine, repo, current, target=target,
            embedding_factory=embedding_factory, store_factory=store_factory)
        hits = retriever.search("存照片和文件的硬盘有多大？", supplier="vendor-a", limit=6)
        assert retriever.last_diagnostics["retrieval_mode"] == "hybrid"
        assert retriever.last_diagnostics["degraded_reason"] is None
        assert retriever.last_diagnostics["complete_coverage"] is False
        assert hits and "1 TB" in hits[0]["text"]
        sources = retriever.repository.read(hits[0]["evidence_refs"])
        assert all(b.role == "spec" and b.supplier == "vendor-a" and b.cell == "A1" for b in sources)
        assert all(b.document_id == uploaded["document_id"] for b in sources)
        # No-answer questions may still have near neighbours; do not claim absence.
        retriever.search("这台电脑能防水吗？", supplier="vendor-a", limit=6)
        assert retriever.last_diagnostics["complete_coverage"] is False
        runs.finish(current.run_id, current.active_attempt_id, current.fence,
                    current.snapshot_id, RunStatus.COMPLETED,
                    answer_text="仅完成合成资料的持久检索验收，未生成采购结论。")
    finally:
        if retriever:
            retriever.close()
        if index:
            try:
                if owned_collection and index._call("has_collection"):
                    index._call("drop_collection")
                if owned_collection:
                    assert not index._call("has_collection")
            finally:
                index.close()
        if model:
            model.close()
