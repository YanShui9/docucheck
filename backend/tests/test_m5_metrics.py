"""Evaluation errors must not disappear from the planned denominator."""

from copy import deepcopy

import pytest

from evals.candidate_corpus import InputManifest, ProposedGold


def example():
    from backend.app.field_contract import FieldKey

    manifest = InputManifest.model_validate({
        "schema_version": 1, "pack_id": "fixture", "dataset_kind": "synthetic_candidate",
        "suppliers": [{"id": "vendor-a", "model": "Model-1"}],
        "question": "完整审查全部供应商的八项指标",
        "documents": [{"filename": name, "role": role, "supplier": sid,
                       "model": None if sid is None else "Model-1",
                       "sha256": digest * 64, "bytes": 100, "pages": pages}
                      for name, role, sid, digest, pages in (
                          ("requirements.pdf", "requirement", None, "a", 1),
                          ("quote.xlsx", "quote", "vendor-a", "b", 0),
                          ("spec.pdf", "spec", "vendor-a", "c", 1))],
    })
    rows, checks, sources = [], [], []
    for field in FieldKey:
        status = "资料冲突" if field == "memory" else "满足"
        anchors = [{"document": "requirements.pdf", "text": f"需求 {field}", "page": 1},
                   {"document": "spec.pdf", "text": f"供货 {field}", "page": 1}]
        ids = [f"r-{field}", f"s-{field}"]
        if field == "memory":
            anchors.append({"document": "quote.xlsx", "text": "另一内存承诺",
                            "sheet": "报价", "cell": "B2"})
            ids.append("q-memory")
        for evidence_id, anchor in zip(ids, anchors, strict=True):
            sources.append({"evidence_id": evidence_id, "document_id": anchor["document"],
                            "document_name": anchor["document"], "source_text": anchor["text"],
                            "page": anchor.get("page"), "sheet": anchor.get("sheet"),
                            "cell": anchor.get("cell")})
        rows.append({"supplier": "vendor-a", "model": "Model-1", "field_key": field,
                     "processing_status": "complete", "business_status": status,
                     "rationale": "Test expectation only; not business input.",
                     "evidence": anchors, "absence_documents": []})
        checks.append({"supplier": "vendor-a", "field_key": field,
                       "snapshot_id": "snapshot", "processing_status": "complete",
                       "business_status": status, "is_formal": True,
                       "evidence_refs": ids, "reason_evidence_refs": []})
    gold = ProposedGold.model_validate({"schema_version": 1, "pack_id": "fixture",
                                       "review_state": "pending_independent_review",
                                       "author": "agent_proposed", "results": rows})
    observation = {"pack_id": "fixture", "stage": "finished", "error_code": None,
                   "case_id": "case", "snapshot_id": "snapshot", "run_id": "run",
                   "run": {"id": "run", "case_id": "case", "snapshot_id": "snapshot",
                           "status": "completed", "is_current": True,
                           "answer_text": "\n".join(
                               f"vendor-a · {name}：{status}。" for name, status in (
                                   ("内存", "资料冲突"), ("存储", "满足"), ("屏幕尺寸", "满足"),
                                   ("重量", "满足"), ("质保", "满足"), ("采购数量", "满足"),
                                   ("单价", "满足"), ("交付期限", "满足")))},
                   "documents": [{"id": d.filename, "original_name": d.filename,
                                  "source_sha256": d.sha256, "role": d.role,
                                  "supplier_handle": d.supplier, "model_name": d.model,
                                  "parse_status": "complete"} for d in manifest.documents],
                   "export": {"schema_version": 1, "result": {
                       "run_id": "run", "case_id": "case", "snapshot_id": "snapshot",
                       "run_status": "completed", "is_current": True, "checks": checks},
                              "sources": sources}}
    return manifest, gold, observation


def score(observation):
    from evals.m5_metrics import score_pack

    manifest, gold, _ = example()
    texts = {("requirements.pdf", 1, None, None): "\n".join(f"需求 {r.field_key}" for r in gold.results),
             ("spec.pdf", 1, None, None): "\n".join(f"供货 {r.field_key}" for r in gold.results),
             ("quote.xlsx", None, "报价", "B2"): "另一内存承诺"}
    return score_pack(manifest, gold, observation, source_texts=texts)


def test_all_required_sources_and_visible_states_are_measured():
    report = score(example()[2])
    assert report["status_accuracy"] == {"numerator": 8, "denominator": 8}
    assert report["necessary_evidence"] == {"numerator": 8, "denominator": 8}
    assert report["citation_errors"] == {"numerator": 0, "denominator": 17}
    assert report["task_success"] is True
    assert report["formal_independent_acceptance"] is False


def test_missing_and_failed_runs_keep_every_expected_cell():
    for observation in ({"pack_id": "fixture", "stage": "parse", "error_code": "parser_failed"},
                        {**example()[2], "export": None}):
        report = score(observation)
        assert report["status_accuracy"] == {"numerator": 0, "denominator": 8}
        assert report["necessary_evidence"] == {"numerator": 0, "denominator": 8}
        assert len(report["cells"]) == 8
        assert report["task_success"] is False


