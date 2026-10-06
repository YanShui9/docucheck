"""Fake declarations below exercise the gate, never seal the actual corpus."""

import csv
import json
import os
from pathlib import Path

import pytest

from evals.candidate_corpus import build_corpus
from evals.corpus_review import freeze_corpus, prepare_review, review_report


@pytest.fixture(scope="module")
def corpus(tmp_path_factory):
    font = Path(os.environ.get("DOCUCHECK_CJK_FONT", "C:/Windows/Fonts/simhei.ttf"))
    if not font.is_file():
        pytest.skip("set DOCUCHECK_CJK_FONT to a Chinese TrueType font")
    root = tmp_path_factory.mktemp("review-assets") / "corpus"
    build_corpus(root, font_path=font)
    return root


@pytest.fixture
def bundle(corpus, tmp_path):
    path = tmp_path / "review"
    prepare_review(corpus, path)
    return path


def _rows(path):
    with (path / "review.csv").open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def _save_rows(path, rows):
    with (path / "review.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)


def _confirm_fixture(path):
    rows = _rows(path)
    for row in rows:
        row.update(review_decision="accepted", reviewed_status=row["proposed_status"],
                   reviewer="Fixture reviewer (not real)", reviewed_on="2026-10-01")
    _save_rows(path, rows)
    confirmation_path = path / "confirmation.json"
    confirmation = json.loads(confirmation_path.read_text(encoding="utf-8"))
    confirmation.update(state="confirmed", reviewer="Fixture reviewer (not real)", reviewed_on="2026-10-01",
                        independent_human_review=True, author_seen_acknowledged=True,
                        holdout_used_for_tuning=False,
                        contamination_history="Fixture only: no real human review or real model tuning.")
    confirmation_path.write_text(json.dumps(confirmation), encoding="utf-8")


def test_blank_review_does_not_freeze(corpus, bundle):
    report = review_report(corpus, bundle)
    assert report["pending_rows"] == 104
    assert report["reviewed_packs"] == report["sealed_packs"] == 0
    assert report["ready_to_freeze"] is False
    assert report["errors"] == []
    with pytest.raises(ValueError, match="review_not_ready"):
        freeze_corpus(corpus, bundle)
    assert not (bundle / "freeze.json").exists()


def test_fixture_confirmation_and_freeze_are_verifiable(corpus, bundle):
    _confirm_fixture(bundle)
    report = review_report(corpus, bundle)
    assert report["ready_to_freeze"] is True
    assert report["reviewed_packs"] == 12
    assert report["sealed_packs"] == 0
    freeze_corpus(corpus, bundle)
    report = review_report(corpus, bundle, seal_path=bundle / "freeze.json")
    assert report["errors"] == []
    assert report["sealed_packs"] == 6
    assert report["business_acceptance_measured"] is False
    with pytest.raises(FileExistsError):
        freeze_corpus(corpus, bundle)


@pytest.mark.parametrize("mutation,code", [
    ("missing", "review_coverage"), ("duplicate", "review_coverage"),
    ("status", "review_status"), ("changed", "gold_revision_required"),
    ("reviewer", "review_identity"), ("date", "review_date"),
    ("independent", "confirmation_required"), ("tuning", "contaminated_holdout"),
    ("holdout", "holdout_scope"), ("snapshot", "review_version"),
])
def test_review_gate_rejects_invalid_declarations(corpus, bundle, mutation, code):
    _confirm_fixture(bundle)
    rows = _rows(bundle)
    confirmation_path = bundle / "confirmation.json"
    confirmation = json.loads(confirmation_path.read_text(encoding="utf-8"))
    if mutation == "missing":
        rows = rows[1:]
    elif mutation == "duplicate":
        rows[1] = dict(rows[0])
    elif mutation == "status":
        rows[0]["reviewed_status"] = "invented status"
    elif mutation == "changed":
        rows[0]["review_decision"] = "changed"
        rows[0]["reviewed_status"] = "满足"
        rows[0]["comment"] = "Fixture proposes a change without revising gold"
    elif mutation == "reviewer":
        rows[0]["reviewer"] = "someone else"
    elif mutation == "date":
        rows[0]["reviewed_on"] = "2026-02-30"
    elif mutation == "independent":
        confirmation["independent_human_review"] = False
    elif mutation == "tuning":
        confirmation["holdout_used_for_tuning"] = True
    elif mutation == "holdout":
        confirmation["holdout_packs"] = ["m5-c01"]
    elif mutation == "snapshot":
        confirmation["corpus_sha256"] = "0" * 64
    _save_rows(bundle, rows)
    confirmation_path.write_text(json.dumps(confirmation), encoding="utf-8")
    report = review_report(corpus, bundle)
    assert code in {e["code"] for e in report["errors"]}
    assert report["ready_to_freeze"] is False
    if mutation == "independent":
        assert report["reviewed_packs"] == 0


@pytest.mark.parametrize("target", ["review.csv", "confirmation.json", "snapshot.json", "freeze.json"])
def test_changed_review_or_freeze_invalidates_record(corpus, bundle, target):
    _confirm_fixture(bundle)
    freeze_corpus(corpus, bundle)
    with (bundle / target).open("ab") as stream:
        stream.write(b"\n ")
    report = review_report(corpus, bundle, seal_path=bundle / "freeze.json")
    assert report["sealed_packs"] == 0
    assert "seal_changed" in {e["code"] for e in report["errors"]}


def test_prepare_refuses_overwrite_and_contains_no_secrets(corpus, bundle):
    with pytest.raises(FileExistsError):
        prepare_review(corpus, bundle)
    snapshot = json.loads((bundle / "snapshot.json").read_text(encoding="utf-8"))
    assert all(not f["path"].startswith(".env") for f in snapshot["code_baseline"]["files"])
    assert all(r["reviewed_status"] == "" for r in _rows(bundle))


def test_confirmation_cannot_skip_one_pending_row(corpus, bundle):
    _confirm_fixture(bundle)
    rows = _rows(bundle)
    rows[0]["review_decision"] = "pending"
    _save_rows(bundle, rows)
    report = review_report(corpus, bundle)
    assert "review_incomplete" in {e["code"] for e in report["errors"]}
    assert report["ready_to_freeze"] is False


def test_seal_path_is_not_silently_replaced_by_same_filename(corpus, bundle):
    _confirm_fixture(bundle)
    freeze_corpus(corpus, bundle)
    outside = bundle.parent / "outside" / "freeze.json"
    report = review_report(corpus, bundle, seal_path=outside)
    assert report["sealed_packs"] == 0
    assert "seal_changed" in {e["code"] for e in report["errors"]}


def test_source_tampering_invalidates_a_fixture_freeze(corpus, bundle, tmp_path):
    import shutil
    _confirm_fixture(bundle)
    freeze_corpus(corpus, bundle)
    modified = tmp_path / "modified"
    shutil.copytree(corpus, modified)
    (modified / "inputs/m5-c01/requirements.pdf").write_bytes(b"tampered")
    report = review_report(modified, bundle, seal_path=bundle / "freeze.json")
    assert report["sealed_packs"] == 0
    assert "corpus_invalid" in {e["code"] for e in report["errors"]}
