"""Opt-in host worker for durable document indexes; preview never opens a model."""
import argparse
import json
from threading import Event

from dotenv import load_dotenv
from sqlalchemy import create_engine

from .db.connection import ROOT, database_url
from .db.index_repository import IndexRepository
from .db.index_worker import process_cleanup, process_index
from .vector_store.target import configured_target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="文档向量索引 worker（默认仅检查配置）")
    parser.add_argument("--poll", action="store_true", help="明确启动循环，会调用配置的 Embedding 和 Milvus")
    parser.add_argument("--once", action="store_true", help="明确处理至多一个索引和一个清理任务")
    parser.add_argument("--cleanup-only", action="store_true", help="只清理失效向量，不调用 Embedding")
    args = parser.parse_args(argv)
    if args.poll and args.once:
        parser.error("--poll 与 --once 不能同时使用")
    load_dotenv(ROOT / ".env", override=False)
    try:
        target = configured_target()
    except ValueError:
        print(json.dumps({"error": "index_target_configuration"}))
        return 2
    if not args.poll and not args.once:
        print(json.dumps({"mode": "preview", "configured": target is not None,
                          "embedding_requests": 0, "vector_requests": 0, "database_requests": 0}))
        return 0
    if target is None and not args.cleanup_only:
        print(json.dumps({"error": "index_target_not_enabled"}))
        return 2
    engine = create_engine(database_url(), pool_pre_ping=True, hide_parameters=True)
    service, stop = IndexRepository(engine), Event()
    try:
        while True:
            work = False
            service.reconcile_stale()
            cleanup = service.claim_cleanup()
            if cleanup is not None:
                print(json.dumps({"task": "cleanup", "status": process_cleanup(service, cleanup)}), flush=True)
                work = True
            if not args.cleanup_only:
                claim = service.claim(target)
                if claim is not None:
                    print(json.dumps({"task": "index", "status": process_index(service, claim)}), flush=True)
                    work = True
            if args.once:
                return 0
            if not work:
                stop.wait(2)
    except KeyboardInterrupt:
        return 0
    except Exception:  # noqa: BLE001 -- CLI errors must not disclose database URLs or tokens.
        print(json.dumps({"error": "index_worker_failed"}))
        return 1
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
