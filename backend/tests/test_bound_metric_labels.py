"""Item display prefixes cannot require fabricated supplier quotations."""
from copy import deepcopy
from dataclasses import replace

import pytest

from backend.app.criteria.facts import (
    BINDING_RULE_VERSION,
    evaluate_checklist,
    validate_fact,
)
from backend.app.criteria.item_bindings import validate_bindings
from backend.app.criteria.models import ChecklistDefinition
from backend.app.evidence.repository import EvidenceRepository, digest
from backend.tests.test_item_binding_review import candidate, sample


def named_sample(name, *, money=False, source=None):
    definition, repo, frozen = sample()
    data = definition.model_dump(mode="json")
    data["criteria"][0]["name"] = name
    if money:
        data["criteria"][0]["rule"] = {"type": "money", "operator": "lte", "value": "6000", "currency": "CNY", "per_unit": "台", "tax_basis": "included"}
    if money or source is not None:
        doc = repo.documents[0]
        blocks = list(doc.parsed.blocks)
        blocks[1] = replace(blocks[1], text=source or "含税单价5800元/台")
        repo = EvidenceRepository("case", 1, [replace(doc, parsed=replace(doc.parsed, blocks=tuple(blocks)))])
    definition = ChecklistDefinition.model_validate(data)
    request = {k: v for k, v in frozen["payload"]["entries"][0].items()
               if k in {"document_id", "source_sha256", "parser_version", "item_id", "selector", "anchor", "identifier_class"}}
    request["anchor"] = {"evidence_refs": [repo.blocks[0].evidence_id], "quote": "苹果手机"}
    frozen["payload"].update(snapshot_id=repo.snapshot_id, checklist_digest=digest(definition.model_dump(mode="json")), entries=validate_bindings([request], definition, repo).payload())
    frozen["digest"] = digest(frozen["payload"])
    raw = candidate(repo) | ({"value": "5800", "unit": "元/台"} if money else {})
    return definition, repo, frozen, raw


@pytest.mark.parametrize("name,money", [("苹果手机数量", False), ("苹果手机采购数量", False),
                                        ("苹果手机含税单价上限", True), ("单价上限", True)])
def test_current_bound_finite_metric_names_use_original_source_without_item_prefix(name, money):
    definition, repo, frozen, raw = named_sample(name, money=money)
    fact = validate_fact(raw, definition, repo, "A", rule_version=BINDING_RULE_VERSION, item_bindings=frozen)
    assert fact["quote"] == repo.blocks[1].text and fact["evidence_refs"] == [repo.blocks[1].evidence_id]
    result = evaluate_checklist(definition, [fact], {"A": True}, snapshot_id=repo.snapshot_id,
                                rule_version=BINDING_RULE_VERSION, item_coverage={"A": {"phone": True}}, item_bindings=frozen)
    assert result[0]["business_status"] == "满足"
    for old_rule in ("dynamic-rules-v4", "dynamic-rules-v5"):
        with pytest.raises(ValueError, match="fact_criterion"):
            validate_fact(deepcopy(raw), definition, repo, "A", rule_version=old_rule, item_bindings=frozen)


@pytest.mark.parametrize("name", ["T恤数量", "苹果手机存储容量", "苹果手机平均单价", "苹果手机总价", "苹果手机预算"])
def test_aliases_cannot_guess_other_metrics_or_remove_another_item_name(name):
    definition, repo, frozen, raw = named_sample(name)
    with pytest.raises(ValueError, match="fact_criterion"):
        validate_fact(raw, definition, repo, "A", rule_version=BINDING_RULE_VERSION, item_bindings=frozen)


@pytest.mark.parametrize("source", ["平均单价5800元/台", "总价5800元/台", "预算单价5800元/台",
                                    "含税单价：预算5800元/台", "含税单价：平均5800元/台", "含税单价：总价5800元/台",
                                    "含税单价：5800元/台（预算）", "含税单价5800元/台（平均）", "含税单价5800元/台（总价）",
                                    "含税单价：指导价5800元/台", "含税单价：5800元/台（建议价格）",
                                    "含税单价：5800元/台，预算价", "含税单价：5800元/台，平均价",
                                    "含税单价：5800元/台，总价", "含税单价：5800元/台；预算价",
                                    "含税单价：5800元/台\n平均价"])
@pytest.mark.parametrize("name", ["苹果手机含税单价上限", "含税单价", "单价"])
def test_bound_metric_names_cannot_turn_average_total_or_budget_into_supplier_unit_price(source, name):
    definition, repo, frozen, raw = named_sample(name, money=True, source=source)
    with pytest.raises(ValueError, match="fact_criterion"):
        validate_fact(raw, definition, repo, "A", rule_version=BINDING_RULE_VERSION, item_bindings=frozen)


@pytest.mark.parametrize("source", ["含税单价：5800元/台", "含税单价不超过5800元/台", "含税单价：人民币5800元/台"])
def test_alias_head_allows_explicit_price_separators_and_comparator(source):
    definition, repo, frozen, raw = named_sample("苹果手机含税单价上限", money=True, source=source)
    fact = validate_fact(raw, definition, repo, "A", rule_version=BINDING_RULE_VERSION, item_bindings=frozen)
    assert fact["value"] == "5800" and fact["tax_basis"] == "included"


def test_different_quantity_clauses_cannot_produce_a_definite_result():
    definition, repo, frozen, raw = named_sample("苹果手机数量", source="数量10台；采购数量20台")
    with pytest.raises(ValueError, match="fact_value"):
        validate_fact(raw, definition, repo, "A", rule_version=BINDING_RULE_VERSION, item_bindings=frozen)
    fact = validate_fact(raw | {"value": "20"}, definition, repo, "A", rule_version=BINDING_RULE_VERSION, item_bindings=frozen)
    result = evaluate_checklist(definition, [fact], {"A": True}, snapshot_id=repo.snapshot_id,
                                rule_version=BINDING_RULE_VERSION, item_coverage={"A": {"phone": True}}, item_bindings=frozen)
    assert fact["ambiguous"] is True and result[0]["business_status"] == "待人工确认"
