"""Candidate corpora are assets and proposed labels, never business scores."""

import json
import os
from pathlib import Path

import pytest

from evals.candidate_corpus import build_corpus, runtime_manifest, validate_corpus

FONT = Path(os.environ.get("DOCUCHECK_CJK_FONT", "C:/Windows/Fonts/simhei.ttf"))


@pytest.fixture(scope="module")
def corpus(tmp_path_factory):
    if not FONT.is_file():
        pytest.skip("set DOCUCHECK_CJK_FONT to an available Chinese TrueType font")
    root = tmp_path_factory.mktemp("candidates") / "corpus"
    build_corpus(root, font_path=FONT)
    return root


def test_corpus_counts_and_no_self_sealing(corpus):
    report = validate_corpus(corpus)
    assert report["candidate_packs"] == 12
    assert report["supplier_field_cases"] == 104
    assert report["holdout_candidates"] == 6
    assert report["errors"] == []
    assert report["reviewed_packs"] == report["sealed_packs"] == 0
    assert report["business_acceptance_measured"] is False


def test_runtime_manifest_excludes_answers_and_gold_paths(corpus):
    manifest = runtime_manifest(corpus / "inputs" / "m5-c01")
    text = json.dumps(manifest, ensure_ascii=False)
    for forbidden in ("gold", "expected", "business_status", "rationale", "anchors"):
        assert forbidden not in text
    assert len(manifest["documents"]) == 5


def test_rebuild_is_identical_and_existing_directory_refused(corpus, tmp_path):
    rebuilt = tmp_path / "rebuilt"
    build_corpus(rebuilt, font_path=FONT)
    for source in corpus.rglob("*"):
        if source.is_file():
            assert source.read_bytes() == (rebuilt / source.relative_to(corpus)).read_bytes()
    with pytest.raises(FileExistsError):
        build_corpus(corpus, font_path=FONT)


@pytest.mark.parametrize("mutation,code", [
    ("cohort", "cohort_coverage"),
    ("cell", "evidence_location"),
    ("pdf", "asset_structure"),
    ("index", "index_schema"),
])
def test_incomplete_or_malformed_corpus_is_not_ready(corpus, tmp_path, mutation, code):
    import shutil
    from hashlib import sha256
    root = tmp_path / "corpus"
    shutil.copytree(corpus, root)
    index_path = root / "corpus.json"
    index = json.loads(index_path.read_text(encoding="utf-8"))
    if mutation == "cohort":
        index["packs"] = index["packs"][:-1]
    elif mutation == "cell":
        gold_path = root / "gold/m5-c01.json"
        gold = json.loads(gold_path.read_text(encoding="utf-8"))
        anchor = next(a for r in gold["results"] for a in r["evidence"] if a["document"].endswith(".xlsx"))
        anchor["cell"] = "Z999"
        gold_path.write_text(json.dumps(gold, ensure_ascii=False), encoding="utf-8")
    elif mutation == "pdf":
        # Update asset hash so structural validation, not only checksum, is exercised.
        path = root / "inputs/m5-c01/requirements.pdf"
        path.write_bytes(b"%PDF-1.7\nnot a valid document")
        manifest_path = path.parent / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["documents"][0]["sha256"] = sha256(path.read_bytes()).hexdigest()
        manifest["documents"][0]["bytes"] = path.stat().st_size
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    elif mutation == "index":
        index["packs"] = "not a list"
    index_path.write_text(json.dumps(index, ensure_ascii=False), encoding="utf-8")
    report = validate_corpus(root)
    assert code in {e["code"] for e in report["errors"]}
    assert report["candidate_preparation_ready"] is False


def test_runtime_only_needs_input_directory(corpus, tmp_path):
    import shutil
    pack = tmp_path / "input-only"
    shutil.copytree(corpus / "inputs/m5-c01", pack)
    assert runtime_manifest(pack)["pack_id"] == "m5-c01"


def test_review_template_is_pending_and_has_every_case(corpus):
    import csv
    with (corpus / "review/review.csv").open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == 104
    assert all(r["review_decision"] == "pending" and not r["reviewer"] for r in rows)
    attestation = json.loads((corpus / "review/attestation-template.json").read_text(encoding="utf-8"))
    assert attestation["state"] == "pending"
    assert "author" in (corpus / "corpus.json").read_text(encoding="utf-8")


