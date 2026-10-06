"""Real Milvus adapter acceptance with synthetic vectors, no model API."""
import logging
import os
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import pytest
from dotenv import dotenv_values

from backend.app.evidence.chunks import ChunkPolicy, chunk_snapshot
from backend.app.evidence.repository import load_case
from backend.app.vector_store.config import MilvusConfig
from backend.app.vector_store.probe import _sdk_client
from backend.app.vector_store.store import MilvusIndex, SearchScope

pytestmark = pytest.mark.skipif(os.getenv("DOCUCHECK_RUN_MILVUS_TESTS") != "1",
                                reason="explicit local Milvus opt-in required")


def test_explicit_schema_idempotent_upsert_strong_scope_and_delete():
    env = dotenv_values(Path(__file__).resolve().parents[2] / ".env", interpolate=False)
    config = MilvusConfig.from_env({k: v for k, v in env.items() if v is not None})
    assert config.uri == "http://127.0.0.1:19530" and config.database == "docucheck_probe"
    assert config.token.get_secret_value().startswith("docucheck_probe:")
    previous = logging.root.manager.disable
    logging.disable(logging.CRITICAL)
    client = None
    created = False
    collection = "docucheck_rag_test_" + uuid4().hex
    try:
        client = _sdk_client(config)
        repo = load_case(Path(__file__).resolve().parents[2] / "examples/dev-pack-001")
        chunks = chunk_snapshot(repo, "test-project", "synthetic-revision",
                                generations={d.document_id: "a" * 32 for d in repo.documents})
        index = MilvusIndex(config, "synthetic-revision", ChunkPolicy().revision,
                            client=client, collection=collection)
        assert not client.has_collection(collection, timeout=config.timeout)
        index.ensure_collection()
        created = True
        index.ensure_collection()  # Existing schema must match, not blindly reused.
        vector = [1.0] + [0.0] * 1023
        for offset in range(0, len(chunks), 8):
            batch = chunks[offset:offset + 8]
            index.upsert(batch, [vector] * len(batch))
            index.upsert(batch, [vector] * len(batch))
        # Deliberately closer/equal out-of-scope vectors must never enter search results.
        foreign = tuple(replace(chunks[0], chunk_id=uuid4().hex * 2, **scope)
                        for scope in ({"project_id": "other-project"}, {"case_id": "other-case"},
                                      {"snapshot_id": "old-snapshot"},
                                      {"supplier_id": "vendor-b", "role": "spec"},
                                      {"index_generation": "b" * 32}))
        index.upsert(foreign, [vector] * len(foreign))
        assert index.verify(chunks)
        scope = SearchScope.from_chunks(chunks, supplier="vendor-a")
        hits = index.search(vector, scope, limit=20)
        allowed = {c.chunk_id for c in chunks if scope.allows(c)}
        assert hits and {h["chunk_id"] for h in hits} <= allowed
        assert not {h["chunk_id"] for h in hits}.intersection(c.chunk_id for c in foreign)
        index.delete_scope(scope)
        assert index.search(vector, scope) == []
        stale_scope = SearchScope("test-project", repo.case_id, repo.snapshot_id,
                                  (chunks[0].document_id,), generations=((chunks[0].document_id, "b" * 32),))
        assert index.scope_exists(stale_scope)
        assert index.cleanup_scope(stale_scope)
        assert not index.scope_exists(stale_scope)
    finally:
        try:
            if client is not None:
                try:
                    # UUID collection belongs to this test only; also clean partial creation.
                    if created or client.has_collection(collection, timeout=config.timeout):
                        client.drop_collection(collection, timeout=config.timeout)
                finally:
                    client.close()
        finally:
            logging.disable(previous)
