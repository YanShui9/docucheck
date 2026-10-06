"""python -m backend.app.milvus_probe: no network unless --send is explicit."""
import argparse
import json
import logging
from collections.abc import Mapping
from pathlib import Path

from .vector_store.config import MilvusConfig, MilvusConfigurationError
from .vector_store.probe import MilvusProbeError, preview, run_probe


def main(argv: list[str] | None = None, *, environ: Mapping[str, str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Milvus 临时集合基础验收；默认仅预览。")
    parser.add_argument("--send", action="store_true", help="实连并创建/清理独立临时集合")
    args = parser.parse_args(argv)
    if environ is None:
        from dotenv import load_dotenv
        load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=False)
    # SDK error logging can include RPC arguments; this standalone CLI reports
    # sanitized codes and restores the process logging state before returning.
    previous_logging = logging.root.manager.disable
    logging.disable(logging.CRITICAL)
    try:
        config = MilvusConfig.from_env(environ)
        result = run_probe(config) if args.send else preview(config)
    except (MilvusConfigurationError, MilvusProbeError) as error:
        print(json.dumps({
            "mode": "send" if args.send else "preview", "status": "failed",
            "reason": str(error), "collection": getattr(error, "collection", None),
        }, ensure_ascii=False))
        return 2
    finally:
        logging.disable(previous_logging)
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
