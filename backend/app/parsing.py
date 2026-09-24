"""Turn supported document parser outputs into source-located evidence blocks."""

from __future__ import annotations

import json
import subprocess
import tempfile
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Literal, Sequence
from zipfile import BadZipFile, ZipFile

from openpyxl import load_workbook
from pypdf import PdfReader


MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_XLSX_UNCOMPRESSED_BYTES = 50 * 1024 * 1024
MAX_SHEETS = 5
MAX_ROWS = 1000
MAX_COLUMNS = 30
MAX_NONEMPTY_CELLS = 20_000
MAX_CELL_CHARACTERS = 2000


class UnsupportedContentError(ValueError):
    """The document is outside the supported input contract."""


class DocumentParsingError(RuntimeError):
    """A supported document could not be fully parsed."""


@dataclass(frozen=True)
class EvidenceBlock:
    evidence_id: str
    document_id: str
    text: str
    page_idx: int | None = None
    bbox: tuple[float, float, float, float] | None = None
    page_width_pt: float | None = None
    page_height_pt: float | None = None
    sheet: str | None = None
    cell: str | None = None


@dataclass(frozen=True)
class ParseResult:
    document_id: str
    parser_version: str
    status: Literal["complete", "partial"]
    blocks: tuple[EvidenceBlock, ...]
    total_pages: int | None = None
    processed_pages: tuple[int, ...] = ()
    total_sheets: int | None = None
    processed_sheets: tuple[str, ...] = ()


def _evidence_id(document_id: str, locator: str, text: str) -> str:
    return sha256(f"{document_id}\0{locator}\0{text}".encode("utf-8")).hexdigest()[:24]


def parse_mineru_middle(path: Path, *, document_id: str) -> ParseResult:
    """Read MinerU 4 middle JSON, preserving normalized PDF page coordinates."""
    source = json.loads(Path(path).read_text(encoding="utf-8"))
    if source.get("schema") != "docvortex.middle" or source.get("schema_version") != "2.0":
        raise UnsupportedContentError("unsupported MinerU middle JSON schema")
    pages = source.get("pages")
    if not isinstance(pages, list):
        raise UnsupportedContentError("MinerU output has no pages")

    metadata = source.get("metadata", {})
    total_pages = metadata.get("document", {}).get("page_count")
    if not isinstance(total_pages, int) or total_pages < 1 or total_pages > 30:
        raise UnsupportedContentError("PDF page count is missing or exceeds limit")
    layout_pages = {
        item["page_idx"]: item
        for item in source.get("extensions", {}).get("docvortex_layout", {}).get("pages", [])
    }
    blocks: list[EvidenceBlock] = []
    processed_pages: list[int] = []
    for page in pages:
        page_idx = page.get("page_idx")
        if not isinstance(page_idx, int) or page_idx < 0 or page_idx >= total_pages:
            raise UnsupportedContentError("invalid PDF page index")
        if page_idx in processed_pages:
            raise UnsupportedContentError("duplicate PDF page index")
        layout = layout_pages.get(page_idx)
        if layout is None or layout.get("width_pt", 0) <= 0 or layout.get("height_pt", 0) <= 0:
            raise UnsupportedContentError("PDF page dimensions are missing")
        processed_pages.append(page_idx)
        for block_index, block in enumerate(page.get("blocks", [])):
            text = " ".join(
                str(item["content"]).strip()
                for item in block.get("content", [])
                if item.get("type") == "text" and item.get("content")
            ).strip()
            if not text:
                continue
            raw_bbox = block.get("bbox")
            if (
                not isinstance(raw_bbox, list)
                or len(raw_bbox) != 4
                or not all(isinstance(value, (int, float)) and 0 <= value <= 1 for value in raw_bbox)
                or raw_bbox[0] > raw_bbox[2]
                or raw_bbox[1] > raw_bbox[3]
            ):
                raise UnsupportedContentError("invalid PDF evidence bbox")
            locator = f"page:{page_idx}:block:{block_index}"
            blocks.append(
                EvidenceBlock(
                    evidence_id=_evidence_id(document_id, locator, text),
                    document_id=document_id,
                    text=text,
                    page_idx=page_idx,
                    bbox=tuple(float(value) for value in raw_bbox),
                    page_width_pt=float(layout["width_pt"]),
                    page_height_pt=float(layout["height_pt"]),
                )
            )

    complete = source.get("is_full_document") is True and sorted(processed_pages) == list(
        range(total_pages)
    )
    producer = metadata.get("producer", {})
    parser_version = f"{producer.get('name', 'mineru')}:{producer.get('version', 'unknown')}/middle:2.0"
    return ParseResult(
        document_id=document_id,
        parser_version=parser_version,
        status="complete" if complete else "partial",
        blocks=tuple(blocks),
        total_pages=total_pages,
        processed_pages=tuple(sorted(processed_pages)),
    )


