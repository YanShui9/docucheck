"""Bind production retrieval to SQL readiness, current attempt and live membership."""
from collections.abc import Callable
from typing import Any

from sqlalchemy import Engine, select

from ..evidence.chunks import chunk_snapshot
from ..evidence.hybrid import HybridRetriever, _receipt
from ..evidence.repository import EvidenceRepository
from ..evidence.retrieval import Retriever
from ..runtime.lifecycle import RunState
from ..vector_store.target import IndexTarget, configured_target
from .index_worker import _close
from .index_worker import embedding_factory as default_embedding
from .index_worker import store_factory as default_store
from .models import cases, document_versions, suppliers
from .models import document_index_jobs as jobs
from .models import index_manifests as manifests
from .run_repository import RunRepository
from .source_repository import protect_sources

_CONFIGURED = object()


def _scope_binding(connection, repo, run_id):
    from .checklist_repository import _version
    from .item_binding_repository import load_run_bindings
    from .models import assistant_runs, dynamic_runs

    binding = connection.execute(select(assistant_runs).where(assistant_runs.c.run_id == run_id)).mappings().first()
    if binding is None:
        binding = connection.execute(select(dynamic_runs).where(dynamic_runs.c.run_id == run_id)).mappings().first()
    definition = frozen = None
    if binding is not None:
        if binding["case_id"] != repo.case_id or binding["snapshot_id"] != repo.snapshot_id:
            raise ValueError("item_binding_source_changed")
        if binding["checklist_version_id"]:
            version = _version(connection, repo.case_id, binding["checklist_version_id"])
            if version["snapshot_id"] == repo.snapshot_id:
                definition = version["definition"]
        frozen = load_run_bindings(connection, binding, definition)
    return frozen, definition


class ProtectedLexical(Retriever):
    def __init__(self, repository: EvidenceRepository, validate: Callable[[], None], reason: str):
        super().__init__(repository)
        self.revalidate = validate
        self.reason = reason
        self.last_diagnostics = {"retrieval_mode": "bm25", "degraded_reason": reason, "complete_coverage": False}

    def search(self, *args, **kwargs) -> list[dict]:
        self.revalidate()
        hits = super().search(*args, **kwargs)
        self.last_diagnostics.update(retrieval_mode="bm25", complete_coverage=False)
        if self.last_diagnostics.get("degraded_reason") != "item_binding_not_confirmed":
            self.last_diagnostics["degraded_reason"] = self.reason
        self.repository.read(list(dict.fromkeys(ref for hit in hits for ref in hit["evidence_refs"])))
        self.revalidate()
        return hits

    def read_source(self, *args, **kwargs) -> list[dict]:
        self.revalidate()
        result = super().read_source(*args, **kwargs)
        self.repository.read([row["evidence_id"] for row in result])
        self.revalidate()
        return result

    def close(self) -> None:
        pass


class ManagedHybrid(HybridRetriever):
    def read_source(self, *args, **kwargs) -> list[dict]:
        result = super().read_source(*args, **kwargs)
        self.repository.read([row["evidence_id"] for row in result])
        self.revalidate()
        return result

    def close(self) -> None:
        _close(self.embedding)
        _close(self.store)


