"""Execute leased document parses without holding a database transaction."""

from collections.abc import Callable
from pathlib import Path

from sqlalchemy import Engine

from ..parsing import ParseResult
from .ingestion import DocumentIngestion, IngestionError
from .parse_repository import ParseRepository


def process_once(engine: Engine, data_root: Path, *, limit: int = 1,
                 pdf_parser: Callable[[Path, str], ParseResult] | None = None) -> list[dict]:
    claims = ParseRepository(engine).claim(limit=limit)
    ingestion = DocumentIngestion(engine, data_root, pdf_parser=pdf_parser)
    results = []
    for claim in claims:
        try:
            result = ingestion.process_document(
                claim.case_id, claim.snapshot_id, claim.document_id,
                claim_id=claim.claim_id,
            )
        except IngestionError as exc:
            if exc.code == "stale_parse_claim":
                continue
            code = ("parser_not_configured" if exc.code == "parser_not_configured"
                    else "worker_failed")
            if not ParseRepository(engine).fail_claim(claim, code):
                continue
            result = ingestion._current_receipt(
                claim.case_id, claim.snapshot_id, claim.document_id)
        except Exception:  # noqa: BLE001 - never expose source or parser error text
            if not ParseRepository(engine).fail_claim(claim, "worker_failed"):
                continue
            result = ingestion._current_receipt(
                claim.case_id, claim.snapshot_id, claim.document_id)
        results.append(result)
    return results