@pytest.mark.parametrize("mutation", ["duplicate", "old", "partial", "wrong_snapshot", "visible_wrong"])
def test_invalid_results_do_not_become_correct_cells(mutation):
    observation = deepcopy(example()[2])
    result = observation["export"]["result"]
    if mutation == "duplicate":
        result["checks"].append(deepcopy(result["checks"][0]))
    elif mutation == "old":
        result["is_current"] = False
    elif mutation == "partial":
        result["run_status"] = "partial"
    elif mutation == "wrong_snapshot":
        result["checks"][0]["snapshot_id"] = "older"
    else:
        observation["run"]["answer_text"] = observation["run"]["answer_text"].replace(
            "内存：资料冲突", "内存：满足")
    report = score(observation)
    assert report["status_accuracy"]["numerator"] < 8
    assert report["status_accuracy"]["denominator"] == 8
    assert report["task_success"] is False


def test_conflict_requires_both_supplier_sources_in_the_same_check():
    observation = deepcopy(example()[2])
    observation["export"]["result"]["checks"][0]["evidence_refs"].remove("q-memory")
    report = score(observation)
    assert report["necessary_evidence"] == {"numerator": 7, "denominator": 8}
    assert report["task_success"] is False


@pytest.mark.parametrize("mutation", ["cross_supplier", "wrong_model", "wrong_page", "boolean_page", "invented_text", "unknown_id"])
def test_invalid_citations_are_counted(mutation):
    observation = deepcopy(example()[2])
    if mutation == "cross_supplier":
        observation["documents"][2]["supplier_handle"] = "vendor-b"
    elif mutation == "wrong_model":
        observation["documents"][2]["model_name"] = "Model-2"
    elif mutation == "wrong_page":
        observation["export"]["sources"][1]["page"] = 2
    elif mutation == "boolean_page":
        observation["export"]["sources"][1]["page"] = True
    elif mutation == "invented_text":
        observation["export"]["sources"][1]["source_text"] = "不存在的原文"
    else:
        observation["export"]["result"]["checks"][0]["reason_evidence_refs"] = ["unknown"]
    report = score(observation)
    assert report["citation_errors"]["numerator"] > 0
    assert report["task_success"] is False


def test_wrong_meet_claim_is_dangerous_even_with_a_partial_run():
    observation = deepcopy(example()[2])
    result = observation["export"]["result"]
    result["checks"][0]["business_status"] = "满足"
    result["run_status"] = "partial"
    observation["run"]["answer_text"] = observation["run"]["answer_text"].replace(
        "内存：资料冲突", "内存：满足")
    report = score(observation)
    assert report["dangerous_meets"] == 1


def test_aggregate_includes_unattempted_packs_and_has_no_zero_citation_success():
    from evals.m5_metrics import aggregate_scores

    successful = score(example()[2])
    missing = score({"pack_id": "fixture", "stage": "not_started"})
    report = aggregate_scores([successful, missing])
    assert report["status_accuracy"] == {"numerator": 8, "denominator": 16}
    assert report["agent_tasks"] == {"numerator": 1, "denominator": 2}
    assert missing["citation_error_rate"] is None
    assert missing["confusion_matrix"]["资料冲突"]["未完成或无正式结果"] == 1


def test_malformed_export_is_a_failed_task_with_the_original_denominator():
    observation = deepcopy(example()[2])
    observation["export"]["sources"] = [None]
    report = score(observation)
    assert report["status_accuracy"] == {"numerator": 0, "denominator": 8}
    assert report["necessary_evidence"] == {"numerator": 0, "denominator": 8}
    assert "invalid_observation" in report["errors"]


def test_insufficient_evidence_requires_complete_applicable_documents():
    from evals.m5_metrics import score_pack

    manifest, gold, observation = example()
    rows = gold.model_dump()["results"]
    rows[0].update(business_status="证据不足", evidence=rows[0]["evidence"][:1],
                   absence_documents=["quote.xlsx", "spec.pdf"])
    gold = ProposedGold.model_validate({**gold.model_dump(), "results": rows})
    observation["export"]["result"]["checks"][0]["business_status"] = "证据不足"
    observation["run"]["answer_text"] = observation["run"]["answer_text"].replace("内存：资料冲突", "内存：证据不足")
    observation["documents"][2]["parse_status"] = "partial"
    texts = {("requirements.pdf", 1, None, None): "\n".join(f"需求 {r.field_key}" for r in gold.results),
             ("spec.pdf", 1, None, None): "\n".join(f"供货 {r.field_key}" for r in gold.results),
             ("quote.xlsx", None, "报价", "B2"): "另一内存承诺"}
    report = score_pack(manifest, gold, observation, source_texts=texts)
    assert report["status_accuracy"] == {"numerator": 7, "denominator": 8}
    assert report["necessary_evidence"]["denominator"] == 7
