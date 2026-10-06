from dataclasses import replace
from pathlib import Path

import pytest
from pydantic import SecretStr

from backend.app.evidence.chunks import ChunkPolicy, chunk_snapshot
from backend.app.evidence.repository import load_case
from backend.app.vector_store.config import MilvusConfig
from backend.app.vector_store.store import MilvusIndex, SearchScope, VectorUnavailable


class SchemaClient:
    def __init__(self):
        self.schema = None
        self.calls = []

    def has_collection(self, **kwargs):
        return self.schema is not None

    def create_collection(self, **kwargs):
        self.schema = kwargs["schema"].to_dict()
        self.calls.append(kwargs)

    def describe_collection(self, **kwargs):
        return self.schema

    def load_collection(self, **kwargs):
        pass

    def upsert(self, **kwargs):
        self.calls.append(kwargs)


def index(client=None):
    config = MilvusConfig("http://127.0.0.1:19530", "test", 3, 5, SecretStr("test:secret"))
    return MilvusIndex(config, "revision", ChunkPolicy().revision, client=client or SchemaClient())


def test_schema_persists_identity_and_reopening_checks_it():
    client = SchemaClient()
    store = index(client)
    store.ensure_collection()
    store.ensure_collection()
    assert len(client.calls) == 1
    client.schema["description"] = "{}"
    with pytest.raises(ValueError, match="vector_collection_mismatch"):
        store.ensure_collection()


@pytest.mark.parametrize("field", ["document_id", "supplier_id", "fingerprint", "provenance"])
def test_existing_schema_cannot_reuse_wrong_scope_or_provenance_types(field):
    client = SchemaClient()
    store = index(client)
    store.ensure_collection()
    next(f for f in client.schema["fields"] if f["name"] == field)["type"] = -1
    with pytest.raises(ValueError, match="vector_collection_mismatch"):
        store.ensure_collection()


def test_write_checks_revision_and_never_stores_untrusted_source_text():
    client = SchemaClient()
    store = index(client)
    repo = load_case(Path(__file__).resolve().parents[2] / "examples/dev-pack-001")
    chunks = chunk_snapshot(repo, "project", "revision", dimension=3)[:1]
    store.upsert(chunks, [[1, 0, 0]])
    row = client.calls[-1]["data"][0]
    assert "text" not in row and "text" not in row["provenance"]
    assert row["provenance"]["evidence_refs"] == list(chunks[0].evidence_refs)
    with pytest.raises(ValueError, match="vector_chunk_identity"):
        store.upsert([replace(chunks[0], embedding_revision="other")], [[1, 0, 0]])


def test_sdk_error_sanitizes_internal_values():
    client = SchemaClient()
    client.upsert = lambda **kwargs: (_ for _ in ()).throw(RuntimeError("secret procurement"))
    store = index(client)
    with pytest.raises(VectorUnavailable) as exc:
        store._call("upsert", data=[])
    assert str(exc.value) == "vector_service_unavailable"


def test_search_uses_explicit_varchar_primary_key_name_in_sdk_response():
    client = SchemaClient()
    client.search = lambda **kwargs: [[{"chunk_id": "a" * 64, "distance": 1.0,
                                      "entity": {"chunk_id": "a" * 64, "fingerprint": "b" * 64}}]]
    result = index(client).search([1, 0, 0], SearchScope("project", "case", "snap", ("doc",)))
    assert result == [{"chunk_id": "a" * 64, "fingerprint": "b" * 64}]
