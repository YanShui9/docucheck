"""Business decisions stay scoped to verified facts and complete field coverage."""

from dataclasses import replace
from decimal import Decimal

import pytest

from backend.app.evidence.models import Document
from backend.app.evidence.repository import EvidenceRepository
from backend.app.parsing import EvidenceBlock, ParseResult


def _repo(*, supplier: str = "vendor-a") -> EvidenceRepository:
    documents = []
    for role, text in (("requirement", "需求"), ("quote", "报价"), ("spec", "规格")):
        document_id = role
        parsed = ParseResult(document_id, "test", "complete", (
            EvidenceBlock(f"raw-{role}", document_id, text),
        ))
        documents.append(Document(
            document_id, f"hash-{role}", role,
            None if role == "requirement" else supplier,
            None if role == "requirement" else "MODEL-A", parsed,
        ))
    return EvidenceRepository("field-rules", 1, documents)


def _fact(repo, role, field, raw_value, raw_unit, comparator, qualifier,
          *, attributes=(), supplier="vendor-a"):
    from backend.app.field_contract import FieldFact, FieldKey
    from backend.app.rules.fields import normalize_field_value

    block = next(block for block in repo.blocks if block.role == role)
    value, unit = normalize_field_value(FieldKey(field), raw_value, raw_unit)
    return FieldFact(
        fact_id=f"{role}-{field}-{raw_value}-{raw_unit}-{qualifier}",
        snapshot_id=repo.snapshot_id,
        field_key=FieldKey(field),
        raw_value=raw_value,
        raw_unit=raw_unit,
        value=value,
        unit=unit,
        qualifier=qualifier,
        comparator=comparator,
        role=role,
        supplier=block.supplier,
        model=block.model,
        evidence_refs=(block.evidence_id,),
        attributes=tuple(attributes),
    )


@pytest.mark.parametrize(("field", "requirement", "offered", "expected"), [
    ("storage", ("512", "GB", "gte", "nominal", ()),
     ("1", "TB", "eq", "nominal", ()), "满足"),
    ("screen_size", ("14", "inch", "gte", "diagonal", ()),
     ("35.56", "cm", "eq", "diagonal", ()), "满足"),
    ("weight", ("1.6", "kg", "lte", "device", ()),
     ("1800", "g", "eq", "device", ()), "不满足"),
    ("warranty", ("24", "month", "gte", "standard", (("service_type", "manufacturer"),)),
     ("2", "year", "eq", "standard", (("service_type", "manufacturer"),)), "满足"),
    ("quantity", ("10", "台", "eq", "purchase", (("item_type", "laptop"),)),
     ("10", "台", "eq", "purchase", (("item_type", "laptop"),)), "满足"),
    ("unit_price", ("6000", "元/台", "lte", "tax_included",
                    (("currency", "CNY"), ("pricing_unit", "台"), ("tax_basis", "included"))),
     ("5800", "元/台", "eq", "tax_included",
      (("currency", "CNY"), ("pricing_unit", "台"), ("tax_basis", "included"))), "满足"),
    ("delivery_days", ("14", "natural_day", "lte", "delivery",
                       (("start_event", "order_confirmed"),)),
     ("2", "week", "eq", "delivery", (("start_event", "order_confirmed"),)), "满足"),
    ("memory", ("16", "GiB", "gte", "installed", ()),
     ("16384", "MiB", "eq", "installed", ()), "满足"),
    ("memory", ("16", "GB", "gt", "installed", ()),
     ("16", "GB", "eq", "installed", ()), "不满足"),
])
def test_field_rules_compare_normalized_values(
    field, requirement, offered, expected,
):
    from backend.app.rules.fields import evaluate_field

    repo = _repo()
    facts = [
        _fact(repo, "requirement", field, *requirement[:4], attributes=requirement[4]),
        _fact(repo, "quote", field, *offered[:4], attributes=offered[4]),
    ]
    result = evaluate_field(repo, "vendor-a", field, facts, scan_complete=True)
    assert result.processing_status == "complete"
    assert result.business_status == expected
    assert result.field_key == field
    assert set(result.evidence_refs) == {ref for fact in facts for ref in fact.evidence_refs}


