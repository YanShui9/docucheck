"""Development-only metrics use real retrieval and rule boundaries, without an API call."""

import json
from pathlib import Path

import pytest

from evals.m2_development import evaluate_dataset, score_recall_at_12

DATASET = Path(__file__).resolve().parents[2] / "evals" / "cases" / "lexical-001.json"


def test_recall_counts_distinct_relevant_sources_once_per_query() -> None:
    score = score_recall_at_12(
        {"requirement#memory", "quote#memory"},
        [
            {"evidence_refs": ["requirement#memory", "requirement#memory"]},
            {"evidence_refs": ["irrelevant#memory"]},
        ],
    )
    assert score == {"numerator": 1, "denominator": 2}


def test_development_report_shows_misses_and_guardrail_denominators() -> None:
    report = evaluate_dataset(DATASET)

    assert report["dataset_kind"] == "synthetic_development"
    assert report["retrieval"]["recall_at_12"] == {"numerator": 17, "denominator": 17}
    queries = {item["id"]: item for item in report["retrieval"]["queries"]}
    assert all(not query["missed_locations"] for query in queries.values())
    assert queries["no_hit"]["hit_count"] == 0
    assert queries["no_hit"]["denominator"] == 0
    assert report["retrieval"]["supplier_scope"] == {"numerator": 9, "denominator": 9}
    for name in (
        "invalid_reference_rejected",
        "partial_coverage_blocks_status",
        "no_hit_blocks_absence",
    ):
        assert report["guardrails"][name] == {"numerator": 1, "denominator": 1}


def test_unknown_gold_location_is_rejected_instead_of_becoming_a_miss(tmp_path: Path) -> None:
    source = json.loads(DATASET.read_text(encoding="utf-8"))
    source["queries"][0]["relevant"].append("invented.pdf#cell")
    bad_dataset = tmp_path / "bad-development-case.json"
    bad_dataset.write_text(json.dumps(source), encoding="utf-8")

    with pytest.raises(ValueError, match="unknown_gold_location"):
        evaluate_dataset(bad_dataset)


def test_gold_annotations_do_not_change_input_snapshot(tmp_path: Path) -> None:
    source = json.loads(DATASET.read_text(encoding="utf-8"))
    changed_gold = tmp_path / "different-gold.json"
    source["queries"][0]["relevant"] = ["requirements.pdf#memory"]
    changed_gold.write_text(json.dumps(source), encoding="utf-8")

    original = evaluate_dataset(DATASET)
    revised = evaluate_dataset(changed_gold)
    assert original["snapshot_id"] == revised["snapshot_id"]
    assert original["retrieval"]["queries"][0]["hit_count"] == revised["retrieval"]["queries"][0]["hit_count"]
