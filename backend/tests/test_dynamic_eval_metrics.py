"""Failure outcomes cannot shrink dynamic evaluation denominators."""

from copy import deepcopy
from importlib import import_module, util

import pytest

from backend.tests.test_dynamic_eval_contracts import contracts, fixture_data


def example():
    manifest, gold, originals = fixture_data()
    sources, cells, coverage = [], [], []
    for row in gold["cells"]:
        refs = []
        for group in row["evidence_groups"]:
            anchor = deepcopy(group["alternatives"][0])
            evidence_id = f"{row['criterion_id']}-{group['group_id']}"
            digest = next(d["sha256"] for d in manifest["documents"] if d["filename"] == anchor["document"])
            sources.append({"evidence_id": evidence_id, "sha256": digest, **anchor})
            refs.append(evidence_id)
        cells.append({**{key: row[key] for key in ("pack_id", "supplier_handle", "item_id", "criterion_id")},
                      "processing_status": "complete", "business_status": row["expected_business"],
                      "is_formal": True, "needs_review": False, "evidence_refs": refs})
    for entry in gold["cells"][0]["required_coverage"]:
        coverage.append({**entry, "processing_status": "complete"})
    observation = {"pack_id": manifest["pack_id"], "run_status": "completed", "api_cells": cells,
                   "visible_cells": deepcopy(cells), "export_cells": deepcopy(cells),
                   "xlsx_export_cells": deepcopy(cells),
                   "sources": sources, "coverage": coverage}
    return manifest, gold, observation, originals


def score(observation=None, *, originals=None, gold=None):
    assert util.find_spec("evals.dynamic.metrics") is not None, "dynamic metrics missing"
    module = import_module("evals.dynamic.metrics")
    schema = contracts()
    manifest, expected, successful, files = example()
    return module.score_pack(schema.Manifest.model_validate(manifest), schema.Gold.model_validate(gold or expected),
                             successful if observation is None else observation,
                             files if originals is None else originals)


def test_all_dynamic_cells_and_all_evidence_groups_are_measured():
    result = score()
    assert result["status_accuracy"] == {"numerator": 3, "denominator": 3}
    assert result["necessary_evidence"] == {"numerator": 2, "denominator": 2}
    assert result["absence_coverage"] == {"numerator": 1, "denominator": 1}
    assert result["citation_errors"] == {"numerator": 0, "denominator": 6}
    assert result["task_success"] is True
    assert result["formal_independent_acceptance"] is False


def separated_baseline_observation():
    manifest, _, observation, _ = example()
    definition = deepcopy(manifest["checklist_definition"])
    buyer_ids = {source["evidence_id"] for source in observation["sources"] if source["role"] == "requirement"}
    for criterion in definition["criteria"]:
        source = next(s for s in observation["sources"] if s["evidence_id"] == criterion["criterion_id"] + "-buyer")
        criterion["source"]["context"] = {"evidence_refs": [source["evidence_id"]], "quote": source["text"]}
    for surface in ("api_cells", "visible_cells", "export_cells", "xlsx_export_cells"):
        for cell in observation[surface]:
            cell["evidence_refs"] = [ref for ref in cell["evidence_refs"] if ref not in buyer_ids]
    observation["checklist_version_id"] = "a" * 32
    observation.update(case_id="c" * 32, snapshot_id="d" * 32)
    observation["confirmed_checklist"] = {"version_id": "a" * 32, "case_id": "c" * 32,
                                         "snapshot_id": "d" * 32, "definition": definition}
    return observation


def test_confirmed_buyer_context_is_separate_from_supplier_check_citations():
    observation = separated_baseline_observation()
    result = score(observation)
    assert result["status_accuracy"] == {"numerator": 3, "denominator": 3}
    assert result["necessary_evidence"] == {"numerator": 2, "denominator": 2}
    assert result["task_success"] is True
    # Never pretend the export itself includes buyer context citations.
    assert result["citation_errors"] == {"numerator": 0, "denominator": 3}