def persisted_retriever(engine: Engine, repo: EvidenceRepository, run: RunState, *,
                        target: IndexTarget | None | object = _CONFIGURED,
                        embedding_factory: Callable[[IndexTarget], Any] = default_embedding,
                        store_factory: Callable[[IndexTarget], Any] = default_store) -> ProtectedLexical | ManagedHybrid:
    runs = RunRepository(engine)
    doc_ids = {d.document_id for d in repo.documents}
    with engine.connect() as connection:
        project_id = connection.scalar(select(cases.c.project_id).where(cases.c.id == repo.case_id))
    project_key = project_id or "local:" + repo.case_id
    frozen_signature = None
    def access():
        if not runs.has_source_access(run.run_id, run.active_attempt_id, run.fence):
            raise ValueError("source_access_changed")
        with engine.connect() as connection:
            if frozen_signature is not None and _scope_binding(connection, repo, run.run_id) != frozen_signature:
                raise ValueError("item_binding_source_changed")
            docs = connection.execute(select(document_versions, suppliers.c.handle.label("supplier_handle"))
                .outerjoin(suppliers, document_versions.c.supplier_id == suppliers.c.id).where(document_versions.c.case_id == repo.case_id,
                document_versions.c.snapshot_id == repo.snapshot_id)).mappings().all()
            if connection.scalar(select(cases.c.project_id).where(cases.c.id == repo.case_id)) != project_id:
                raise ValueError("source_access_changed")
        if {d["id"] for d in docs} != doc_ids:
            raise ValueError("source_access_changed")
        by_id = {d.document_id: d for d in repo.documents}
        for row in docs:
            original = by_id[row["id"]]
            if (row["source_sha256"] != original.source_hash or row["role"] != original.role
                    or row["model_name"] != original.model or row["parse_status"] != original.parsed.status
                    or row["supplier_handle"] != original.supplier
                    or (row["coverage_json"] or {}).get("parser_version_full") != original.parsed.parser_version):
                raise ValueError("source_access_changed")
    access()
    protected = protect_sources(engine, repo, access)
    # SQL is the authority. Never read the mutable head or accept model-supplied refs.
    with engine.connect() as connection:
        frozen_signature = _scope_binding(connection, repo, run.run_id)
        frozen, definition = frozen_signature

    def scoped(retriever):
        if frozen is not None and definition is not None:
            retriever.set_item_scope(frozen, definition)
        return retriever
    if target is _CONFIGURED:
        try:
            target = configured_target()
        except ValueError:
            return scoped(ProtectedLexical(protected, access, "vector_configuration_error"))
    if target is None:
        return scoped(ProtectedLexical(protected, access, "vector_disabled"))
    def ready_rows():
        with engine.connect() as connection:
            return connection.execute(select(manifests).join(jobs, jobs.c.manifest_id == manifests.c.id).where(
                jobs.c.document_id.in_(doc_ids), jobs.c.case_id == repo.case_id, jobs.c.snapshot_id == repo.snapshot_id,
                jobs.c.status == "ready", jobs.c.target_key == target.key, manifests.c.status == "ready",
                manifests.c.expected_count == manifests.c.ingested_count, manifests.c.ingested_count > 0,
                manifests.c.collection_name == target.collection,
                manifests.c.project_key == project_key,
                manifests.c.embedding_revision == target.revision, manifests.c.dimension == target.dimension,
                manifests.c.chunk_schema == target.policy.revision)).mappings().all()
    rows = ready_rows()
    if {row["document_id"] for row in rows} != doc_ids or len(rows) != len(doc_ids):
        return scoped(ProtectedLexical(protected, access, "vector_index_not_ready"))
    generations = {r["document_id"]: r["generation"] for r in rows}
    if len({r["project_key"] for r in rows}) != 1:
        raise ValueError("source_access_changed")
    project = rows[0]["project_key"]
    chunks = chunk_snapshot(protected, project, target.revision, dimension=target.dimension,
                            policy=target.policy, generations=generations)
    for row in rows:
        document_chunks = tuple(c for c in chunks if c.document_id == row["document_id"])
        if (_receipt(document_chunks).fingerprint != row["fingerprint"]
                or len(document_chunks) != row["ingested_count"]):
            raise ValueError("index_receipt_mismatch")
    signatures = {(r["id"], r["fingerprint"], r["generation"], r["ingested_count"]) for r in rows}
    def validate():
        access()
        if {(r["id"], r["fingerprint"], r["generation"], r["ingested_count"]) for r in ready_rows()} != signatures:
            raise ValueError("source_access_changed")
    protected.validate_access = validate
    validate()
    model = store = None
    try:
        model = embedding_factory(target)
        if model.revision != target.revision or model.dimension != target.dimension:
            raise ValueError("index_model_mismatch")
        store = store_factory(target)
    except Exception:  # noqa: BLE001 -- A dependency failure degrades explicitly, without exposing SDK details.
        _close(model)
        _close(store)
        validate()
        return scoped(ProtectedLexical(protected, validate, "vector_service_unavailable"))
    try:
        validate()
        return scoped(ManagedHybrid(protected, project, model, store, receipt=_receipt(chunks),
                                    policy=target.policy, generations=generations, revalidate=validate))
    except BaseException:
        _close(model)
        _close(store)
        raise
