"""Temporary AI declarations test version checks, never a human review claim."""

import json
import shutil
from hashlib import sha256

import pytest

from backend.tests import test_candidate_corpus
from evals.m5_run import development_baseline, prepare_protocol

corpus = test_candidate_corpus.corpus


def declaration(root, path):
    index = json.loads((root / "corpus.json").read_text(encoding="utf-8"))
    rows = []
    for pack in index["packs"]:
        gold = json.loads((root / "gold" / f"{pack['id']}.json").read_text(encoding="utf-8"))
        rows.extend({"pack": pack["id"], "supplier": r["supplier"], "field": r["field_key"],
                     "reviewed_status": r["business_status"], "decision": "accepted_for_development",
                     "source_reason": "Temporary schema fixture, not an actual review."} for r in gold["results"])
    value = {"kind": "ai_source_review_of_agent_authored_candidates",
             "independent_human_review": False, "formal_independent_acceptance": False,
             "known_author_exposure": True, "independently_sealed_packs": 0,
             "corpus_sha256": sha256((root / "corpus.json").read_bytes()).hexdigest(),
             "packs": index["packs"], "reviewed_rows": rows}
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    return value


def test_development_protocol_keeps_provenance_and_fixed_five_file_ten_runs(corpus, tmp_path):
    path = tmp_path / "ai-fixture.json"
    declaration(corpus, path)
    baseline = development_baseline(corpus, path)
    assert baseline["reviewed_rows"] == 104
    assert baseline["formal_independent_acceptance"] is False
    assert baseline["independently_sealed_packs"] == 0
    quality = prepare_protocol(corpus, path)
    assert quality["planned_tasks"] == 12
    assert sum(len(task["input"]["suppliers"]) * 8 for task in quality["tasks"]) == 104
    performance = prepare_protocol(corpus, path, performance=True)
    assert performance["planned_tasks"] == 10
    assert all(len(task["input"]["documents"]) == 5 for task in performance["tasks"])
    assert {task["pack_id"] for task in performance["tasks"]} == {"m5-c01"}
    for protocol in (quality, performance):
        assert protocol["maximum_input_tokens"] is None and protocol["maximum_output_tokens"] is None
        assert protocol["token_limit_source"] == "runtime_configuration_not_verified"
        assert protocol["maximum_agent_requests"] == protocol["planned_tasks"] * 32


@pytest.mark.parametrize("mutation", ["human", "missing", "duplicate", "null_rows"])
def test_forged_independence_or_incomplete_review_is_refused(corpus, tmp_path, mutation):
    path = tmp_path / "ai-fixture.json"
    value = declaration(corpus, path)
    if mutation == "human":
        value["independent_human_review"] = True
    elif mutation == "missing":
        value["reviewed_rows"].pop()
    elif mutation == "duplicate":
        value["reviewed_rows"].append(value["reviewed_rows"][0])
    else:
        value["reviewed_rows"] = None
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError):
        development_baseline(corpus, path)


def test_updated_gold_cannot_use_the_old_ai_review(corpus, tmp_path):
    root = tmp_path / "changed"
    shutil.copytree(corpus, root)
    review = tmp_path / "ai-fixture.json"
    declaration(root, review)
    gold_path = root / "gold/m5-c01.json"
    gold = json.loads(gold_path.read_text(encoding="utf-8"))
    gold["results"][0]["rationale"] += " New version."
    gold_path.write_text(json.dumps(gold, ensure_ascii=False), encoding="utf-8")
    index_path = root / "corpus.json"
    index = json.loads(index_path.read_text(encoding="utf-8"))
    index["packs"][0]["gold_sha256"] = sha256(gold_path.read_bytes()).hexdigest()
    index_path.write_text(json.dumps(index, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError, match="development_review_version_or_provenance"):
        development_baseline(root, review)