@pytest.mark.parametrize("mutation", ["version", "case", "snapshot", "missing_context", "missing_confirmation", "mixed_refs_wrong_quote", "rule", "wrong_role", "wrong_item", "wrong_location", "invented_quote", "unrelated_ref"])
def test_confirmed_buyer_context_must_match_frozen_rule_and_verified_source(mutation):
    observation = separated_baseline_observation()
    confirmed = observation["confirmed_checklist"]
    context = confirmed["definition"]["criteria"][0]["source"]["context"]
    source = next(s for s in observation["sources"] if s["evidence_id"] == "quantity-buyer")
    if mutation == "version":
        confirmed["version_id"] = "b" * 32
    elif mutation == "case":
        confirmed["case_id"] = "e" * 32
    elif mutation == "snapshot":
        confirmed["snapshot_id"] = "e" * 32
    elif mutation == "missing_context":
        confirmed["definition"]["criteria"][0]["source"]["context"] = None
    elif mutation == "missing_confirmation":
        observation["confirmed_checklist"] = None
        # Partial new metadata is not a legacy observation; even a ref added
        # to check citations cannot recover a missing frozen confirmation.
        for surface in ("api_cells", "visible_cells", "export_cells", "xlsx_export_cells"):
            observation[surface][0]["evidence_refs"].append("quantity-buyer")
    elif mutation == "mixed_refs_wrong_quote":
        context.update(evidence_refs=["quantity-buyer", "capacity-buyer"], quote="capacity 256")
    elif mutation == "rule":
        confirmed["definition"]["criteria"][0]["rule"]["value"] = "999"
    elif mutation == "wrong_role":
        context["evidence_refs"] = ["quantity-quote"]
    elif mutation == "wrong_item":
        source["item_id"] = "fruit"
    elif mutation == "wrong_location":
        source["cell"] = "A3"
    elif mutation == "invented_quote":
        context["quote"] = "quantity 999"
    else:
        context["evidence_refs"] = ["capacity-buyer"]
    result = score(observation)
    assert result["task_success"] is False
    assert "buyer" in result["cells"][0]["missing_evidence_groups"]
    assert result["status_accuracy"] == {"numerator": 3, "denominator": 3}


@pytest.mark.parametrize("mutation", ["missing", "partial", "failed", "not_started"])
def test_unfinished_cells_keep_the_frozen_denominator(mutation):
    observation = example()[2]
    if mutation == "missing":
        observation["api_cells"].pop(0)
    elif mutation == "partial":
        observation["api_cells"][0]["processing_status"] = "partial"
    else:
        observation = {"pack_id": "dyn-fixture", "run_status": mutation}
    result = score(observation)
    assert result["status_accuracy"]["denominator"] == 3
    assert result["necessary_evidence"]["denominator"] == 2
    assert result["status_accuracy"]["numerator"] == (2 if mutation in {"missing", "partial"} else 0)
    assert result["task_success"] is False


@pytest.mark.parametrize("surface", ["api_cells", "visible_cells", "export_cells"])
@pytest.mark.parametrize("mutation", ["duplicate", "extra", "wrong_item"])
def test_duplicate_extra_and_wrong_identity_fail_strict_task(surface, mutation):
    observation = example()[2]
    cell = deepcopy(observation[surface][0])
    if mutation == "duplicate":
        observation[surface].append(cell)
    elif mutation == "extra":
        cell["criterion_id"] = "extra"
        observation[surface].append(cell)
    else:
        observation[surface][0]["item_id"] = "fruit"
    result = score(observation)
    assert result["task_success"] is False
    assert result["status_accuracy"]["denominator"] == 3
    assert result["identity_errors"]


@pytest.mark.parametrize("surface", ["api_cells", "visible_cells", "export_cells"])
def test_wrong_satisfied_claim_is_dangerous_on_any_output_surface(surface):
    observation = example()[2]
    observation[surface][1]["business_status"] = "满足"
    observation[surface][1]["is_formal"] = False
    observation["run_status"] = "partial"
    result = score(observation)
    assert result["dangerous_meets"] == 1
    assert result["dangerous_meet_surfaces"][surface] == 1
    assert result["task_success"] is False


