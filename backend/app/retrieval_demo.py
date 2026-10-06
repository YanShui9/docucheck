"""Explicit local semantic retrieval demo over a hash-verified synthetic pack."""
import argparse
import json
import logging
from pathlib import Path
from time import perf_counter
from uuid import uuid4

from dotenv import load_dotenv

from .evidence.chunks import ChunkPolicy, chunk_snapshot
from .evidence.hybrid import HybridRetriever, build_index
from .evidence.repository import load_case
from .evidence.retrieval import Retriever
from .vector_store.config import MilvusConfig
from .vector_store.embedding import EmbeddingError, OllamaConfig, OllamaEmbedding
from .vector_store.store import MilvusIndex, VectorUnavailable

PROJECT = Path(__file__).resolve().parents[2]


def main(argv=None):
    parser = argparse.ArgumentParser(description="中文原文混合检索；不调用聊天大模型")
    parser.add_argument("--case-dir", type=Path, default=PROJECT / "examples/dev-pack-001")
    parser.add_argument("--question", default="什么时候能送到？")
    parser.add_argument("--supplier", default="vendor-a")
    parser.add_argument("--send", action="store_true", help="连接本机 Ollama 与 Milvus")
    args = parser.parse_args(argv)
    load_dotenv(PROJECT / ".env", override=False)
    if not args.question.strip() or len(args.question.encode()) > 8000:
        print(json.dumps({"status": "failed", "error": "retrieval_query"}))
        return 2
    previous = logging.root.manager.disable
    logging.disable(logging.CRITICAL)
    model = index = None
    attempted = False
    collection = "docucheck_rag_demo_" + uuid4().hex
    result = None
    exit_code = 0
    try:
        repo = load_case(args.case_dir)
        repo.assert_supplier(args.supplier)
        if not args.send:
            chunks = chunk_snapshot(repo, "local-synthetic-demo", "preview-unresolved-model")
            result = {"mode": "preview", "snapshot_id": repo.snapshot_id, "chunks": len(chunks),
                      "dimension": 1024, "network_calls": 0, "chat_model_calls": 0,
                      "embedding_requests": 0,
                      "requires": "local Ollama bge-m3:latest and scoped Milvus credential",
                      "persistent_api_enabled": False}
        else:
            cfg = MilvusConfig.from_env()
            if (cfg.uri != "http://127.0.0.1:19530" or cfg.database != "docucheck_probe"
                    or cfg.dimension != 1024
                    or not cfg.token.get_secret_value().startswith("docucheck_probe:")):
                raise ValueError("retrieval_demo_local_scope")
            model = OllamaEmbedding(OllamaConfig.from_env())
            model.resolve()
            index = MilvusIndex(cfg, model.revision, ChunkPolicy().revision, collection=collection)
            if index._call("has_collection"):
                raise ValueError("retrieval_demo_name_collision")
            attempted = True
            started = perf_counter()
            receipt = build_index(repo, "local-synthetic-demo", model, index)
            index_seconds = perf_counter() - started
            retriever = HybridRetriever(repo, "local-synthetic-demo", model, index, receipt=receipt)
            started = perf_counter()
            hits = retriever.search(args.question, supplier=args.supplier, limit=6)
            query_seconds = perf_counter() - started
            lexical = Retriever(repo).search(args.question, supplier=args.supplier, limit=6)
            degraded = retriever.last_diagnostics["degraded_reason"]
            result = {"mode": "send", "status": "partial" if degraded else "completed",
                      "embedding_revision": model.revision, "dimension": model.dimension,
                      "snapshot_id": repo.snapshot_id, "indexed_chunks": len(receipt.chunk_ids),
                      "index_seconds": round(index_seconds, 3), "query_seconds": round(query_seconds, 3),
                      "diagnostics": retriever.last_diagnostics,
                      "bm25_baseline": lexical, "hybrid_hits": hits, "chat_model_calls": 0,
                      "embedding_requests": model.embedding_requests,
                      "persistent_api_enabled": False, "temporary_collection_removed": False}
            exit_code = 2 if degraded else 0
    except (EmbeddingError, VectorUnavailable) as exc:
        result = {"status": "failed", "error": str(exc), "chat_model_calls": 0}
        exit_code = 2
    except Exception:  # noqa: BLE001 -- CLI errors never echo credentials or document payloads.
        result = {"status": "failed", "error": "retrieval_demo_invalid_input_or_state", "chat_model_calls": 0}
        exit_code = 2
    finally:
        try:
            if index is not None:
                try:
                    if attempted and index._call("has_collection"):
                        index._call("drop_collection")
                    if result is not None and "temporary_collection_removed" in result:
                        result["temporary_collection_removed"] = True
                finally:
                    index.close()
            if model is not None:
                model.close()
        except Exception:  # noqa: BLE001 -- Do not claim success after failed cleanup.
            result = {"status": "failed", "error": "retrieval_demo_cleanup_failed", "collection": collection}
            exit_code = 2
        finally:
            logging.disable(previous)
    print(json.dumps(result, ensure_ascii=False))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
