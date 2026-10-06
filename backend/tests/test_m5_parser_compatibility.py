"""MinerU 4 text blocks and pagination must preserve conservative PDF coverage."""

import json
from pathlib import Path

import pytest
from pypdf import PdfReader
from reportlab.pdfgen import canvas

from backend.app.parsing import (
    _pdf_layout_issue,
    parse_mineru_middle,
    verify_pdf_source_coverage,
)

MIDDLE = Path(__file__).resolve().parents[2] / "examples/dev-pack-001/parsed/requirements.middle.json"


def _middle(tmp_path: Path, blocks: list[dict]) -> Path:
    payload = json.loads(MIDDLE.read_text(encoding="utf-8"))
    payload["pages"][0]["blocks"] = blocks
    path = tmp_path / "document.middle.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _block(kind: str, text: str) -> dict:
    return {"type": kind, "bbox": [0.1, 0.1, 0.8, 0.9],
            "content": [{"type": "text", "content": text}]}


@pytest.mark.parametrize("kind", ["paragraph_title", "footer"])
def test_mineru_plain_title_or_footer_is_complete_and_keeps_evidence(tmp_path, kind):
    parsed = parse_mineru_middle(_middle(tmp_path, [_block(kind, "Warranty 12 months")]), document_id="doc")

    assert parsed.status == "complete"
    assert parsed.blocks[0].text == "Warranty 12 months"
    assert parsed.blocks[0].page_idx == 0


@pytest.mark.parametrize("kind", ["paragraph_title", "footer"])
def test_mineru_plain_block_with_image_content_is_still_partial(tmp_path, kind):
    block = _block(kind, "Warranty 12 months")
    block["content"].append({"type": "image", "content": "unverified image"})
    parsed = parse_mineru_middle(_middle(tmp_path, [block]), document_id="doc")

    assert parsed.status == "partial"
    assert "unsupported_content" in parsed.coverage_issues[0]


def _pdf_with_rows(tmp_path: Path, rows: list[tuple[float, float, str]]) -> tuple[Path, Path]:
    source = tmp_path / "document.pdf"
    pdf = canvas.Canvas(str(source), pagesize=(612, 792))
    for x, y, text in rows:
        pdf.drawString(x, y, text)
    pdf.save()
    blocks = [_block("text", line) for line in PdfReader(source).pages[0].extract_text().splitlines()]
    return source, _middle(tmp_path, blocks)


def test_pdf_footer_beside_verified_pagination_is_complete(tmp_path):
    source, middle = _pdf_with_rows(tmp_path, [
        (72, 720, "Installed RAM 8 GB"),
        (72, 42, "Warranty 12 months"),
        (450, 42, "Page 1 of 1"),
    ])
    payload = json.loads(middle.read_text(encoding="utf-8"))
    payload["pages"][0]["blocks"][1]["type"] = "footer"
    payload["pages"][0]["blocks"][2]["type"] = "page_number"
    middle.write_text(json.dumps(payload), encoding="utf-8")

    result = verify_pdf_source_coverage(parse_mineru_middle(middle, document_id="doc"), source)

    assert result.status == "complete"
    assert not result.coverage_issues
    assert any(b.text == "Warranty 12 months" for b in result.blocks)


@pytest.mark.parametrize("pagination", ["Page 2 of 1", "Page 1 of 2"])
def test_pdf_pagination_exception_rejects_wrong_page_or_total(tmp_path, pagination):
    source, middle = _pdf_with_rows(tmp_path, [
        (72, 720, "Installed RAM 8 GB"), (72, 42, "Warranty 12 months"), (450, 42, pagination),
    ])

    result = verify_pdf_source_coverage(parse_mineru_middle(middle, document_id="doc"), source)

    assert result.status == "partial"
    assert "page:0:layout_columns_unverified" in result.coverage_issues


def test_pdf_pagination_text_inside_body_columns_is_still_partial(tmp_path):
    source, middle = _pdf_with_rows(tmp_path, [
        (72, 720, "Specification"), (72, 680, "Installed RAM"),
        (300, 680, "Page 1 of 1"), (72, 42, "End of specification"),
    ])

    result = verify_pdf_source_coverage(parse_mineru_middle(middle, document_id="doc"), source)

    assert result.status == "partial"
    assert "page:0:layout_columns_unverified" in result.coverage_issues


def test_pdf_footer_label_value_columns_beside_pagination_are_still_partial(tmp_path):
    source, middle = _pdf_with_rows(tmp_path, [
        (72, 720, "Specification"), (72, 42, "Warranty"),
        (270, 42, "12 months"), (450, 42, "Page 1 of 1"),
    ])

    result = verify_pdf_source_coverage(parse_mineru_middle(middle, document_id="doc"), source)

    assert result.status == "partial"
    assert "page:0:layout_columns_unverified" in result.coverage_issues


def test_unknown_mineru_block_kind_remains_partial(tmp_path):
    result = parse_mineru_middle(_middle(tmp_path, [_block("unknown_structure", "Warranty 12 months")]),
                                document_id="doc")

    assert result.status == "partial"
    assert "unsupported_content" in result.coverage_issues[0]


@pytest.mark.parametrize(("page_counter", "expected"), [
    ("第 1 页 / 共 1 页", None),
    ("第 1 页 / 共 2 页", "layout_columns_unverified"),
])
def test_chinese_pagination_requires_current_page_and_total(page_counter, expected):
    class TextPage:
        def extract_text(self, *, extraction_mode):
            return f"已装内存 8 GB\n完整原件   {page_counter}"

    assert _pdf_layout_issue(TextPage(), page_idx=0, total_pages=1) == expected