def test_conflict_needs_every_required_source_group():
    observation = example()[2]
    observation["api_cells"][1]["evidence_refs"].remove("capacity-spec")
    result = score(observation)
    assert result["necessary_evidence"] == {"numerator": 1, "denominator": 2}
    assert result["cells"][1]["missing_evidence_groups"] == ["spec"]


def test_equivalent_anchor_alternatives_satisfy_one_group():
    manifest, gold, observation, originals = example()
    alternate = deepcopy(gold["cells"][0]["evidence_groups"][1]["alternatives"][0])
    alternate.update(document="spec.xlsx", role="spec")
    gold["cells"][0]["evidence_groups"][1]["alternatives"].append(alternate)
    source = next(s for s in observation["sources"] if s["evidence_id"] == "quantity-quote")
    source.update(alternate)
    source["sha256"] = next(d["sha256"] for d in manifest["documents"] if d["filename"] == "spec.xlsx")
    assert score(observation, originals=originals, gold=gold)["task_success"] is True


@pytest.mark.parametrize("mutation", ["original_hash", "source_hash", "cross_supplier", "cross_item",
                                     "wrong_cell", "invented_text", "requirement_as_supplier", "unknown_ref"])
def test_invalid_originals_and_citations_are_recorded(mutation):
    manifest, _, observation, originals = example()
    source = next(s for s in observation["sources"] if s["evidence_id"] == "quantity-quote")
    if mutation == "original_hash":
        originals["quote.xlsx"] += b"changed"
    elif mutation == "source_hash":
        source["sha256"] = "0" * 64
    elif mutation == "cross_supplier":
        source["supplier_handle"] = "vendor-b"
    elif mutation == "cross_item":
        source["item_id"] = "fruit"
    elif mutation == "wrong_cell":
        source["cell"] = "A99"
    elif mutation == "invented_text":
        source["text"] = "invented quotation"
    elif mutation == "requirement_as_supplier":
        source.update(document="requirements.xlsx", role="requirement", supplier_handle=None)
        source["sha256"] = next(d["sha256"] for d in manifest["documents"] if d["role"] == "requirement")
    else:
        observation["api_cells"][0]["evidence_refs"].append("unknown")
    result = score(observation, originals=originals)
    assert result["necessary_evidence"]["numerator"] < 2
    assert result["task_success"] is False
    if mutation == "requirement_as_supplier":
        assert "quote" in result["cells"][0]["missing_evidence_groups"]
    else:
        assert result["citation_errors"]["numerator"] > 0 or result["original_errors"]


def test_absence_requires_scope_coverage_and_no_supplier_support():
    observation = example()[2]
    observation["coverage"].pop()
    result = score(observation)
    assert result["absence_coverage"] == {"numerator": 0, "denominator": 1}
    assert result["task_success"] is False
    assert result["cells"][2]["missing_coverage"]


@pytest.mark.parametrize("field", ["is_formal", "needs_review"])
def test_correct_business_state_with_wrong_formal_or_review_flag_is_incorrect(field):
    observation = example()[2]
    observation["api_cells"][0][field] = not observation["api_cells"][0][field]
    assert score(observation)["status_accuracy"] == {"numerator": 2, "denominator": 3}


def test_no_citations_has_no_artificial_perfect_citation_rate():
    result = score({"pack_id": "dyn-fixture", "run_status": "failed"})
    assert result["citation_error_rate"] is None
    assert result["citation_errors"] == {"numerator": 0, "denominator": 0}


def test_malformed_observation_is_recorded_with_fixed_denominators():
    observation = example()[2]
    observation["api_cells"][0]["is_formal"] = "true"
    result = score(observation)
    assert "invalid_observation" in result["errors"]
    assert result["status_accuracy"] == {"numerator": 0, "denominator": 3}


