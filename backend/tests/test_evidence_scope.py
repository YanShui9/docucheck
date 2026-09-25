"""Evidence identity and completeness at the Agent trust boundary."""
import json
from dataclasses import replace
from importlib import import_module
from pathlib import Path

import pytest

from backend.app.parsing import parse_mineru_middle, parse_xlsx

PACK = Path(__file__).resolve().parents[2] / "examples/dev-pack-001"


def repository(case="case-a", revision=1, parser_version=None):
    module = import_module("backend.app.evidence.repository")
    parsed = parse_xlsx(PACK / "vendor-a-quote.xlsx", document_id="quote")
    if parser_version:
        parsed = replace(parsed, parser_version=parser_version)
    source = module.bind_document(parsed, source_path=PACK / "vendor-a-quote.xlsx",
                                  role="quote", supplier="vendor-a", model="DC-14-A")
    return module.EvidenceRepository(case, revision, [source])


def test_cross_case_and_stale_evidence_are_rejected():
    original = repository()
    foreign = repository("case-b")
    stale = repository(revision=2)
    evidence_id = original.blocks[0].evidence_id
    assert original.read([evidence_id])[0].evidence_id == evidence_id
    for other in [foreign, stale, repository(parser_version="changed")]:
        with pytest.raises(ValueError, match="evidence_scope"):
            other.read([evidence_id])


def test_unknown_pdf_content_cannot_claim_full_coverage(tmp_path):
    source = json.loads((PACK / "parsed/requirements.middle.json").read_text(encoding="utf-8"))
    source["pages"][0]["blocks"].append({
        "type": "table", "content": [{"type": "html", "content": "<table>32 GB</table>"}],
        "bbox": [0.1, 0.1, 0.5, 0.5],
    })
    path = tmp_path / "unhandled.json"
    path.write_text(json.dumps(source), encoding="utf-8")
    result = parse_mineru_middle(path, document_id="requirements")
    assert result.status == "partial"
    assert result.coverage_issues


def test_snapshot_keeps_original_coordinates_and_does_not_read_labels():
    module = import_module("backend.app.evidence.repository")
    repo = module.load_case(PACK)
    assert len(repo.blocks) == 35
    memory = [b for b in repo.blocks if "已装内存" in b.text and "GB" in b.text]
    assert len(memory) == 3
    assert any(b.cell == "B6" and b.sheet == "报价" for b in memory)
    assert any(b.page_idx == 0 and b.bbox for b in memory)
    assert repo.coverage("vendor-a")["complete"]


def test_modified_original_cannot_use_stale_parser_cache(tmp_path):
    import shutil
    module = import_module("backend.app.evidence.repository")
    shutil.copytree(PACK, tmp_path / "pack")
    (tmp_path / "pack/requirements.pdf").write_bytes(b"%PDF-changed")
    with pytest.raises(ValueError, match="source_hash"):
        module.load_case(tmp_path / "pack")


def test_unknown_text_block_type_is_not_silently_supported(tmp_path):
    source = json.loads((PACK / "parsed/requirements.middle.json").read_text(encoding="utf-8"))
    source["pages"][0]["blocks"][0]["type"] = "unknown_grid"
    path = tmp_path / "unknown.json"
    path.write_text(json.dumps(source), encoding="utf-8")
    assert parse_mineru_middle(path, document_id="req").status == "partial"


def test_parser_configuration_changes_identity(tmp_path):
    source = json.loads((PACK / "parsed/requirements.middle.json").read_text(encoding="utf-8"))
    original = parse_mineru_middle(PACK / "parsed/requirements.middle.json", document_id="req")
    source["extensions"]["mineru"]["parse_mode"] = "ocr"
    path = tmp_path / "ocr.json"
    path.write_text(json.dumps(source), encoding="utf-8")
    assert parse_mineru_middle(path, document_id="req").parser_version != original.parser_version


def test_pdf_page_without_extracted_body_is_partial_even_if_full_document_flag_is_set(tmp_path):
    source = json.loads((PACK / "parsed/requirements.middle.json").read_text(encoding="utf-8"))
    source["pages"][0]["blocks"] = []
    path = tmp_path / "empty_page.json"
    path.write_text(json.dumps(source), encoding="utf-8")

    result = parse_mineru_middle(path, document_id="requirements")
    assert result.status == "partial"
    assert "page:0:no_body_text" in result.coverage_issues
