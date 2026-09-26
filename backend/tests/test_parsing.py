"""Source-backed parsing checks for the synthetic development pack."""

import json
import sys
from importlib import import_module
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import pytest
from openpyxl import Workbook

PACK = Path(__file__).resolve().parents[2] / "examples" / "dev-pack-001"


def parsing():
    return import_module("backend.app.parsing")


def test_mineru_blocks_keep_original_page_and_bbox() -> None:
    result = parsing().parse_mineru_middle(
        PACK / "parsed" / "requirements.middle.json", document_id="requirements"
    )

    assert result.status == "complete"
    assert result.total_pages == 1
    assert result.processed_pages == (0,)
    memory = next(block for block in result.blocks if "已装内存至少 16 GB" in block.text)
    assert memory.document_id == "requirements"
    assert memory.page_idx == 0
    assert memory.page_width_pt > 500
    assert memory.page_height_pt > 800
    assert len(memory.bbox) == 4
    assert all(0 <= value <= 1 for value in memory.bbox)


def test_xlsx_cells_keep_sheet_and_cell_coordinates() -> None:
    result = parsing().parse_xlsx(PACK / "vendor-a-quote.xlsx", document_id="vendor-a-quote")

    assert result.status == "complete"
    assert result.processed_sheets == ("报价",)
    cells = {(block.sheet, block.cell): block for block in result.blocks}
    assert cells[("报价", "B6")].text == "已装内存 16 GB"
    assert cells[("报价", "B9")].text == "合同签订后 10 个工作日"
    assert len({block.evidence_id for block in result.blocks}) == len(result.blocks)


def test_mineru_partial_flag_is_not_reported_as_complete(tmp_path: Path) -> None:
    source = json.loads(
        (PACK / "parsed" / "requirements.middle.json").read_text(encoding="utf-8")
    )
    source["is_full_document"] = False
    parsed_path = tmp_path / "partial.json"
    parsed_path.write_text(json.dumps(source), encoding="utf-8")

    result = parsing().parse_mineru_middle(parsed_path, document_id="requirements")
    assert result.status == "partial"


def test_formula_cells_are_rejected_instead_of_treated_as_values(tmp_path: Path) -> None:
    workbook = Workbook()
    workbook.active["B2"] = "=SUM(1, 2)"
    path = tmp_path / "formula.xlsx"
    workbook.save(path)

    with pytest.raises(parsing().UnsupportedContentError, match="formula"):
        parsing().parse_xlsx(path, document_id="formula")


def test_merged_xlsx_cells_do_not_claim_complete_coverage(tmp_path: Path) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet["A6"] = "已装内存"
    sheet.merge_cells("A6:A7")
    sheet["B6"] = "16 GB"
    sheet["B7"] = "32 GB"
    path = tmp_path / "merged.xlsx"
    workbook.save(path)

    result = parsing().parse_xlsx(path, document_id="merged")
    assert result.status == "partial"
    assert any("merged_cell" in issue for issue in result.coverage_issues)


def test_merged_xlsx_worksheet_at_relocated_part_is_still_partial(tmp_path: Path) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet["A6"] = "已装内存"
    sheet.merge_cells("A6:A7")
    sheet["B6"] = "16 GB"
    sheet["B7"] = "32 GB"
    original = tmp_path / "original.xlsx"
    relocated = tmp_path / "relocated.xlsx"
    workbook.save(original)
    old_part = b"/xl/worksheets/sheet1.xml"
    new_part = b"/xl/custom/sheet1.xml"
    with ZipFile(original) as source, ZipFile(relocated, "w", compression=ZIP_DEFLATED) as target:
        for item in source.infolist():
            data = source.read(item.filename)
            if item.filename in {"xl/_rels/workbook.xml.rels", "[Content_Types].xml"}:
                data = data.replace(old_part, new_part)
            name = item.filename.replace("xl/worksheets/sheet1.xml", "xl/custom/sheet1.xml")
            target.writestr(name, data)

    result = parsing().parse_xlsx(relocated, document_id="relocated")
    assert result.status == "partial"
    assert any("merged_cell" in issue for issue in result.coverage_issues)


def test_pdf_entrypoint_checks_source_and_runs_mineru(tmp_path: Path) -> None:
    source_json = PACK / "parsed" / "requirements.middle.json"
    fake_mineru = tmp_path / "fake_mineru.py"
    fake_mineru.write_text(
        "import shutil, sys\n"
        "from pathlib import Path\n"
        f"source = Path({str(source_json)!r})\n"
        "assert sys.argv[1] == 'parse'\n"
        "assert '--format' in sys.argv and 'middle_json' in sys.argv\n"
        "shutil.copyfile(source, sys.argv[sys.argv.index('-o') + 1])\n",
        encoding="utf-8",
    )

    result = parsing().parse_pdf(
        PACK / "requirements.pdf",
        document_id="requirements",
        mineru_command=[sys.executable, str(fake_mineru)],
    )

    assert result.status == "complete"
    assert any("采购数量 10 台" in block.text for block in result.blocks)


def test_all_labeled_evidence_can_be_located_in_parsed_sources() -> None:
    documents = {
        "requirements.pdf": parsing().parse_mineru_middle(
            PACK / "parsed" / "requirements.middle.json", document_id="requirements.pdf"
        ),
        "vendor-a-spec.pdf": parsing().parse_mineru_middle(
            PACK / "parsed" / "vendor-a-spec.middle.json", document_id="vendor-a-spec.pdf"
        ),
        "vendor-a-quote.xlsx": parsing().parse_xlsx(
            PACK / "vendor-a-quote.xlsx", document_id="vendor-a-quote.xlsx"
        ),
    }
    labels = json.loads((PACK / "labels.json").read_text(encoding="utf-8"))
    checked = 0
    for result in labels["results"]:
        for reference in result["requirement_evidence"] + result["supplier_evidence"]:
            parsed = documents[reference["document"]]
            assert parsed.status == "complete"
            assert any(
                reference["text"] in block.text
                and (
                    block.page_idx == reference["page"] - 1
                    if "page" in reference
                    else block.sheet == reference["sheet"] and block.cell == reference["cell"]
                )
                for block in parsed.blocks
            ), reference
            checked += 1
    assert checked == 16
