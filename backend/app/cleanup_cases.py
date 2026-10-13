"""Poll MySQL for requested Case deletions and finish file/database cleanup."""

import argparse
import json
import os
import time
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import Engine, create_engine

from .db.case_cleanup import CaseCleanup
from .db.connection import ROOT, database_url
from .db.upload_intents import UploadIntents


def main(argv: list[str] | None = None, *, engine: Engine | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--case-id")
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--poll-seconds", type=float, default=2)
    parser.add_argument("--max-files", type=int, default=20)
    args = parser.parse_args(argv)
    if not 0.1 <= args.poll_seconds <= 60 or not 1 <= args.max_files <= 100:
        parser.error("invalid cleanup limits")
    load_dotenv(ROOT / ".env", override=False)
    data_root = args.data_dir or Path(os.getenv("DOCUCHECK_DATA_DIR", str(ROOT / "data")))
    owns_engine = engine is None
    if engine is None:
        engine = create_engine(database_url(), pool_pre_ping=True,
                               hide_parameters=True)
    cleaner = CaseCleanup(engine, data_root)
    uploads = UploadIntents(engine, data_root)
    try:
        while True:
            try:
                result = cleaner.process_once(case_id=args.case_id,
                                              max_files=args.max_files)
                recovery = uploads.reconcile(limit=args.max_files) if args.case_id is None else None
            except Exception:  # noqa: BLE001 - no paths or DB details in worker output
                print(json.dumps({"error": "cleanup_worker_unavailable"}),
                      flush=True)
                if args.once:
                    return 2
                time.sleep(max(args.poll_seconds, 5))
                continue
            if result is not None:
                print(json.dumps(result), flush=True)
            if recovery is not None and (recovery["checked"] or recovery["error_code"]):
                print(json.dumps({"upload_recovery": recovery}), flush=True)
            if args.once:
                return 1 if ((result is not None and result["error_code"])
                    or (recovery is not None and recovery["error_code"])) else 0
            time.sleep(args.poll_seconds)
    except KeyboardInterrupt:
        return 130
    finally:
        if owns_engine:
            engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
