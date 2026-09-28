"""Local operator entry point for parsing uploaded snapshot documents."""

import argparse
import json
import os
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine

from .db.connection import ROOT, database_url
from .db.ingestion import DocumentIngestion, IngestionError
from .parsing import parse_pdf


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Parse one uploaded Case snapshot")
    parser.add_argument("--case-id", required=True)
    parser.add_argument("--snapshot-id", required=True)
    parser.add_argument("--mineru-executable")
    parser.add_argument("--data-dir", type=Path)
    args = parser.parse_args(argv)
    load_dotenv(ROOT / ".env", override=False)
    root = args.data_dir or Path(os.getenv("DOCUCHECK_DATA_DIR", str(ROOT / "data")))
    executable = args.mineru_executable or os.getenv("DOCUCHECK_MINERU_EXECUTABLE")
    pdf_parser = (lambda path, document_id: parse_pdf(
        path, document_id=document_id, mineru_command=[executable],
    )) if executable else None
    engine = create_engine(database_url(), pool_pre_ping=True, hide_parameters=True)
    try:
        service = DocumentIngestion(engine, root, pdf_parser=pdf_parser)
        try:
            results = service.process_snapshot(args.case_id, args.snapshot_id)
        except IngestionError as exc:
            print(json.dumps({"error": exc.code}, ensure_ascii=False))
            return 2
        print(json.dumps({"case_id": args.case_id, "snapshot_id": args.snapshot_id,
                          "documents": results}, ensure_ascii=False))
        return 0 if all(item["parse_status"] in {"complete", "partial"}
                        for item in results) else 1
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
