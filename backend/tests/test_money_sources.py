"""v13 money keeps each independent tax statement and verifies its relation."""

from copy import deepcopy
from dataclasses import replace

import pytest

from backend.app.criteria.facts import (
    fact_source_groups,
    fact_source_refs,
    revalidate_fact,
)
from backend.app.criteria.item_bindings import (
    ReviewBindings,
    review_bindings,
    validate_bindings,
)
from backend.app.criteria.models import ChecklistDefinition
from backend.app.evidence.models import Document
from backend.app.evidence.repository import EvidenceRepository, digest
from backend.app.parsing import EvidenceBlock, ParseResult
from backend.tests.test_bound_enum_headers import candidate, checks, source, verify

V13 = "dynamic-rules-v13"
V12 = "dynamic-rules-v12"
RULE = {"type": "money", "operator": "lte", "value": "25", "currency": "CNY",
        "per_unit": "箱", "tax_basis": "excluded"}


def sample(*, price="不含税单价", value="25", tax=None, extra=()):
    cells = [("A6", price), ("B6", value), ("C6", "元/箱")]
    if tax is not None:
        cells.extend([("A8", "税费口径"), ("B8", tax)])
    cells.extend(extra)
    built = source("单价上限", RULE, cells)
    return built, candidate(built[1], value, "元/箱", indices=[1, 2, 3])


@pytest.mark.parametrize("label", ["不含税单价", "未含税单价", "税前单价"])
@pytest.mark.parametrize("value,status", [("25", "满足"), ("26", "不满足")])
def test_v13_finite_excluded_price_headers(label, value, status):
    built, body = sample(price=label, value=value)
    fact = verify(built, body, V13)
    assert fact["tax_basis"] == "excluded" and not fact["ambiguous"]
    assert fact["tax_sources"] == [] and fact["tax_scope_state"] == "resolved"
    assert checks(built, [fact], V13)[0]["business_status"] == status


def test_unselected_independent_conflicting_tax_row_is_not_lost():
    built, body = sample(tax="含税")
    fact = verify(built, body, V13)
    tax = fact["tax_sources"][0]
    assert tax["evidence_refs"] == [b.evidence_id for b in built[1].blocks[4:6]]
    assert tax["quote"] == "税费口径 含税"
    assert tax["tax_basis"] == "included" and tax["scope_state"] == "resolved"
    assert fact["tax_conflict"] and fact["tax_scope_state"] == "resolved"
    result = checks(built, [fact], V13)[0]
    assert (result["business_status"], result["reason_code"]) == ("资料冲突", "money_tax_conflict")
    assert result["evidence_refs"] == fact_source_refs(fact)
    assert result["tax_source_groups"] == [{"fact_id": fact["fact_id"], "evidence_refs": tax["evidence_refs"]}]
    assert fact_source_groups(fact)[1] == tuple(sorted(tax["evidence_refs"]))


@pytest.mark.parametrize("tax", ["待确认", "含税或不含税", "含税（暂估）", ""])
def test_unknown_or_qualified_tax_statement_requires_manual(tax):
    built, body = sample(tax=tax)
    fact = verify(built, body, V13)
    assert fact["tax_sources"] and fact["tax_scope_state"] == "unclear"
    assert checks(built, [fact], V13)[0]["business_status"] == "待人工确认"


def test_independent_tax_can_supply_missing_main_tax_basis():
    built, body = sample(price="单价", tax="不含税")
    fact = verify(built, body, V13)
    assert fact["tax_basis"] == "unspecified" and fact["effective_tax_basis"] == "excluded"
    assert checks(built, [fact], V13)[0]["business_status"] == "满足"


def test_multiple_prices_cannot_assign_one_shared_tax_statement():
    built, body = sample(tax="含税", extra=[("A10", "单价30元/箱")])
    fact = verify(built, body, V13)
    assert fact["tax_scope_state"] == "unclear" and not fact["tax_conflict"]
    assert checks(built, [fact], V13)[0]["business_status"] == "待人工确认"


@pytest.mark.parametrize("field", ["tax_sources", "tax_scope_state", "tax_conflict", "effective_tax_basis",
                                   "price_sources", "price_scope_state"])