@pytest.mark.parametrize(("field", "req", "offer"), [
    ("storage", ("512", "GB", "nominal", ()), ("512", "GiB", "nominal", ())),
    ("screen_size", ("14", "inch", "diagonal", ()),
     ("14", "inch", "diagonal", (("approximate", "true"),))),
    ("weight", ("1.6", "kg", "device", ()), ("1", "kg", "package", ())),
    ("warranty", ("24", "month", "standard", (("service_type", "manufacturer"),)),
     ("24", "month", "standard", (("service_type", "seller"),))),
    ("quantity", ("10", "台", "purchase", (("item_type", "laptop"),)),
     ("10", "台", "gift", (("item_type", "laptop"),))),
    ("unit_price", ("6000", "元/台", "tax_included",
                    (("currency", "CNY"), ("pricing_unit", "台"), ("tax_basis", "included"))),
     ("5800", "元/台", "tax_excluded",
      (("currency", "CNY"), ("pricing_unit", "台"), ("tax_basis", "excluded")))),
    ("delivery_days", ("14", "natural_day", "delivery",
                       (("start_event", "order_confirmed"),)),
     ("10", "working_day", "delivery", (("start_event", "contract_signed"),))),
])
def test_incompatible_field_basis_requires_review(field, req, offer):
    from backend.app.rules.fields import evaluate_field

    repo = _repo()
    facts = [
        _fact(repo, "requirement", field, req[0], req[1], "gte", req[2], attributes=req[3]),
        _fact(repo, "quote", field, offer[0], offer[1], "eq", offer[2], attributes=offer[3]),
    ]
    result = evaluate_field(repo, "vendor-a", field, facts, scan_complete=True)
    assert result.processing_status == "complete"
    assert result.business_status == "待人工确认"


def test_incomplete_scan_never_publishes_a_business_status():
    from backend.app.rules.fields import evaluate_field

    repo = _repo()
    facts = [
        _fact(repo, "requirement", "storage", "512", "GB", "gte", "nominal"),
        _fact(repo, "quote", "storage", "1", "TB", "eq", "nominal"),
    ]
    result = evaluate_field(repo, "vendor-a", "storage", facts, scan_complete=False)
    assert result.processing_status == "partial"
    assert result.business_status is None


def test_failed_extraction_is_distinct_from_partial_coverage():
    from backend.app.rules.fields import evaluate_field

    repo = _repo()
    result = evaluate_field(repo, "vendor-a", "storage", [],
                            scan_complete=False, failed=True)
    assert result.processing_status == "failed"
    assert result.business_status is None
    assert result.reason_code == "extraction_failed"


def test_conflicting_supplier_sources_are_not_silently_selected():
    from backend.app.rules.fields import evaluate_field

    repo = _repo()
    facts = [
        _fact(repo, "requirement", "storage", "512", "GB", "gte", "nominal"),
        _fact(repo, "quote", "storage", "1", "TB", "eq", "nominal"),
        _fact(repo, "spec", "storage", "2", "TB", "eq", "nominal"),
    ]
    result = evaluate_field(repo, "vendor-a", "storage", facts, scan_complete=True)
    assert result.business_status == "资料冲突"
    assert {repo.read([ref])[0].role for ref in result.reason_evidence_refs} == {"quote", "spec"}


def test_full_scan_can_distinguish_missing_supplier_fact_from_partial_scan():
    from backend.app.rules.fields import evaluate_field

    repo = _repo()
    facts = [_fact(repo, "requirement", "warranty", "24", "month", "gte", "standard",
                   attributes=(("service_type", "manufacturer"),))]
    assert evaluate_field(repo, "vendor-a", "warranty", facts,
                          scan_complete=True).business_status == "证据不足"
    assert evaluate_field(repo, "vendor-a", "warranty", facts,
                          scan_complete=False).business_status is None


def test_field_and_supplier_scope_are_checked_before_rules():
    from backend.app.rules.fields import evaluate_field

    repo = _repo()
    fact = _fact(repo, "quote", "storage", "1", "TB", "eq", "nominal")
    with pytest.raises(ValueError, match="fact_field"):
        evaluate_field(repo, "vendor-a", "weight", [fact], scan_complete=True)
    with pytest.raises(ValueError, match="fact_snapshot"):
        evaluate_field(repo, "vendor-a", "storage",
                       [replace(fact, snapshot_id="old")], scan_complete=True)
    with pytest.raises(ValueError, match="supplier_scope"):
        evaluate_field(repo, "vendor-a", "storage",
                       [replace(fact, supplier="vendor-b")], scan_complete=True)


