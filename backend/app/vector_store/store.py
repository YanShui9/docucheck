"""Explicit Milvus schema and typed snapshot filters; source text stays authoritative."""
import json
import re
from dataclasses import dataclass

from ..evidence.chunks import SourceChunk
from ..evidence.repository import digest
from .config import MilvusConfig
from .embedding import validate_vectors
from .probe import _sdk_client

MAX_CHUNK_FILTER_IDS = 100


class VectorUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class SearchScope:
    project_id: str
    case_id: str
    snapshot_id: str
    document_ids: tuple[str, ...]
    supplier: str | None = None
    generations: tuple[tuple[str, str], ...] = ()
    chunk_ids: tuple[str, ...] | None = None

    @classmethod
    def from_chunks(cls, chunks: tuple[SourceChunk, ...], supplier=None):
        if not chunks:
            raise ValueError("vector_empty_scope")
        first = chunks[0]
        scope = cls(first.project_id, first.case_id, first.snapshot_id,
                    tuple(sorted({c.document_id for c in chunks})), supplier,
                    tuple(sorted({(c.document_id, c.index_generation) for c in chunks}))
                    if any(c.index_generation for c in chunks) else ())
        if any((c.project_id, c.case_id, c.snapshot_id) !=
               (scope.project_id, scope.case_id, scope.snapshot_id) for c in chunks):
            raise ValueError("vector_mixed_scope")
        return scope

    def __post_init__(self):
        if self.chunk_ids is not None and (
                not isinstance(self.chunk_ids, tuple) or not 1 <= len(self.chunk_ids) <= MAX_CHUNK_FILTER_IDS
                or any(not isinstance(cid, str) or not re.fullmatch(r"[a-f0-9]{64}", cid) for cid in self.chunk_ids)
                or len(set(self.chunk_ids)) != len(self.chunk_ids)):
            raise ValueError("vector_chunk_scope")
        values = [self.project_id, self.case_id, self.snapshot_id, *self.document_ids]
        if self.supplier is not None:
            values.append(self.supplier)
        if (not 1 <= len(self.document_ids) <= 7
                or any(not isinstance(v, str) or not v or len(v.encode()) > 256 for v in values)):
            raise ValueError("vector_scope")
        if self.generations and ({d for d, _ in self.generations} != set(self.document_ids)
                or len(self.generations) != len(self.document_ids)
                or any(not re.fullmatch(r"[a-f0-9]{32}", g) for _, g in self.generations)):
            raise ValueError("vector_generation_scope")

    def allows(self, chunk: SourceChunk):
        return ((chunk.project_id, chunk.case_id, chunk.snapshot_id) ==
                (self.project_id, self.case_id, self.snapshot_id)
                and chunk.document_id in self.document_ids
                and (self.chunk_ids is None or chunk.chunk_id in self.chunk_ids)
                and (not self.generations or (chunk.document_id, chunk.index_generation) in self.generations)
                and (self.supplier is None or chunk.role == "requirement"
                     or chunk.supplier_id == self.supplier))

    def expression(self):
        # Values are typed, authorized IDs; JSON escaping handles quotes/backslashes.
        quote = lambda v: json.dumps(v, ensure_ascii=True)
        result = (f"project_id == {quote(self.project_id)} and case_id == {quote(self.case_id)} "
                  f"and snapshot_id == {quote(self.snapshot_id)} "
                  f"and document_id in {quote(list(self.document_ids))}")
        if self.supplier is not None:
            result += f' and (role == "requirement" or supplier_id == {quote(self.supplier)})'
        if self.generations:
            result += " and (" + " or ".join(
                f"(document_id == {quote(d)} and index_generation == {quote(g)})"
                for d, g in self.generations) + ")"
        if self.chunk_ids is not None:
            result += " and chunk_id in " + quote(list(self.chunk_ids))
        return result


