"""One durable index claim; no SQL transaction spans model or vector RPCs."""
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import replace
from threading import Event, Thread
from typing import Any

from ..evidence.hybrid import build_index
from ..vector_store.config import MilvusConfig
from ..vector_store.embedding import EmbeddingError, OllamaConfig, OllamaEmbedding
from ..vector_store.store import MilvusIndex, SearchScope, VectorUnavailable
from ..vector_store.target import IndexTarget
from .agent_snapshot import load_agent_snapshot
from .index_repository import CleanupClaim, IndexClaim, IndexError, IndexRepository
from .source_repository import protect_sources


def embedding_factory(target: IndexTarget) -> OllamaEmbedding:
    model = OllamaEmbedding(OllamaConfig.from_env())
    try:
        model.resolve()
        if model.revision != target.revision or model.dimension != target.dimension:
            raise ValueError("index_model_mismatch")
    except BaseException:
        model.close()
        raise
    return model


def store_factory(target: IndexTarget) -> MilvusIndex:
    return MilvusIndex(replace(MilvusConfig.from_env(), dimension=target.dimension),
                       target.revision, target.policy.revision)


def _close(resource: Any) -> bool:
    if resource is not None and hasattr(resource, "close"):
        try:
            resource.close()
        except Exception:  # noqa: BLE001 -- Fixed error codes only; never log external credentials.
            return False
    return True


@contextmanager
def _heartbeat(service: IndexRepository, claim: IndexClaim, interval: float) -> Iterator[Callable[[], None]]:
    if not 0 < interval < 150:
        raise ValueError("invalid_index_heartbeat")
    stopped, lost = Event(), Event()
    def pulse():
        while not stopped.wait(interval):
            try:
                service.renew(claim)
            except Exception:  # noqa: BLE001 -- Stop disclosure if lease renewal cannot be confirmed.
                lost.set()
                break
    thread = Thread(target=pulse, daemon=True, name="document-index-lease")
    thread.start()
    def validate():
        if lost.is_set():
            raise IndexError("stale_index_claim")
        service.validate(claim)
    try:
        yield validate
    finally:
        stopped.set()
        thread.join(timeout=5)


def process_index(service: IndexRepository, claim: IndexClaim, *,
                  embedding_factory: Callable[[IndexTarget], Any] = embedding_factory,
                  store_factory: Callable[[IndexTarget], Any] = store_factory, heartbeat_seconds: float = 10) -> str:
    model = store = None
    try:
        with _heartbeat(service, claim, heartbeat_seconds) as validate:
            validate()
            repo = load_agent_snapshot(service.engine, claim.case_id, claim.snapshot_id,
                                       document_ids=(claim.document_id,))
            repo = protect_sources(service.engine, repo, validate)
            model = embedding_factory(claim.target)
            validate()
            if model.revision != claim.target.revision or model.dimension != claim.target.dimension:
                raise ValueError("index_model_mismatch")
            store = store_factory(claim.target)
            receipt = build_index(repo, claim.project_key, model, store,
                                  policy=claim.target.policy, revalidate=validate,
                                  generations={claim.document_id: claim.generation})
            service.finish(claim, receipt.fingerprint, len(receipt.chunk_ids))
        return "ready"
    except IndexError:
        service.reconcile_stale()
        return "stale"
    except Exception as exc:  # noqa: BLE001 -- Sanitized persisted failure, bounded retries.
        code = "index_service_unavailable" if isinstance(exc, (VectorUnavailable, EmbeddingError)) else "index_processing_failed"
        try:
            return service.fail(claim, code)
        except IndexError:
            return "stale"
    finally:
        _close(model)
        _close(store)


def process_cleanup(service: IndexRepository, claim: CleanupClaim, *,
                    store_factory: Callable[[IndexTarget], Any] = store_factory) -> str:
    store = None
    try:
        scope = SearchScope(claim.project_key, claim.case_id, claim.snapshot_id,
                            (claim.document_id,), generations=((claim.document_id, claim.generation),))
        store = store_factory(claim.target)
        success = store.cleanup_scope(scope)
    except Exception:  # noqa: BLE001 -- Cleanup is retried from the retained SQL locator.
        success = False
    finally:
        _close(store)
    try:
        service.finish_cleanup(claim, success=success)
    except IndexError:
        return "stale"
    return "deleted" if success else "pending"
