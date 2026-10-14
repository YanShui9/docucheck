"""Dynamic evaluation assets must preserve source identity and gold separation."""

import importlib
import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest


def corpus_module():
    assert importlib.util.find_spec("evals.dynamic.corpus"), "dynamic corpus builder missing"
    return importlib.import_module("evals.dynamic.corpus")


@pytest.fixture
def font_path():
    from backend.tests.test_candidate_corpus import FONT

    if not FONT.is_file():
        pytest.skip("set DOCUCHECK_CJK_FONT to an available Chinese TrueType font")
    return FONT


def test_default_corpus_has_12_packs_104_cells_and_verified_originals(tmp_path, font_path):
    module = corpus_module()
    root = tmp_path / "corpus"
    module.build_corpus(root, font_path=font_path)
    result = module.validate_corpus(root)
    assert result["errors"] == []
    assert (result["packs"], result["primary_cells"], result["documents"]) == (12, 104, 58)
    assert result["independent_sealed_packs"] == 0
    manifest, gold, originals = module.load_pack(root, "dyn-d10")
    assert len(gold.cells) == 12
    assert len(manifest.checklist_definition.items) == 2
    assert set(originals) == {d.filename for d in manifest.documents}
    assert all(b"expected_business" not in content for content in originals.values())
    assert all(len(binding.scope.cell_range or "") or binding.scope.page_start
               for binding in manifest.bindings)


def test_existing_destination_is_rejected_without_changing_files(tmp_path, font_path):
    root = tmp_path / "existing"
    root.mkdir()
    sentinel = root / "do-not-touch.txt"
    sentinel.write_text("original", encoding="utf-8")
    with pytest.raises(ValueError, match="destination_exists"):
        corpus_module().build_corpus(root, font_path=font_path)
    assert sentinel.read_text(encoding="utf-8") == "original"


def test_tampered_original_or_extra_input_is_rejected(tmp_path, font_path):
    module = corpus_module()
    root = tmp_path / "corpus"
    module.build_corpus(root, font_path=font_path, pack_ids=["dyn-d01"])
    path = root / "inputs/dyn-d01/vendor-a-quote.xlsx"
    original = path.read_bytes()
    path.write_bytes(original + b"tamper")
    with pytest.raises(ValueError, match="input_integrity"):
        module.load_pack(root, "dyn-d01")
    path.write_bytes(original)
    (path.parent / "gold.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="input_inventory"):
        module.load_pack(root, "dyn-d01")


def test_gold_hash_or_unknown_pack_is_rejected(tmp_path, font_path):
    module = corpus_module()
    root = tmp_path / "corpus"
    module.build_corpus(root, font_path=font_path, pack_ids=["dyn-d01"])
    path = root / "gold/dyn-d01.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    value["cells"][0]["expected_business"] = "不满足"
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError, match="config_or_gold_integrity"):
        module.load_pack(root, "dyn-d01")
    with pytest.raises(ValueError, match="pack_selection"):
        module.load_pack(root, "../other")


def test_pack_selection_and_missing_font_fail_before_creation(tmp_path):
    module = corpus_module()
    root = tmp_path / "corpus"
    with pytest.raises(ValueError, match="pack_selection"):
        module.build_corpus(root, font_path=Path("missing.ttf"), pack_ids=["dyn-d01", "dyn-d01"])
    assert not root.exists()
    with pytest.raises(ValueError, match="font_unavailable"):
        module.build_corpus(root, font_path=tmp_path / "missing.ttf")
    assert not root.exists()