class MilvusIndex:
    def __init__(self, config: MilvusConfig, revision: str, chunk_schema: str,
                 *, client=None, collection: str | None = None):
        if not revision or len(revision.encode()) > 256 or not chunk_schema:
            raise ValueError("vector_index_identity")
        self.config = config
        self.revision = revision
        self.chunk_schema = chunk_schema
        self.identity = {"embedding_revision": revision, "dimension": config.dimension,
                         "chunk_schema": chunk_schema, "store_schema": "original-chunks-v2"}
        self.collection = collection or "docucheck_rag_" + digest(self.identity)[:32]
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}", self.collection):
            raise ValueError("vector_collection_name")
        self._owns_client = client is None
        try:
            self.client = client or _sdk_client(config)
        except Exception:  # noqa: BLE001 -- External errors can include connection credentials.
            raise VectorUnavailable("vector_service_unavailable") from None

    def _call(self, method, **kwargs):
        try:
            return getattr(self.client, method)(collection_name=self.collection,
                                               timeout=self.config.timeout, **kwargs)
        except Exception:  # noqa: BLE001 -- Never echo SDK request parameters.
            raise VectorUnavailable("vector_service_unavailable") from None

    def ensure_collection(self):
        from pymilvus import DataType, MilvusClient

        if self._call("has_collection"):
            details = self._call("describe_collection")
            try:
                fields = {f["name"]: f for f in details["fields"]}
                matches = (json.loads(details["description"]) == self.identity
                           and fields["chunk_id"]["is_primary"]
                           and fields["chunk_id"]["type"] == DataType.VARCHAR
                           and int(fields["chunk_id"]["params"]["max_length"]) == 64
                           and fields["embedding"]["type"] == DataType.FLOAT_VECTOR
                           and int(fields["embedding"]["params"]["dim"]) == self.config.dimension
                           and all(fields[f]["type"] == DataType.VARCHAR
                                   and int(fields[f]["params"]["max_length"]) == 256
                                   for f in ("project_id", "case_id", "snapshot_id", "document_id",
                                             "supplier_id", "role", "index_generation"))
                           and fields["fingerprint"]["type"] == DataType.VARCHAR
                           and int(fields["fingerprint"]["params"]["max_length"]) == 64
                           and fields["provenance"]["type"] == DataType.JSON
                           and not details.get("enable_dynamic_field", False))
            except (ValueError, KeyError, TypeError):
                matches = False
            if not matches:
                raise ValueError("vector_collection_mismatch")
        else:
            schema = MilvusClient.create_schema(
                auto_id=False, enable_dynamic_field=False,
                description=json.dumps(self.identity, sort_keys=True),
            )
            schema.add_field("chunk_id", DataType.VARCHAR, max_length=64, is_primary=True)
            schema.add_field("embedding", DataType.FLOAT_VECTOR, dim=self.config.dimension)
            for field in ("project_id", "case_id", "snapshot_id", "document_id", "supplier_id", "role", "index_generation"):
                schema.add_field(field, DataType.VARCHAR, max_length=256)
            schema.add_field("fingerprint", DataType.VARCHAR, max_length=64)
            schema.add_field("provenance", DataType.JSON)
            indexes = MilvusClient.prepare_index_params()
            indexes.add_index("embedding", index_type="FLAT", metric_type="COSINE")
            for field in ("project_id", "case_id", "snapshot_id", "document_id", "supplier_id", "role", "index_generation"):
                indexes.add_index(field, index_type="INVERTED")
            self._call("create_collection", schema=schema, index_params=indexes, consistency_level="Strong")
        self._call("load_collection")

    def upsert(self, chunks, vectors):
        if not 1 <= len(chunks) <= 8 or len({c.chunk_id for c in chunks}) != len(chunks):
            raise ValueError("vector_write_batch")
        vectors = validate_vectors(vectors, count=len(chunks), dimension=self.config.dimension)
        rows = []
        for chunk, vector in zip(chunks, vectors, strict=True):
            if (chunk.embedding_revision != self.revision or chunk.dimension != self.config.dimension
                    or chunk.chunk_schema != self.chunk_schema
                    or not re.fullmatch(r"[a-f0-9]{64}", chunk.chunk_id)):
                raise ValueError("vector_chunk_identity")
            values = {field: getattr(chunk, field) for field in (
                "project_id", "case_id", "snapshot_id", "document_id", "supplier_id", "role", "index_generation")}
            if chunk.index_generation and not re.fullmatch(r"[a-f0-9]{32}", chunk.index_generation):
                raise ValueError("vector_generation_scope")
            if any(len(value.encode()) > 256 for value in values.values()):
                raise ValueError("vector_chunk_metadata")
            provenance = {"document_hash": chunk.document_hash, "parse_version": chunk.parse_version,
                          "binding_version": chunk.binding_version, "item_ids": list(chunk.item_ids),
                          "evidence_refs": list(chunk.evidence_refs), "chunk_schema": chunk.chunk_schema,
                          "embedding_revision": chunk.embedding_revision,
                          "spans": [{"evidence_id": s.evidence_id, "start": s.start, "end": s.end}
                                    for s in chunk.spans]}
            if len(json.dumps(provenance, ensure_ascii=False).encode()) > 60_000:
                raise ValueError("vector_chunk_metadata")
            rows.append(values | {"chunk_id": chunk.chunk_id, "embedding": vector,
                                  "fingerprint": chunk.fingerprint, "provenance": provenance})
        self._call("upsert", data=rows)

    def verify(self, chunks):
        scope = SearchScope.from_chunks(tuple(chunks))
        expected = {c.chunk_id: c.fingerprint for c in chunks}
        if len(expected) != len(chunks):
            raise ValueError("vector_duplicate_chunks")
        for offset in range(0, len(chunks), 100):
            ids = [c.chunk_id for c in chunks[offset:offset + 100]]
            rows = self._call("query", filter=scope.expression() +
                              " and chunk_id in " + json.dumps(ids),
                              output_fields=["chunk_id", "fingerprint"], limit=len(ids),
                              consistency_level="Strong")
            if {r["chunk_id"]: r["fingerprint"] for r in rows} != {i: expected[i] for i in ids}:
                return False
        return True

    def search(self, vector, scope: SearchScope, limit=20, *, include_scores=False):
        if type(limit) is not int or not 1 <= limit <= 20:
            raise ValueError("vector_search_limit")
        vectors = validate_vectors([vector], count=1, dimension=self.config.dimension)
        hits = self._call("search", data=vectors, anns_field="embedding", filter=scope.expression(),
                          search_params={"metric_type": "COSINE", "params": {}}, limit=limit,
                          output_fields=["chunk_id", "fingerprint"], consistency_level="Strong")
        if not isinstance(hits, list) or len(hits) != 1:
            raise ValueError("vector_response")
        try:
            return [{"chunk_id": h["chunk_id"], "fingerprint": h["entity"]["fingerprint"]}
                    | ({"distance": h["distance"]} if include_scores else {}) for h in hits[0]]
        except (KeyError, TypeError):
            raise ValueError("vector_response") from None

    def delete_scope(self, scope: SearchScope):
        self._call("delete", filter=scope.expression())

    def scope_exists(self, scope: SearchScope):
        if not self._call("has_collection"):
            return False
        return bool(self._call("query", filter=scope.expression(), output_fields=["chunk_id"],
                               limit=1, consistency_level="Strong"))

    def cleanup_scope(self, scope: SearchScope):
        if not scope.generations:
            raise ValueError("vector_generation_scope")
        if self._call("has_collection"):
            self.delete_scope(scope)
        return not self.scope_exists(scope)

    def close(self):
        if self._owns_client:
            try:
                self.client.close()
            except Exception:  # noqa: BLE001 -- Sanitize SDK shutdown errors.
                raise VectorUnavailable("vector_close_failed") from None
