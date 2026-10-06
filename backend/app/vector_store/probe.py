"""Small synthetic Milvus probe; this is not the business RAG implementation."""
from collections.abc import Callable
from uuid import uuid4

from .config import MilvusConfig

_SCOPE = (
    'project_id == "probe_project" and case_id == "probe_case" '
    'and snapshot_id == "probe_snapshot"'
)
_FIELDS = ["project_id", "case_id", "snapshot_id"]
_EXPECTED_SCOPE = dict(zip(
    _FIELDS, ["probe_project", "probe_case", "probe_snapshot"], strict=True,
))


class MilvusProbeError(RuntimeError):
    def __init__(self, code: str, *, collection: str | None = None):
        super().__init__(code)
        self.collection = collection


def preview(config: MilvusConfig) -> dict:
    return {
        "mode": "preview", "database": config.database,
        "dimension": config.dimension,
        "credentials_configured": bool(config.token.get_secret_value()),
        "network_calls": 0, "synthetic_vectors": 5,
        "action": "create unique temporary collection, search/query/delete, then drop",
        "business_rag_connected": False,
    }


def _sdk_client(config: MilvusConfig):
    try:
        from pymilvus import MilvusClient
    except ImportError:
        raise MilvusProbeError("milvus_sdk_unavailable") from None
    return MilvusClient(
        uri=config.uri, token=config.token.get_secret_value(),
        db_name=config.database, timeout=config.timeout,
    )


def run_probe(config: MilvusConfig, *, client_factory: Callable | None = None) -> dict:
    if not config.token.get_secret_value():
        raise MilvusProbeError("milvus_credentials_missing")
    try:
        client = (client_factory or _sdk_client)(config)
    except MilvusProbeError:
        raise
    except Exception:  # noqa: BLE001 -- SDK exceptions may contain credentials.
        raise MilvusProbeError("milvus_connection_failed") from None

    collection = "docucheck_probe_" + uuid4().hex
    created = attempted_create = False
    common = {"collection_name": collection, "timeout": config.timeout}
    try:
        if client.has_collection(**common):
            raise MilvusProbeError("probe_name_collision")
        attempted_create = True
        client.create_collection(
            **common, dimension=config.dimension, metric_type="COSINE",
            auto_id=False, consistency_level="Strong", enable_dynamic_field=True,
        )
        created = True
        vector = [1.0, *([0.0] * (config.dimension - 1))]
        rows = [
            {"id": number, "vector": vector,
             "project_id": "other_project" if number == 5 else "probe_project",
             "case_id": "other_case" if number == 3 else "probe_case",
             "snapshot_id": "old_snapshot" if number == 4 else "probe_snapshot"}
            for number in range(1, 6)
        ]
        client.upsert(**common, data=rows)
        hits = client.search(
            **common, data=[vector], anns_field="vector", filter=_SCOPE,
            output_fields=_FIELDS, limit=4, consistency_level="Strong",
        )
        if (len(hits) != 1 or len(hits[0]) != 2
                or {hit["id"] for hit in hits[0]} != {1, 2}
                or any(hit["entity"].get(key) != value
                       for hit in hits[0] for key, value in _EXPECTED_SCOPE.items())):
            raise MilvusProbeError("milvus_scope_probe_failed")
        selected = client.query(
            **common, filter=_SCOPE, output_fields=["id"], consistency_level="Strong",
        )
        if {row["id"] for row in selected} != {1, 2}:
            raise MilvusProbeError("milvus_read_visibility_failed")
        client.delete(**common, ids=[2])
        remaining = client.query(
            **common, filter=_SCOPE, output_fields=["id"], consistency_level="Strong",
        )
        if {row["id"] for row in remaining} != {1}:
            raise MilvusProbeError("milvus_delete_visibility_failed")
        result = {
            "mode": "send", "status": "completed", "collection": collection,
            "dimension": config.dimension, "matched_ids": [1, 2], "remaining_ids": [1],
            "consistency": "Strong", "synthetic_vectors": 5,
            "collection_removed": True, "business_rag_connected": False,
        }
    except MilvusProbeError:
        raise
    except Exception:  # noqa: BLE001 -- Sanitize all external SDK failures.
        raise MilvusProbeError(
            "milvus_operation_failed",
            collection=collection if attempted_create and not created else None,
        ) from None
    finally:
        cleanup_failed = close_failed = False
        if created:
            try:
                client.drop_collection(**common)
            except Exception:  # noqa: BLE001 -- Still close after SDK cleanup failure.
                cleanup_failed = True
        try:
            client.close()
        except Exception:  # noqa: BLE001 -- Closing errors cannot report success.
            close_failed = True
        if cleanup_failed:
            raise MilvusProbeError("milvus_cleanup_failed", collection=collection) from None
        if close_failed:
            raise MilvusProbeError("milvus_client_close_failed") from None
    return result
