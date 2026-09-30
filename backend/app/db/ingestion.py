"""Parse uploaded originals and commit snapshot-scoped evidence atomically."""

import json
import os
from collections.abc import Callable
from dataclasses import asdict
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path, PurePosixPath

from sqlalchemy import Engine, func, insert, select, update

from ..parsing import (
    MAX_FILE_BYTES,
    DocumentParsingError,
    ParseResult,
    UnsupportedContentError,
    parse_pdf,
    parse_xlsx,
)
from .models import cases, document_versions, evidence_blocks

MAX_EVIDENCE_BYTES = 60_000
MAX_EVIDENCE_BLOCKS = 25_000
MAX_EVIDENCE_TOTAL_BYTES = 50 * 1024 * 1024


class IngestionError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _digest(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":")).encode("utf-8")
    return sha256(encoded).hexdigest()


def _stored_parser_version(version: str) -> str:
    # The first migration has VARCHAR(64); keep the complete string in coverage_json.
    return f"parser:{sha256(version.encode('utf-8')).hexdigest()[:32]}"


def _bbox_payload(block) -> dict | None:
    if block.bbox is None:
        return None
    return {"bbox": list(block.bbox), "page_width_pt": block.page_width_pt,
            "page_height_pt": block.page_height_pt}


class DocumentIngestion:
    def __init__(self, engine: Engine, data_root: Path,
                 pdf_parser: Callable[[Path, str], ParseResult] | None = None):
        self.engine = engine
        self.data_root = data_root.resolve()
        self.pdf_parser = pdf_parser

    def _document(self, case_id: str, snapshot_id: str, document_id: str):
        with self.engine.connect() as connection:
            row = connection.execute(select(document_versions).where(
                document_versions.c.case_id == case_id,
                document_versions.c.snapshot_id == snapshot_id,
                document_versions.c.id == document_id,
            )).mappings().first()
        if row is None:
            raise IngestionError("not_found")
        return row

    def _require_readable(self, case_id: str) -> None:
        with self.engine.connect() as connection:
            case = connection.execute(select(
                cases.c.deletion_cleanup_started_at,
                cases.c.deletion_completed_at,
            ).where(cases.c.id == case_id)).first()
        if case is not None and case.deletion_completed_at is not None:
            raise IngestionError("case_deleted")
        if case is not None and case.deletion_cleanup_started_at is not None:
            raise IngestionError("case_deleting")

    def _source(self, row) -> Path:
        key = row["storage_key"]
        if not isinstance(key, str):
            raise IngestionError("source_path_invalid")
        relative = PurePosixPath(key)
        if (relative.is_absolute()
                or len(relative.parts) < 3 or relative.parts[0] != "uploads"
                or any(part in {".", ".."} for part in relative.parts)
                or "\\" in key or ":" in key):
            raise IngestionError("source_path_invalid")
        path = (self.data_root / relative).resolve()
        if not path.is_relative_to(self.data_root):
            raise IngestionError("source_path_invalid")
        if not path.is_file():
            raise IngestionError("source_missing")
        if path.stat().st_size > MAX_FILE_BYTES or path.stat().st_size == 0:
            raise IngestionError("source_limit")
        digest = sha256()
        with path.open("rb") as source:
            while chunk := source.read(1024 * 1024):
                digest.update(chunk)
        if digest.hexdigest() != row["source_sha256"]:
            raise IngestionError("source_hash_mismatch")
        return path

    def _parse(self, row, path: Path) -> ParseResult:
        if row["media_type"] == "application/pdf" and path.suffix == ".pdf":
            if self.pdf_parser is not None:
                return self.pdf_parser(path, row["id"])
            executable = os.getenv("DOCUCHECK_MINERU_EXECUTABLE")
            if not executable:
                raise IngestionError("parser_not_configured")
            return parse_pdf(path, document_id=row["id"],
                             mineru_command=[executable])
        if (row["media_type"] ==
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                and path.suffix == ".xlsx"):
            return parse_xlsx(path, document_id=row["id"])
        raise IngestionError("source_type_mismatch")

    @staticmethod
    def _validate_result(row, parsed: ParseResult) -> None:
        if (parsed.document_id != row["id"] or not parsed.parser_version
                or parsed.status not in {"complete", "partial"}
                or len(parsed.blocks) > MAX_EVIDENCE_BLOCKS
                or (parsed.status == "complete" and parsed.coverage_issues)):
            raise IngestionError("parser_result_invalid")
        expected_pages = (row["coverage_json"] or {}).get("preflight", {}).get("pdf_pages", 0)
        if row["media_type"] == "application/pdf" and parsed.total_pages != expected_pages:
            raise IngestionError("parser_result_invalid")
        total_bytes = 0
        for block in parsed.blocks:
            text_bytes = len(block.text.encode("utf-8"))
            total_bytes += text_bytes
            if (block.document_id != row["id"] or not block.text
                    or text_bytes > MAX_EVIDENCE_BYTES
                    or total_bytes > MAX_EVIDENCE_TOTAL_BYTES):
                raise IngestionError("parser_result_invalid")
            if row["media_type"] == "application/pdf":
                if (block.page_idx is None or block.page_idx < 0
                        or block.page_idx >= parsed.total_pages or block.bbox is None
                        or block.page_width_pt is None or block.page_width_pt <= 0
                        or block.page_height_pt is None or block.page_height_pt <= 0):
                    raise IngestionError("parser_result_invalid")
            elif block.sheet is None or block.cell is None:
                raise IngestionError("parser_result_invalid")

    def _receipt(self, connection, row) -> dict:
        count = connection.scalar(select(func.count()).select_from(evidence_blocks).where(
            evidence_blocks.c.document_version_id == row["id"],
        ))
        coverage = row["coverage_json"] or {}
        return {"case_id": row["case_id"], "snapshot_id": row["snapshot_id"],
                "document_id": row["id"], "parse_status": row["parse_status"],
                "parser_version": coverage.get("parser_version_full"),
                "coverage_issues": coverage.get("coverage_issues", []),
                "evidence_count": count}

    def _current_receipt(self, case_id: str, snapshot_id: str,
                         document_id: str) -> dict:
        with self.engine.connect() as connection:
            row = connection.execute(select(document_versions).where(
                document_versions.c.case_id == case_id,
                document_versions.c.snapshot_id == snapshot_id,
                document_versions.c.id == document_id,
            )).mappings().one()
            return self._receipt(connection, row)

    def _fail(self, row, code: str, claim_id: str | None = None) -> dict:
        coverage = {"preflight": (row["coverage_json"] or {}).get("preflight", {}),
                    "coverage_issues": [code]}
        current = datetime.now(UTC).replace(tzinfo=None)
        with self.engine.begin() as connection:
            condition = [
                document_versions.c.id == row["id"],
                document_versions.c.parse_status == row["parse_status"],
            ]
            if claim_id is not None:
                condition.extend((document_versions.c.parse_claim_id == claim_id,
                                  document_versions.c.parse_lease_until > current))
            connection.execute(update(document_versions).where(*condition).values(
                parse_status="failed", parser_version=None,
                parse_claim_id=None, parse_lease_until=None,
                coverage_json=coverage))
        return self._current_receipt(row["case_id"], row["snapshot_id"], row["id"])

    def process_document(self, case_id: str, snapshot_id: str,
                         document_id: str, *, claim_id: str | None = None) -> dict:
        with self.engine.connect() as connection:
            case = connection.execute(select(
                cases.c.deletion_requested_at,
            ).where(cases.c.id == case_id)).first()
        if case is not None and case.deletion_requested_at is not None:
            raise IngestionError("case_deleting")
        row = self._document(case_id, snapshot_id, document_id)
        if row["parse_status"] in {"complete", "partial"}:
            return self._current_receipt(case_id, snapshot_id, document_id)
        if claim_id is not None:
            if row["parse_status"] != "processing" or row["parse_claim_id"] != claim_id:
                raise IngestionError("stale_parse_claim")
        elif row["parse_status"] not in {"pending", "failed"}:
            raise IngestionError("invalid_parse_state")
        try:
            path = self._source(row)
            parsed = self._parse(row, path)
            self._source(row)
            self._validate_result(row, parsed)
        except IngestionError as exc:
            if exc.code == "parser_not_configured":
                raise
            return self._fail(row, exc.code, claim_id)
        except UnsupportedContentError:
            return self._fail(row, "unsupported_content", claim_id)
        except DocumentParsingError:
            return self._fail(row, "parser_failed", claim_id)

        coverage = {
            "preflight": (row["coverage_json"] or {}).get("preflight", {}),
            "parser_version_full": parsed.parser_version,
            "total_pages": parsed.total_pages,
            "processed_pages": list(parsed.processed_pages),
            "total_sheets": parsed.total_sheets,
            "processed_sheets": list(parsed.processed_sheets),
            "coverage_issues": list(parsed.coverage_issues),
        }
        with self.engine.begin() as connection:
            current = datetime.now(UTC).replace(tzinfo=None)
            case = connection.execute(select(
                cases.c.deletion_requested_at,
            ).where(cases.c.id == case_id).with_for_update()).first()
            if case is not None and case.deletion_requested_at is not None:
                raise IngestionError("case_deleting")
            condition = [
                document_versions.c.id == document_id,
                document_versions.c.case_id == case_id,
                document_versions.c.snapshot_id == snapshot_id,
                document_versions.c.parse_status == row["parse_status"],
            ]
            if claim_id is not None:
                condition.extend((document_versions.c.parse_claim_id == claim_id,
                                  document_versions.c.parse_lease_until > current))
            changed = connection.execute(update(document_versions).where(*condition).values(
                parse_status=parsed.status, parse_claim_id=None,
                parse_lease_until=None,
                     parser_version=_stored_parser_version(parsed.parser_version),
                     coverage_json=coverage))
            if changed.rowcount == 1:
                now = datetime.now(UTC).replace(tzinfo=None)
                for ordinal, block in enumerate(parsed.blocks):
                    evidence_id = _digest([snapshot_id, document_id,
                                           row["source_sha256"], parsed.parser_version,
                                           ordinal, asdict(block)])
                    connection.execute(insert(evidence_blocks).values(
                        id=evidence_id, document_version_id=document_id,
                        ordinal=ordinal, source_text=block.text,
                        page_idx=block.page_idx, bbox_json=_bbox_payload(block),
                        sheet=block.sheet, cell=block.cell, created_at=now,
                    ))
        return self._current_receipt(case_id, snapshot_id, document_id)

    def process_snapshot(self, case_id: str, snapshot_id: str) -> list[dict]:
        with self.engine.connect() as connection:
            ids = connection.scalars(select(document_versions.c.id).where(
                document_versions.c.case_id == case_id,
                document_versions.c.snapshot_id == snapshot_id,
            ).order_by(document_versions.c.role, document_versions.c.id)).all()
        if not ids:
            raise IngestionError("not_found")
        return [self.process_document(case_id, snapshot_id, document_id)
                for document_id in ids]

    @staticmethod
    def _evidence(row, document) -> dict:
        bbox = row["bbox_json"] or {}
        coverage = document["coverage_json"] or {}
        return {"id": row["id"], "document_id": document["id"],
                "ordinal": row["ordinal"], "source_text": row["source_text"],
                "page_idx": row["page_idx"], "bbox": bbox.get("bbox"),
                "page_width_pt": bbox.get("page_width_pt"),
                "page_height_pt": bbox.get("page_height_pt"),
                "sheet": row["sheet"], "cell": row["cell"],
                "parser_version": coverage.get("parser_version_full")}

    def list_evidence(self, case_id: str, snapshot_id: str, document_id: str,
                      after_ordinal: int = -1, limit: int = 100) -> dict:
        self._require_readable(case_id)
        document = self._document(case_id, snapshot_id, document_id)
        with self.engine.connect() as connection:
            rows = connection.execute(select(evidence_blocks).where(
                evidence_blocks.c.document_version_id == document_id,
                evidence_blocks.c.ordinal > after_ordinal,
            ).order_by(evidence_blocks.c.ordinal).limit(limit)).mappings().all()
        coverage = document["coverage_json"] or {}
        evidence = [self._evidence(row, document) for row in rows]
        return {"case_id": case_id, "snapshot_id": snapshot_id,
                "document_id": document_id, "parse_status": document["parse_status"],
                "parser_version": coverage.get("parser_version_full"),
                "coverage_issues": coverage.get("coverage_issues", []),
                "evidence": evidence,
                "next_ordinal": evidence[-1]["ordinal"] if evidence else after_ordinal}

    def read_evidence(self, case_id: str, snapshot_id: str,
                      evidence_id: str) -> dict:
        self._require_readable(case_id)
        with self.engine.connect() as connection:
            row = connection.execute(select(
                evidence_blocks, document_versions.c.id.label("owner_document_id"),
                document_versions.c.coverage_json,
            )
                                     .join(document_versions,
                                           evidence_blocks.c.document_version_id ==
                                           document_versions.c.id)
                                     .where(document_versions.c.case_id == case_id,
                                            document_versions.c.snapshot_id == snapshot_id,
                                            evidence_blocks.c.id == evidence_id)
                                     ).mappings().first()
        if row is None:
            raise IngestionError("not_found")
        return self._evidence(row, {"id": row["owner_document_id"],
                                    "coverage_json": row["coverage_json"]})