def test_server_tax_metadata_is_required_and_recomputed_at_revalidation(field):
    built, body = sample(tax="含税")
    fact = verify(built, body, V13)
    broken = deepcopy(fact)
    del broken[field]
    with pytest.raises(ValueError, match="fact_integrity"):
        revalidate_fact(broken, built[0], built[1], "A", rule_version=V13, item_bindings=built[2])
    assert revalidate_fact(fact, built[0], built[1], "A", rule_version=V13, item_bindings=built[2]) == fact


def test_candidate_cannot_inject_tax_metadata():
    built, body = sample()
    with pytest.raises(ValueError):
        verify(built, body | {"tax_sources": []}, V13)


def test_v12_fact_and_result_keep_their_historical_shape_and_behavior():
    built, body = sample(price="单价", tax="含税")
    fact = verify(built, body, V12)
    assert "tax_sources" not in fact and "tax_conflict" not in fact
    assert checks(built, [fact], V12)[0]["business_status"] == "待人工确认"
    assert "tax_source_groups" not in checks(built, [fact], V12)[0]
    built, body = sample()
    with pytest.raises(ValueError, match="fact_criterion"):
        verify(built, body, V12)


def test_v12_fact_check_and_batch_identities_match_pre_change_source():
    # Captured from a80c76f facts.py before the v13 implementation. No model or
    # current-v13 result supplies the expected historical identities.
    from backend.app.agent.dynamic import review_plan

    built, body = sample(price="单价", tax="含税")
    fact = verify(built, body, V12)
    check = checks(built, [fact], V12)[0]
    plans, _ = review_plan(built[1], built[0], rule_version=V12, item_bindings=built[2])
    manifest = plans["A"][0][2]
    assert fact["fact_id"] == "2cf4f2ceb4d7c63d92321deae2773212766356b3419f7f416b89c6b2bf7e50df"
    assert check["check_result_id"] == "debc8ba85c4d1b827e2f1448c77b40d5b8e5884ab3c1de064022891a0db69440"
    assert manifest["batch_id"] == "d1044c34d4bf9c64b7dc8c39fa7943a234f61886119619b69e57b52497be8f22"
    assert manifest["input_digest"] == "05d9f190519ec9fcb7204071c60f3e6ed30e40d9937614c6d34c19da2bcb99a3"


def independent(*, supplier="A", item_id="sample", sheet="报价", split_entry=False,
                pdf=False, whole=False, classification="category", second_text="税费口径：含税"):
    """A tax statement in a separately confirmed document/range is never guessed."""
    definition = ChecklistDefinition.model_validate({
        "items": [{"item_id": "sample", "name": "样品"}, {"item_id": "other", "name": "其他商品"}],
        "criteria": [{"criterion_id": "metric", "item_id": "sample", "name": "单价上限", "rule": RULE,
                      "source": {"kind": "user_defined", "reason": "买方确认"}}]})
    documents, requests = [], []
    for doc_id, owner, anchor, text, position in [
            ("main", "A", "样品", "不含税单价25元/箱", "A6"),
            ("tax", supplier, "样品" if item_id == "sample" else "其他商品", second_text, "A8")]:
        blocks = (EvidenceBlock("anchor", doc_id, anchor, **({"page_idx": 0} if pdf else {"sheet": sheet, "cell": "A1"})),
                  EvidenceBlock("body", doc_id, text, **({"page_idx": 1} if pdf else {"sheet": sheet, "cell": position})))
        documents.append(Document(doc_id, ("a" if doc_id == "main" else "b") * 64, "quote", owner, None,
                                  ParseResult(doc_id, "parser", "complete", blocks,
                                              **({"total_pages": 2, "processed_pages": (0, 1)} if pdf else
                                                 {"total_sheets": 1, "processed_sheets": (sheet,)}))))
    if split_entry or whole:
        main, tax = documents
        tax_blocks = tuple(replace(b, document_id="main") for b in tax.parsed.blocks[1:])
        documents = [replace(main, parsed=replace(main.parsed, blocks=(*main.parsed.blocks, *tax_blocks)))]
    repo = EvidenceRepository("case", 1, documents)
    for document in documents:
        anchor = next(b for b in repo.blocks if b.document_id == document.document_id)
        base = {"document_id": document.document_id, "source_sha256": document.source_hash,
                "parser_version": "parser", "item_id": "sample" if document.document_id == "main" else item_id,
                "anchor": {"evidence_refs": [anchor.evidence_id], "quote": anchor.text},
                "identifier_class": classification}
        if whole:
            requests.append(base | {"selector": {"kind": "whole"}, "single_item_confirmed": True})
        elif split_entry:
            for row in (6, 8):
                requests.append(base | {"selector": {"kind": "xlsx", "sheet": sheet, "start_row": row,
                                                     "end_row": row, "start_column": 1, "end_column": 1}})
        else:
            selector = ({"kind": "pdf", "start_page": 1, "end_page": 2} if pdf else
                        {"kind": "xlsx", "sheet": sheet, "start_row": 6 if document.document_id == "main" else 8,
                         "end_row": 6 if document.document_id == "main" else 8,
                         "start_column": 1, "end_column": 1})
            requests.append(base | {"selector": selector})
    return rebuild(definition, repo, requests)