def test_all_candidates_pass_actual_upload_preflight(corpus, tmp_path):
    from fastapi import UploadFile

    from backend.app.api.upload import UploadStorage
    from backend.app.parsing import parse_xlsx
    storage = UploadStorage(tmp_path / "uploads")
    pdf_count = xlsx_count = 0
    for directory in sorted((corpus / "inputs").iterdir()):
        manifest = runtime_manifest(directory)
        assert sum(d["pages"] for d in manifest["documents"]) <= 30
        for document in manifest["documents"]:
            with (directory / document["filename"]).open("rb") as stream:
                upload = storage.save(UploadFile(file=stream, filename=document["filename"]))
                assert upload.source_sha256 == document["sha256"]
                if upload.path.suffix == ".xlsx":
                    assert parse_xlsx(upload.path, document_id=upload.document_id).status == "complete"
                    xlsx_count += 1
                else:
                    assert upload.pdf_pages == document["pages"]
                    pdf_count += 1
                storage.discard(upload)
    assert (pdf_count, xlsx_count) == (25, 13)


def test_gold_directory_cannot_escape_corpus_root(corpus, tmp_path):
    import shutil
    root = tmp_path / "corpus"
    outside = tmp_path / "outside"
    outside.mkdir()
    shutil.copytree(corpus / "inputs", root / "inputs")
    shutil.copy(corpus / "corpus.json", root / "corpus.json")
    for gold in (corpus / "gold").glob("*.json"):
        shutil.copy(gold, outside / gold.name)
    try:
        (root / "gold").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks are unavailable on this host")
    report = validate_corpus(root)
    assert "gold_schema" in {e["code"] for e in report["errors"]}


@pytest.mark.parametrize("raw", [None, {"documents": ["not a document"]}])
def test_malformed_manifest_has_a_structured_error(corpus, tmp_path, raw):
    import shutil
    root = tmp_path / "corpus"
    shutil.copytree(corpus, root)
    (root / "inputs/m5-c01/manifest.json").write_text(json.dumps(raw), encoding="utf-8")
    report = validate_corpus(root)
    assert "input_schema" in {e["code"] for e in report["errors"]}
    assert report["candidate_preparation_ready"] is False


@pytest.mark.parametrize("mutation,code", [
    ("hash", "asset_hash"),
    ("path", "asset_path"),
    ("page", "evidence_location"),
    ("text", "evidence_text"),
    ("supplier", "evidence_scope"),
    ("duplicate", "field_coverage"),
    ("absence", "absence_scope"),
    ("conflict", "conflict_sources"),
    ("family", "family_split"),
    ("leak", "input_schema"),
])
def test_rejects_corrupted_assets_labels_and_splits(corpus, tmp_path, mutation, code):
    import shutil
    root = tmp_path / "corpus"
    shutil.copytree(corpus, root)
    index_path = root / "corpus.json"
    index = json.loads(index_path.read_text(encoding="utf-8"))
    pack_path = root / "inputs" / "m5-c01" / "manifest.json"
    pack = json.loads(pack_path.read_text(encoding="utf-8"))
    gold_path = root / "gold" / "m5-c01.json"
    gold = json.loads(gold_path.read_text(encoding="utf-8"))
    if mutation == "hash":
        (pack_path.parent / pack["documents"][0]["filename"]).write_bytes(b"tampered")
    elif mutation == "path":
        pack["documents"][0]["filename"] = "../../outside.pdf"
    elif mutation == "page":
        gold["results"][0]["evidence"][0]["page"] = 999
    elif mutation == "text":
        gold["results"][0]["evidence"][0]["text"] = "source never said this"
    elif mutation == "supplier":
        gold["results"][0]["evidence"][1]["document"] = "vendor-b-spec.pdf"
    elif mutation == "duplicate":
        gold["results"][1]["field_key"] = gold["results"][0]["field_key"]
    elif mutation == "absence":
        missing = next(r for r in gold["results"] if r["business_status"] == "证据不足")
        missing["absence_documents"] = []
    elif mutation == "conflict":
        conflict = next(r for r in gold["results"] if r["business_status"] == "资料冲突")
        conflict["evidence"] = conflict["evidence"][:2]
    elif mutation == "family":
        index["packs"][6]["family"] = index["packs"][0]["family"]
    elif mutation == "leak":
        pack["expected_status"] = "满足"
    pack_path.write_text(json.dumps(pack, ensure_ascii=False), encoding="utf-8")
    gold_path.write_text(json.dumps(gold, ensure_ascii=False), encoding="utf-8")
    index_path.write_text(json.dumps(index, ensure_ascii=False), encoding="utf-8")
    assert code in {e["code"] for e in validate_corpus(root)["errors"]}
