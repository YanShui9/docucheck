"""Poll MySQL for uploaded documents and parse them on the MinerU host."""

import argparse
import json
import os
import time
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import Engine, create_engine

from .db.connection import ROOT, database_url
from .db.parse_worker import process_once
from .parsing import parse_pdf


def main(argv: list[str] | None = None, *, engine: Engine | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--poll-seconds", type=float, default=2)
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--mineru-executable")
    args = parser.parse_args(argv)
    if not 0.1 <= args.poll_seconds <= 60:
        parser.error("invalid poll interval")
    load_dotenv(ROOT / ".env", override=False)
    data_root = args.data_dir or Path(os.getenv("DOCUCHECK_DATA_DIR", str(ROOT / "data")))
    executable = args.mineru_executable or os.getenv("DOCUCHECK_MINERU_EXECUTABLE")
    pdf_parser = (lambda path, document_id: parse_pdf(
        path, document_id=document_id, mineru_command=[executable],
    )) if executable else None
    owns_engine = engine is None
    if engine is None:
        engine = create_engine(database_url(), pool_pre_ping=True,
                               hide_parameters=True)
    try:
        while True:
            try:
                receipts = process_once(engine, data_root, pdf_parser=pdf_parser)
            except Exception:  # noqa: BLE001 - DB errors may contain private values
                print(json.dumps({"error": "parse_worker_unavailable"}), flush=True)
                if args.once:
                    return 2
                time.sleep(max(args.poll_seconds, 5))
                continue
            for receipt in receipts:
                print(json.dumps({"document_id": receipt["document_id"],
                                  "snapshot_id": receipt["snapshot_id"],
                                  "parse_status": receipt["parse_status"],
                                  "coverage_issues": receipt["coverage_issues"]},
                                 ensure_ascii=False), flush=True)
            if args.once:
                return 0 if all(receipt["parse_status"] in {"complete", "partial"}
                                for receipt in receipts) else 1
            time.sleep(args.poll_seconds)
    except KeyboardInterrupt:
        return 130
    finally:
        if owns_engine:
            engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