def rebuild(definition, repo, requests):
    entries = validate_bindings(requests, definition, repo).payload()
    payload = {"schema_version": 1, "case_id": "case", "snapshot_id": repo.snapshot_id,
               "checklist_version_id": "confirmed", "checklist_digest": digest(definition.model_dump(mode="json")),
               "entries": entries}
    frozen = {"version_id": "binding-a", "digest": digest(payload), "payload": payload}
    primary = next(b for b in repo.blocks if "不含税单价" in b.text)
    body = {"criterion_id": "metric", "item_id": "sample", "supplier": "A", "value": "25", "unit": "元/箱",
            "evidence_refs": [primary.evidence_id], "quote": primary.text}
    return (definition, repo, frozen), body


@pytest.mark.parametrize("options", [{}, {"split_entry": True}, {"pdf": True}])
def test_other_confirmed_entry_is_preserved_without_becoming_applicable(options):
    built, body = independent(**options)
    fact = verify(built, body, V13)
    assert len(fact["tax_sources"]) == 1
    assert fact["tax_scope_state"] == "unclear" and not fact["tax_conflict"]
    assert checks(built, [fact], V13)[0]["business_status"] == "待人工确认"


@pytest.mark.parametrize("options", [{"supplier": "B"}, {"item_id": "other"}])
def test_other_supplier_or_item_tax_cannot_be_inherited(options):
    built, body = independent(**options)
    fact = verify(built, body, V13)
    assert fact["tax_sources"] == []
    assert checks(built, [fact], V13)[0]["business_status"] == "满足"


@pytest.mark.parametrize("split_entry", [False, True])
def test_unselected_price_in_another_confirmed_entry_requires_manual(split_entry):
    built, body = independent(split_entry=split_entry, second_text="单价30元/箱")
    fact = verify(built, body, V13)
    result = checks(built, [fact], V13)[0]
    assert (result["business_status"], result["reason_code"]) == ("待人工确认", "money_price_scope_unclear")
    other = next(block for block in built[1].blocks if block.text == "单价30元/箱")
    source = fact["price_sources"][0]
    assert source["evidence_refs"] == [other.evidence_id]
    assert source["quote"] == "单价30元/箱"
    assert source["item_binding_entry_digest"] != fact["item_binding_entry_digest"]
    assert other.evidence_id in result["evidence_refs"]
    assert revalidate_fact(fact, built[0], built[1], "A", rule_version=V13, item_bindings=built[2]) == fact


@pytest.mark.parametrize("options", [{"supplier": "B"}, {"item_id": "other"}])
def test_other_supplier_or_item_price_cannot_be_inherited(options):
    built, body = independent(**options, second_text="单价30元/箱")
    fact = verify(built, body, V13)
    assert fact["price_sources"] == [] and fact["price_scope_state"] == "resolved"
    assert checks(built, [fact], V13)[0]["business_status"] == "满足"


def test_whole_excel_same_sheet_unique_price_can_resolve_but_pdf_cannot():
    built, body = independent(whole=True)
    fact = verify(built, body, V13)
    assert checks(built, [fact], V13)[0]["business_status"] == "资料冲突"
    built, body = independent(whole=True, pdf=True)
    fact = verify(built, body, V13)
    assert fact["tax_sources"][0]["scope_state"] == "unclear"