def test_visible_status_only_does_not_invent_formal_flags_or_evidence():
    observation = example()[2]
    for cell in observation["visible_cells"]:
        cell.update(surface="status_only", is_formal=None, needs_review=None, evidence_refs=[])
    assert score(observation)["task_success"] is True
    observation["api_cells"][0].update(surface="status_only", is_formal=None, needs_review=None)
    assert score(observation)["status_accuracy"] == {"numerator": 2, "denominator": 3}


def test_xlsx_export_claims_are_measured_independently():
    observation = example()[2]
    observation["xlsx_export_cells"] = deepcopy(observation["export_cells"])
    observation["xlsx_export_cells"][1]["business_status"] = "满足"
    result = score(observation)
    assert result["dangerous_meets"] == 1
    assert result["dangerous_meet_surfaces"]["xlsx_export_cells"] == 1
    assert result["task_success"] is False


def test_missing_xlsx_export_cannot_pass_strict_task_without_surface_errors():
    observation = example()[2]
    observation["xlsx_export_cells"] = []
    assert not observation.get("surface_errors")
    result = score(observation)
    assert result["status_accuracy"] == {"numerator": 3, "denominator": 3}
    assert result["necessary_evidence"] == {"numerator": 2, "denominator": 2}
    assert result["task_success"] is False
    assert {tuple(error["key"]) for error in result["identity_errors"]
            if error["surface"] == "xlsx_export_cells" and error["code"] == "missing_cell"} == {
                ("dyn-fixture", "vendor-a", "phone", name) for name in ("quantity", "capacity", "certificate")}


def test_inconsistent_export_does_not_change_correct_api_state_accuracy():
    observation = example()[2]
    observation["surface_errors"] = ["xlsx_json_mismatch"]
    result = score(observation)
    assert result["status_accuracy"] == {"numerator": 3, "denominator": 3}
    assert result["task_success"] is False


def test_invalid_observation_does_not_hide_visible_dangerous_claim():
    observation = example()[2]
    observation["api_cells"][0]["is_formal"] = "true"
    observation["visible_cells"][1]["business_status"] = "满足"
    result = score(observation)
    assert "invalid_observation" in result["errors"]
    assert result["dangerous_meets"] == 1


def test_export_only_unknown_reference_is_an_invalid_citation():
    observation = example()[2]
    observation["export_cells"][0]["evidence_refs"].append("invented-export-citation")
    result = score(observation)
    assert result["citation_errors"] == {"numerator": 1, "denominator": 7}
    assert result["task_success"] is False


def test_unknown_coverage_cannot_receive_absence_credit():
    observation = example()[2]
    observation["coverage_unknown"] = True
    assert score(observation)["absence_coverage"] == {"numerator": 0, "denominator": 1}


def test_duplicate_attempts_cannot_expand_the_primary_pack_denominator():
    assert util.find_spec("evals.dynamic.metrics") is not None
    module = import_module("evals.dynamic.metrics")
    successful = score()
    with pytest.raises(ValueError, match="duplicate_pack"):
        module.aggregate_scores([successful, deepcopy(successful)])


def test_pdf_original_page_text_and_page_bounds_are_verified():
    from hashlib import sha256
    from io import BytesIO

    from reportlab.pdfgen.canvas import Canvas

    manifest, gold, observation, originals = example()
    stream = BytesIO()
    canvas = Canvas(stream, invariant=1)
    for index, text in enumerate(["Phone model DEMO-P256", "quantity 10", "capacity 256", "certificate attached"]):
        canvas.drawString(50, 700 - index * 30, text)
    canvas.save()
    originals.pop("requirements.xlsx")
    originals["requirements.pdf"] = stream.getvalue()
    document = manifest["documents"][0]
    document.update(filename="requirements.pdf", sha256=sha256(stream.getvalue()).hexdigest(),
                    bytes=len(stream.getvalue()), pages=1)
    manifest["bindings"] = [b for b in manifest["bindings"] if b["document"] != "requirements.xlsx"]
    for anchor in manifest["buyer_anchors"].values():
        anchor.update(document="requirements.pdf", page=1)
        anchor.pop("sheet")
        anchor.pop("cell")
    for cell in gold["cells"]:
        anchor = cell["evidence_groups"][0]["alternatives"][0]
        anchor.update(document="requirements.pdf", page=1)
        anchor.pop("sheet")
        anchor.pop("cell")
    for source in observation["sources"]:
        if source["role"] == "requirement":
            source.update(document="requirements.pdf", sha256=document["sha256"], page=1)
            source.pop("sheet")
            source.pop("cell")
    schema = contracts()
    module = import_module("evals.dynamic.metrics")
    labels = schema.Gold.model_validate(gold)
    pack = schema.Manifest.model_validate(manifest)
    assert module.score_pack(pack, labels, observation, originals)["task_success"] is True
    observation["sources"][0]["page"] = 2
    result = module.score_pack(pack, labels, observation, originals)
    assert result["citation_errors"]["numerator"] == 1
    assert result["necessary_evidence"]["numerator"] == 1