def parse_pdf(
    path: Path,
    *,
    document_id: str,
    mineru_command: Sequence[str],
    timeout_seconds: int = 180,
) -> ParseResult:
    """Run local MinerU Basic on a text PDF and convert its middle JSON output."""
    source = Path(path)
    if source.suffix.lower() != ".pdf" or source.stat().st_size > MAX_FILE_BYTES:
        raise UnsupportedContentError("expected a PDF file of at most 10 MiB")
    if source.read_bytes()[:5] != b"%PDF-":
        raise UnsupportedContentError("file content is not PDF")
    try:
        pdf = PdfReader(source)
        page_count = len(pdf.pages)
        if page_count < 1 or page_count > 30:
            raise UnsupportedContentError("PDF page count exceeds 30")
        if any(not page.extract_text().strip() for page in pdf.pages):
            raise UnsupportedContentError("scanned or empty PDF pages are unsupported")
    except UnsupportedContentError:
        raise
    except Exception as exc:
        raise UnsupportedContentError("PDF structure could not be read") from exc

    with tempfile.TemporaryDirectory(prefix="docucheck-mineru-") as temp_dir:
        output_path = Path(temp_dir) / "document.middle.json"
        command = [
            *mineru_command,
            "parse",
            str(source),
            "-o",
            str(output_path),
            "--format",
            "middle_json",
            "--tier",
            "basic",
            "--ocr-mode",
            "txt",
        ]
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise DocumentParsingError("MinerU timed out") from exc
        except OSError as exc:
            raise DocumentParsingError("MinerU could not be started") from exc
        if completed.returncode != 0 or not output_path.is_file():
            raise DocumentParsingError(f"MinerU failed with exit code {completed.returncode}")
        result = parse_mineru_middle(output_path, document_id=document_id)
    if result.total_pages != page_count:
        raise DocumentParsingError("MinerU page count differs from the source PDF")
    return result


def parse_xlsx(path: Path, *, document_id: str) -> ParseResult:
    """Read literal XLSX cells, rejecting formulas and inputs beyond phase-one limits."""
    source = Path(path)
    if source.suffix.lower() != ".xlsx" or source.stat().st_size > MAX_FILE_BYTES:
        raise UnsupportedContentError("expected an XLSX file of at most 10 MiB")
    try:
        with ZipFile(source) as archive:
            uncompressed_bytes = sum(item.file_size for item in archive.infolist())
            if uncompressed_bytes > MAX_XLSX_UNCOMPRESSED_BYTES:
                raise UnsupportedContentError("XLSX uncompressed size exceeds 50 MiB")
    except BadZipFile as exc:
        raise UnsupportedContentError("XLSX is not a valid ZIP container") from exc

    workbook = load_workbook(source, read_only=True, data_only=False)
    try:
        if len(workbook.sheetnames) > MAX_SHEETS:
            raise UnsupportedContentError("XLSX sheet count exceeds 5")
        blocks: list[EvidenceBlock] = []
        nonempty = 0
        for sheet in workbook:
            if (sheet.max_row is not None and sheet.max_row > MAX_ROWS) or (
                sheet.max_column is not None and sheet.max_column > MAX_COLUMNS
            ):
                raise UnsupportedContentError("XLSX sheet dimensions exceed 1000 rows or 30 columns")
            for row in sheet.iter_rows():
                for cell in row:
                    if cell.value is None:
                        continue
                    if cell.row > MAX_ROWS or cell.column > MAX_COLUMNS:
                        raise UnsupportedContentError(
                            "XLSX sheet dimensions exceed 1000 rows or 30 columns"
                        )
                    nonempty += 1
                    if nonempty > MAX_NONEMPTY_CELLS:
                        raise UnsupportedContentError("XLSX nonempty cell count exceeds 20000")
                    if cell.data_type == "f":
                        raise UnsupportedContentError("formula cells are unsupported")
                    text = str(cell.value)
                    if len(text) > MAX_CELL_CHARACTERS:
                        raise UnsupportedContentError("XLSX cell text exceeds 2000 characters")
                    locator = f"sheet:{sheet.title}:cell:{cell.coordinate}"
                    blocks.append(
                        EvidenceBlock(
                            evidence_id=_evidence_id(document_id, locator, text),
                            document_id=document_id,
                            text=text,
                            sheet=sheet.title,
                            cell=cell.coordinate,
                        )
                    )
        return ParseResult(
            document_id=document_id,
            parser_version="openpyxl:3.1.5",
            status="complete",
            blocks=tuple(blocks),
            total_sheets=len(workbook.sheetnames),
            processed_sheets=tuple(workbook.sheetnames),
        )
    finally:
        workbook.close()