def test_original_bytes_are_reproducible_across_save_times(tmp_path, font_path, monkeypatch):
    import openpyxl.writer.excel as writer

    module = corpus_module()
    roots = [tmp_path / "first", tmp_path / "later"]
    for day, root in enumerate(roots, 11):
        class SaveClock:
            @staticmethod
            def now(*_, _day=day, **_kwargs):
                return datetime(2026, 10, _day, tzinfo=UTC)

        monkeypatch.setattr(writer, "datetime", SimpleNamespace(datetime=SaveClock, timezone=SimpleNamespace(utc=UTC)))
        module.build_corpus(root, font_path=font_path, pack_ids=["dyn-d01"])
    assert module.load_pack(roots[0], "dyn-d01")[2] == module.load_pack(roots[1], "dyn-d01")[2]


def test_other_sku_page_is_present_and_excluded_from_target_scope(tmp_path, font_path):
    module = corpus_module()
    root = tmp_path / "corpus"
    module.build_corpus(root, font_path=font_path, pack_ids=["dyn-d03"])
    manifest, _, originals = module.load_pack(root, "dyn-d03")
    from evals.dynamic.metrics import read_originals

    texts, errors = read_originals(manifest, originals)
    assert not errors
    assert "DEMO-OTHER" in texts["vendor-a-spec.pdf", 2, None, None]
    assert all(b.scope.page_end == 1 for b in manifest.bindings if b.document.endswith(".pdf"))


@pytest.mark.parametrize("pack_id,criterion_id", [("dyn-d01", "warranty_doc"), ("dyn-d06", "trace"),
                                               ("dyn-d06", "test"), ("dyn-d09", "test")])
def test_presence_gold_requires_material_body_beyond_supplier_attached_claim(tmp_path, font_path, pack_id, criterion_id):
    module = corpus_module()
    root = tmp_path / "corpus"
    module.build_corpus(root, font_path=font_path, pack_ids=[pack_id])
    manifest, gold, originals = module.load_pack(root, pack_id)
    expected = next(c for c in gold.cells if c.criterion_id == criterion_id and c.supplier_handle == "vendor-a")
    # A supplier's "attached" claim must not replace the actual attached body.
    sources, refs = [], []
    for group in expected.evidence_groups:
        anchor = next((a for a in group.alternatives if not a.text.startswith("附件材料正文")), None)
        if anchor is None:
            continue
        ref = f"source-{group.group_id}"
        refs.append(ref)
        document = next(d for d in manifest.documents if d.filename == anchor.document)
        sources.append({**anchor.model_dump(mode="json"), "evidence_id": ref, "sha256": document.sha256})
    cell = {"pack_id": manifest.pack_id, "supplier_handle": expected.supplier_handle,
            "item_id": expected.item_id, "criterion_id": expected.criterion_id,
            "processing_status": "complete", "business_status": "满足", "is_formal": True,
            "needs_review": False, "evidence_refs": refs}
    coverage = [{**scope.model_dump(mode="json"), "processing_status": "complete"}
                for scope in expected.required_coverage]
    observation = {"pack_id": manifest.pack_id, "run_status": "completed", "sources": sources,
                   "coverage": coverage, **{name: [cell] for name in
                       ("api_cells", "visible_cells", "export_cells", "xlsx_export_cells")}}
    from evals.dynamic.metrics import score_pack
    measured = next(c for c in score_pack(manifest, gold, observation, originals)["cells"]
                    if c["key"][-1] == criterion_id and c["key"][1] == "vendor-a")
    assert measured["status_correct"] is True
    assert measured["evidence_correct"] is False
    assert measured["strict_correct"] is False
    assert measured["missing_evidence_groups"] == ["supplier-material"]
    body = next(g for g in expected.evidence_groups if g.group_id == "supplier-material").alternatives[0]
    document = next(d for d in manifest.documents if d.filename == body.document)
    sources.append({**body.model_dump(mode="json"), "evidence_id": "body", "sha256": document.sha256})
    refs.append("body")
    measured = next(c for c in score_pack(manifest, gold, observation, originals)["cells"]
                    if c["key"][-1] == criterion_id and c["key"][1] == "vendor-a")
    assert measured["evidence_correct"] is True
    assert measured["strict_correct"] is True