def test_group_location_recall_is_distinct_from_grounded_business_success():
    observation = example()[2]
    observation["api_cells"][1]["business_status"] = "不满足"
    result = score(observation)
    assert result["grounded_correct_cells"] == {"numerator": 1, "denominator": 2}
    assert result["necessary_evidence"] == result["grounded_correct_cells"]
    assert result["anchor_group_recall"] == {"numerator": 6, "denominator": 6}


def test_aggregation_keeps_failed_packs_and_rule_breakdowns():
    module = import_module("evals.dynamic.metrics")
    schema = contracts()
    manifest, gold, _, originals = example()
    manifest["pack_id"] = gold["pack_id"] = "dyn-unattempted"
    for cell in gold["cells"]:
        cell["pack_id"] = "dyn-unattempted"
    failed = module.score_pack(schema.Manifest.model_validate(manifest), schema.Gold.model_validate(gold),
                               {"pack_id": "dyn-unattempted", "run_status": "not_started"}, originals)
    result = module.aggregate_scores([score(), failed])
    assert result["agent_tasks"] == {"numerator": 1, "denominator": 2}
    assert result["status_accuracy"] == {"numerator": 3, "denominator": 6}
    assert result["grounded_correct_cells"] == {"numerator": 2, "denominator": 4}
    assert result["anchor_group_recall"] == {"numerator": 6, "denominator": 12}
    assert result["by_category"]["phone"]["status_accuracy"] == {"numerator": 3, "denominator": 6}
    assert result["by_rule"]["numeric"]["status_accuracy"] == {"numerator": 2, "denominator": 4}
    assert result["by_rule"]["presence"]["status_accuracy"] == {"numerator": 1, "denominator": 2}


def test_one_claim_cannot_satisfy_two_conflict_groups_via_shared_alternative():
    _, gold, observation, _ = example()
    quote = gold["cells"][1]["evidence_groups"][1]["alternatives"][0]
    gold["cells"][1]["evidence_groups"][2]["alternatives"].append(deepcopy(quote))
    for surface in ("api_cells", "visible_cells", "export_cells", "xlsx_export_cells"):
        observation[surface][1]["evidence_refs"].remove("capacity-spec")
    with pytest.raises(ValueError, match="conflict_requires_distinct_claims"):
        score(observation, gold=gold)


def test_same_unknown_citation_is_counted_once_across_api_json_and_xlsx():
    observation = example()[2]
    for surface in ("api_cells", "export_cells", "xlsx_export_cells"):
        observation[surface][0]["evidence_refs"].append("shared-unknown-citation")
    result = score(observation)
    assert result["citation_errors"] == {"numerator": 1, "denominator": 7}


def test_fixture_output_surfaces_do_not_share_mutable_reference_lists():
    observation = example()[2]
    observation["export_cells"][0]["evidence_refs"].append("export-only")
    assert "export-only" not in observation["api_cells"][0]["evidence_refs"]
    assert "export-only" not in observation["visible_cells"][0]["evidence_refs"]
    assert "export-only" not in observation["xlsx_export_cells"][0]["evidence_refs"]