def test_tax_on_another_sheet_in_same_whole_entry_is_unclear():
    built, _ = independent(whole=True)
    document = built[1].documents[0]
    changed = tuple(replace(b, sheet="说明") if "税费口径" in b.text else b for b in document.parsed.blocks)
    repo = EvidenceRepository("case", 1, [replace(document, parsed=replace(document.parsed, blocks=changed,
        total_sheets=2, processed_sheets=("报价", "说明")))])
    anchor = repo.blocks[0]
    requests = [{"document_id": "main", "source_sha256": "a" * 64, "parser_version": "parser", "item_id": "sample",
                 "selector": {"kind": "whole"}, "single_item_confirmed": True,
                 "anchor": {"evidence_refs": [anchor.evidence_id], "quote": anchor.text}, "identifier_class": "category"}]
    built, body = rebuild(built[0], repo, requests)
    fact = verify(built, body, V13)
    assert fact["tax_scope_state"] == "unclear" and not fact["tax_conflict"]


@pytest.mark.parametrize("field", ["source_sha256", "parser_version"])
def test_each_tax_source_checks_original_version_even_for_prevalidated_bindings(field):
    built, body = independent()
    definition, repo, frozen = built
    validated = review_bindings(frozen, definition, repo).bindings
    altered = replace(validated.entries[1], **{field: "c" * 64 if field == "source_sha256" else "changed"})
    bindings = replace(validated, entries=(validated.entries[0], altered))
    context = ReviewBindings("binding-a", frozen["digest"], repo.case_id, repo.snapshot_id,
                             digest(definition.model_dump(mode="json")), bindings)
    with pytest.raises(ValueError, match="item_binding_source_changed"):
        verify((definition, repo, context), body, V13)


@pytest.mark.parametrize("field", ["quote", "evidence_refs", "item_binding_entry_digest", "tax_basis", "scope_state"])
def test_nested_tax_source_tampering_is_rejected(field):
    built, body = sample(tax="含税")
    fact = deepcopy(verify(built, body, V13))
    fact["tax_sources"][0][field] = ["forged"] if field == "evidence_refs" else "forged"
    with pytest.raises(ValueError, match="fact_integrity"):
        revalidate_fact(fact, built[0], built[1], "A", rule_version=V13, item_bindings=built[2])


@pytest.mark.parametrize("cells", [[("A8", "税费口径"), ("C8", "含税")],
                                  [("A8", "税费口径"), ("B8", "含税"), ("C8", "附注"), ("D8", "详见"), ("E8", "说明")]])
def test_broken_or_oversized_tax_row_preserves_original_blocks_without_join(cells):
    built, body = sample(extra=cells)
    fact = verify(built, body, V13)
    assert len(fact["tax_sources"]) == len(cells)
    assert fact["tax_scope_state"] == "unclear"


def test_conflict_does_not_create_formal_status_for_incomplete_coverage():
    from backend.app.criteria.facts import evaluate_checklist

    built, body = sample(tax="含税")
    definition, repo, frozen = built
    fact = verify(built, body, V13)
    result = evaluate_checklist(definition, [fact], {"A": False}, snapshot_id=repo.snapshot_id,
                               rule_version=V13, item_coverage={"A": {"sample": False}}, item_bindings=frozen)[0]
    assert result["processing_status"] == "partial" and result["business_status"] is None


def test_proposed_tax_basis_describes_main_quote_and_is_not_rewritten_by_independent_note():
    built, body = sample(price="单价", tax="不含税")
    fact = verify(built, body | {"tax_basis": "unspecified"}, V13)
    assert fact["tax_basis"] == "unspecified" and fact["effective_tax_basis"] == "excluded"
    assert checks(built, [fact], V13)[0]["business_status"] == "满足"
    assert revalidate_fact(fact, built[0], built[1], "A", rule_version=V13, item_bindings=built[2]) == fact
    with pytest.raises(ValueError, match="fact_tax_basis"):
        verify(built, body | {"tax_basis": "excluded"}, V13)


def test_effective_tax_basis_cannot_hide_conflicting_qualifications_inside_main_span():
    built = source("单价上限", RULE, [("A6", "单价25元/箱，不含税，含税"), ("A8", "税费口径：不含税")])
    body = candidate(built[1], "25", "元/箱", indices=[1])
    fact = verify(built, body, V13)
    assert fact["ambiguous"] and fact["effective_tax_basis"] == "unspecified"
    assert checks(built, [fact], V13)[0]["business_status"] == "待人工确认"