def test_model_specific_requirement_does_not_apply_to_a_different_model():
    from backend.app.rules.fields import evaluate_field

    repo = _repo()
    documents = [replace(document, model="MODEL-B") if document.role == "requirement"
                 else document for document in repo.documents]
    repo = EvidenceRepository("field-rules", 2, documents)
    facts = [
        _fact(repo, "requirement", "storage", "512", "GB", "gte", "nominal"),
        _fact(repo, "quote", "storage", "1", "TB", "eq", "nominal"),
    ]
    result = evaluate_field(repo, "vendor-a", "storage", facts, scan_complete=True)
    assert result.business_status == "待人工确认"


def test_price_currency_attribute_must_match_the_original_unit():
    from backend.app.rules.fields import evaluate_field

    repo = _repo()
    requirement = _fact(
        repo, "requirement", "unit_price", "6000", "元/台", "lte", "tax_included",
        attributes=(("currency", "CNY"), ("pricing_unit", "台"), ("tax_basis", "included")),
    )
    offered = _fact(
        repo, "quote", "unit_price", "5000", "元/台", "eq", "tax_included",
        attributes=(("currency", "USD"), ("pricing_unit", "台"), ("tax_basis", "included")),
    )
    result = evaluate_field(repo, "vendor-a", "unit_price", [requirement, offered],
                            scan_complete=True)
    assert result.business_status == "待人工确认"


@pytest.mark.parametrize(("field", "unit", "qualifier", "attribute"), [
    ("warranty", "month", "standard", "service_type"),
    ("quantity", "台", "purchase", "item_type"),
    ("delivery_days", "natural_day", "delivery", "start_event"),
])
def test_unknown_required_attribute_cannot_be_treated_as_a_match(
    field, unit, qualifier, attribute,
):
    from backend.app.rules.fields import evaluate_field

    repo = _repo()
    facts = [
        _fact(repo, "requirement", field, "10", unit, "gte", qualifier,
              attributes=((attribute, "unknown"),)),
        _fact(repo, "quote", field, "10", unit, "eq", qualifier,
              attributes=((attribute, "unknown"),)),
    ]
    result = evaluate_field(repo, "vendor-a", field, facts, scan_complete=True)
    assert result.business_status == "待人工确认"


def test_normalized_values_cannot_be_forged():
    from backend.app.rules.fields import evaluate_field

    repo = _repo()
    fact = _fact(repo, "quote", "storage", "1", "TB", "eq", "nominal")
    with pytest.raises(ValueError, match="fact_normalization"):
        evaluate_field(repo, "vendor-a", "storage",
                       [replace(fact, value=Decimal(10))], scan_complete=True)
    with pytest.raises(ValueError, match="fact_normalization"):
        evaluate_field(repo, "vendor-a", "storage",
                       [replace(fact, value=1000.0)], scan_complete=True)


def test_result_identity_includes_fact_content_even_if_fact_id_is_reused():
    from backend.app.rules.fields import evaluate_field

    repo = _repo()
    requirement = _fact(repo, "requirement", "storage", "512", "GB", "gte", "nominal")
    offered = _fact(repo, "quote", "storage", "1", "TB", "eq", "nominal")
    changed = replace(offered, raw_value="2", value=Decimal(2000))
    first = evaluate_field(repo, "vendor-a", "storage", [requirement, offered],
                           scan_complete=True)
    second = evaluate_field(repo, "vendor-a", "storage", [requirement, changed],
                            scan_complete=True)
    assert first.business_status == second.business_status == "满足"
    assert first.check_result_id != second.check_result_id


def test_quantity_requires_positive_integer_and_single_purchase_basis():
    from backend.app.rules.fields import normalize_field_value

    for invalid in ("0", "-1", "1.5"):
        with pytest.raises(ValueError, match="fact_value"):
            normalize_field_value("quantity", invalid, "台")


def test_money_keeps_decimal_precision():
    from backend.app.rules.fields import normalize_field_value

    value, unit = normalize_field_value("unit_price", "5800.01", "元/台")
    assert value == Decimal("5800.01")
    assert unit == "CNY/台"


def test_normalization_rejects_unbounded_numeric_input():
    from backend.app.rules.fields import normalize_field_value

    with pytest.raises(ValueError, match="fact_value"):
        normalize_field_value("unit_price", "9" * 1000, "元/台")