def test_tax_source_budget_fails_closed_without_silently_discarding_the_65th_statement():
    built, body = sample(extra=[(f"A{row}", "税费口径：不含税") for row in range(8, 73)])
    with pytest.raises(ValueError, match="money_tax_source_budget"):
        verify(built, body, V13)


def test_unselected_alternative_price_requires_manual_even_without_tax_note():
    built, body = sample(extra=[("A10", "单价30元/箱")])
    fact = verify(built, body, V13)
    result = checks(built, [fact], V13)[0]
    assert (result["business_status"], result["reason_code"]) == ("待人工确认", "money_price_scope_unclear")
    assert fact["price_scope_state"] == "unclear"
    price = fact["price_sources"][0]
    assert price == {"evidence_refs": [built[1].blocks[4].evidence_id], "quote": "单价30元/箱",
                     "item_binding_entry_digest": fact["item_binding_entry_digest"]}
    assert price["evidence_refs"][0] in fact_source_refs(fact)
    assert tuple(price["evidence_refs"]) in fact_source_groups(fact)
    assert result["price_source_groups"] == [{"fact_id": fact["fact_id"], "evidence_refs": price["evidence_refs"]}]
    assert "tax_source_groups" not in result
    assert revalidate_fact(fact, built[0], built[1], "A", rule_version=V13, item_bindings=built[2]) == fact


def test_unique_price_has_complete_empty_metadata_and_frozen_v12_shape():
    built, body = sample(price="单价")
    fact = verify(built, body, V13)
    assert fact["price_sources"] == [] and fact["price_scope_state"] == "resolved"
    old = verify(built, body, V12)
    assert "price_sources" not in old and "price_scope_state" not in old


@pytest.mark.parametrize("field", ["price_sources", "price_scope_state"])
def test_model_cannot_supply_price_scope_metadata(field):
    built, body = sample()
    with pytest.raises(ValueError):
        verify(built, body | {field: [] if field == "price_sources" else "resolved"}, V13)


@pytest.mark.parametrize("field", ["quote", "evidence_refs", "item_binding_entry_digest"])
def test_nested_price_source_tampering_fails_revalidation(field):
    built, body = sample(extra=[("A10", "单价30元/箱")])
    fact = deepcopy(verify(built, body, V13))
    fact["price_sources"][0][field] = ["forged"] if field == "evidence_refs" else "forged"
    with pytest.raises(ValueError, match="fact_integrity"):
        revalidate_fact(fact, built[0], built[1], "A", rule_version=V13, item_bindings=built[2])


@pytest.mark.parametrize("cells", [[("A10", "单价"), ("C10", "30元/箱")],
                                  [("A10", "单价"), ("B10", "30"), ("C10", "元/箱"),
                                   ("D10", "附注"), ("E10", "说明")]])
def test_broken_or_oversized_alternative_price_keeps_independent_blocks(cells):
    built, body = sample(extra=cells)
    fact = verify(built, body, V13)
    assert len(fact["price_sources"]) == len(cells)
    assert all(len(group["evidence_refs"]) == 1 for group in fact["price_sources"])
    assert fact["price_scope_state"] == "unclear"


def test_price_source_budget_rejects_overflow_without_dropping_alternative_prices():
    built, body = sample(extra=[(f"A{row}", "单价30元/箱") for row in range(8, 73)])
    with pytest.raises(ValueError, match="money_price_source_budget"):
        verify(built, body, V13)


def test_two_price_numbers_in_main_row_cannot_hide_unselected_cells():
    built, body = sample(extra=[("D6", "单价30元/箱")])
    fact = verify(built, body, V13)
    assert fact["price_scope_state"] == "unclear" and fact["price_sources"]
    assert built[1].blocks[4].evidence_id in fact_source_refs(fact)
    assert checks(built, [fact], V13)[0]["reason_code"] == "money_price_scope_unclear"


@pytest.mark.parametrize("note", ["备注：本报价包含增值税", "所有价格均已计入增值税", "税率：13%"])
def test_unfamiliar_tax_expressions_are_preserved_without_interpretation(note):
    built, body = sample(extra=[("A8", note)])
    fact = verify(built, body, V13)
    assert fact["tax_sources"] and fact["tax_sources"][0]["quote"] == note
    assert fact["tax_scope_state"] == "unclear"
    assert checks(built, [fact], V13)[0]["business_status"] == "待人工确认"
