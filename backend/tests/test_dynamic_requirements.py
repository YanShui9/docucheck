"""Source-bound dynamic requirement contracts, with no external services."""

import importlib
from copy import deepcopy

import pytest

from backend.app.criteria.models import ChecklistDefinition
from backend.app.evidence.models import Document
from backend.app.evidence.repository import EvidenceRepository
from backend.app.parsing import EvidenceBlock, ParseResult


def validation():
    assert importlib.util.find_spec("backend.app.criteria.validation"), "dynamic validation missing"
    return importlib.import_module("backend.app.criteria.validation")


def make_repo(requirement="苹果净重至少100 g", offers=None, *, case="case", revision=1):
    offers = offers or [("A", "苹果净重120 g", None)]
    documents = []
    for index, (supplier, text, model) in enumerate([(None, requirement, None), *offers]):
        document_id = f"document-{index}.xlsx"
        entries = text if isinstance(text, list) else [("A1", text)]
        blocks = tuple(
            EvidenceBlock(f"raw-{index}-{n}", document_id, content, sheet="sheet", cell=cell)
            for n, (cell, content) in enumerate(entries)
        )
        parsed = ParseResult(document_id, "test", "complete", blocks)
        documents.append(Document(document_id, f"hash-{index}", "requirement" if supplier is None
                                  else "quote", supplier, model, parsed))
    return EvidenceRepository(case, revision, documents)


def make_definition(repo=None, *, rule=None, name="净重", item_name="苹果", document=False):
    source = {"kind": "user_defined", "reason": "用户设置"}
    if document:
        source = {"kind": "document_extracted", "context": {
            "evidence_refs": [repo.blocks[0].evidence_id], "quote": repo.blocks[0].text,
        }}
    return ChecklistDefinition.model_validate({
        "items": [{"item_id": "apple", "name": item_name}],
        "criteria": [{"criterion_id": "weight", "item_id": "apple", "name": name,
                      "rule": rule or {"type": "numeric", "operator": "gte",
                                       "value": "100", "unit": "g"}, "source": source}],
    })


def test_document_threshold_has_proven_source_and_no_resolution_issue():
    repo = make_repo()
    definition = make_definition(repo, document=True)
    validation().validate_definition(definition, repo)
    assert validation().rule_supported(definition.criteria[0], definition.items[0])
    assert definition.issues() == []


@pytest.mark.parametrize("change", [
    {"value": "200"}, {"operator": "lte"}, {"unit": "kg"},
])
def test_invented_document_threshold_is_not_executable(change):
    repo = make_repo()
    definition = make_definition(repo, document=True)
    body = definition.model_dump()
    body["criteria"][0]["rule"].update(change)
    changed = ChecklistDefinition.model_validate(body)
    validation().validate_definition(changed, repo)
    assert not validation().rule_supported(changed.criteria[0], changed.items[0])
    assert changed.issues()[0]["code"] == "rule_source_unresolved"


@pytest.mark.parametrize("text", ["梨净重至少100 g", "苹果价格至少100 g", "苹果净重约100 g",
                                      "苹果净重可选100 g或200 g", "苹果净重100 g，且须满足特殊等级"])
def test_wrong_item_name_or_complex_document_rule_stays_unresolved(text):
    repo = make_repo(text)
    definition = make_definition(repo, document=True)
    assert not validation().rule_supported(definition.criteria[0], definition.items[0])


def test_manual_threshold_can_differ_from_its_requirement_context():
    repo = make_repo()
    body = make_definition(repo, document=True).model_dump()
    body["criteria"][0]["source"].update(kind="user_defined", reason="买方修改为200克")
    body["criteria"][0]["rule"]["value"] = "200"
    definition = ChecklistDefinition.model_validate(body)
    validation().validate_definition(definition, repo)
    assert validation().rule_supported(definition.criteria[0], definition.items[0])


@pytest.mark.parametrize("scope", ["supplier", "other_snapshot", "fabricated_quote", "nonadjacent"])
def test_definition_context_rejects_foreign_or_fabricated_evidence(scope):
    repo = make_repo(requirement=[("A1", "苹果净重"), ("C1", "至少100 g")])
    body = make_definition(repo, document=True).model_dump()
    context = body["criteria"][0]["source"]["context"]
    if scope == "supplier":
        context.update(evidence_refs=[repo.blocks[-1].evidence_id], quote=repo.blocks[-1].text)
    elif scope == "other_snapshot":
        other = make_repo(case="other")
        context.update(evidence_refs=[other.blocks[0].evidence_id], quote=other.blocks[0].text)
    elif scope == "fabricated_quote":
        context["quote"] = "苹果净重至少999 g"
    else:
        context.update(evidence_refs=[b.evidence_id for b in repo.blocks[:2]], quote="苹果净重 至少100 g")
    with pytest.raises(ValueError):
        validation().validate_definition(ChecklistDefinition.model_validate(body), repo)


def test_adjacent_requirement_cells_can_form_a_source_quote():
    repo = make_repo(requirement=[("A1", "苹果净重"), ("B1", "至少100 g")])
    body = make_definition(repo, document=True).model_dump()
    body["criteria"][0]["source"]["context"].update(
        evidence_refs=[b.evidence_id for b in repo.blocks[:2]], quote="苹果净重 至少100 g")
    definition = ChecklistDefinition.model_validate(body)
    validation().validate_definition(definition, repo)
    assert validation().rule_supported(definition.criteria[0], definition.items[0])


def test_between_requires_both_source_bounds():
    repo = make_repo("苹果净重100至200 g")
    rule = {"type": "numeric", "operator": "between", "lower": "100", "upper": "200", "unit": "g"}
    definition = make_definition(repo, rule=rule, document=True)
    assert validation().rule_supported(definition.criteria[0], definition.items[0])
    body = deepcopy(definition.model_dump())
    body["criteria"][0]["rule"]["upper"] = "300"
    changed = ChecklistDefinition.model_validate(body)
    assert not validation().rule_supported(changed.criteria[0], changed.items[0])


@pytest.mark.parametrize(("text", "operator"), [("苹果净重不少于100 g", "gte"), ("苹果净重不小于100 g", "gte"), ("苹果净重不大于100 g", "lte")])
def test_negative_chinese_comparator_is_not_misread_as_its_positive_suffix(text, operator):
    repo = make_repo(text)
    definition = make_definition(repo, document=True, rule={"type": "numeric", "operator": operator, "value": "100", "unit": "g"})
    assert validation().rule_supported(definition.criteria[0], definition.items[0])


@pytest.mark.parametrize(("text", "rule"), [
    ("苹果面料不采用纯棉", {"type": "enum", "operator": "eq", "values": ["纯棉"]}),
    ("苹果无检测报告", {"type": "presence", "description": "检测报告", "must_be_present": True}),
])
def test_negated_requirement_does_not_support_positive_enum_or_presence(text, rule):
    repo = make_repo(text)
    definition = make_definition(repo, document=True, rule=rule, name="面料" if rule["type"] == "enum" else "检测报告")
    assert not validation().rule_supported(definition.criteria[0], definition.items[0])


@pytest.mark.parametrize("text", ["苹果净重至少100 g，参数可选", "苹果净重至少100 g；须满足其他特殊条件"])
def test_document_quote_cannot_drop_original_semantic_qualifications(text):
    repo = make_repo(text)
    body = make_definition(repo, document=True).model_dump()
    body["criteria"][0]["source"]["context"]["quote"] = "苹果净重至少100 g"
    with pytest.raises(ValueError, match="requirement_quote_incomplete"):
        validation().validate_definition(ChecklistDefinition.model_validate(body), repo)


@pytest.mark.parametrize(("text", "values", "supported"), [
    ("苹果面料纯棉或羊毛", ["纯棉", "羊毛"], True),
    ("苹果面料纯棉或涤纶", ["纯棉"], False),
    ("苹果面料含纯棉", ["纯棉"], False),
])
def test_document_enum_in_only_accepts_an_explicit_finite_choice(text, values, supported):
    repo = make_repo(text)
    definition = make_definition(repo, document=True, name="面料", rule={"type": "enum", "operator": "in", "values": values})
    assert validation().rule_supported(definition.criteria[0], definition.items[0]) is supported


def test_document_presence_with_no_requirement_is_unresolved():
    repo = make_repo("苹果检测报告无要求")
    definition = make_definition(repo, document=True, name="检测报告", rule={"type": "presence", "description": "检测报告"})
    assert not validation().rule_supported(definition.criteria[0], definition.items[0])


def test_contract_agreement_word_does_not_make_an_exact_threshold_approximate():
    repo = make_repo("苹果合同约定净重至少100 g")
    definition = make_definition(repo, document=True)
    assert validation().rule_supported(definition.criteria[0], definition.items[0])


@pytest.mark.parametrize(("text", "rule", "name"), [
    ("苹果单价含税不超过10 美元/斤", {"type": "money", "operator": "lte", "value": "10", "currency": "USD", "per_unit": "斤", "tax_basis": "included"}, "单价"),
    ("苹果单价含税不超过10 欧元/斤", {"type": "money", "operator": "lte", "value": "10", "currency": "EUR", "per_unit": "斤", "tax_basis": "included"}, "单价"),
    ("苹果交期合同签订后不超过10自然日", {"type": "duration", "operator": "lte", "value": "10", "calendar": "natural_days", "start_event": "合同签订"}, "交期"),
])
def test_document_money_and_duration_have_explicit_supported_basis(text, rule, name):
    repo = make_repo(text)
    definition = make_definition(repo, document=True, name=name, rule=rule)
    assert validation().rule_supported(definition.criteria[0], definition.items[0])


def test_duration_document_measure_word_is_supported_only_under_the_new_semantics():
    repo = make_repo('苹果交期订单确认后不超过14个自然日')
    definition = make_definition(repo, document=True, name='交期', rule={'type':'duration',
        'operator':'lte', 'value':'14', 'calendar':'natural_days', 'start_event':'订单确认'})
    assert validation().rule_supported(definition.criteria[0], definition.items[0])
    assert not validation().rule_supported(definition.criteria[0], definition.items[0], legacy_duration=True)


def test_duration_document_with_competing_start_events_cannot_be_formalized():
    repo = make_repo('苹果交期订单确认后不超过14个自然日内交付并以收到付款为起算点')
    definition = make_definition(repo, document=True, name='交期', rule={'type':'duration',
        'operator':'lte', 'value':'14', 'calendar':'natural_days', 'start_event':'订单确认'})
    assert not validation().rule_supported(definition.criteria[0], definition.items[0])


@pytest.mark.parametrize(("operator", "supported"), [("eq", True), ("lte", False)])
def test_newline_preserves_comparator_association_with_its_metric(operator, supported):
    repo = make_repo("苹果净重100 g\n苹果价格不超过20 元/斤")
    definition = make_definition(repo, document=True, rule={"type": "numeric", "operator": operator, "value": "100", "unit": "g"})
    validation().validate_definition(definition, repo)
    assert validation().rule_supported(definition.criteria[0], definition.items[0]) is supported


def test_document_quote_cannot_flatten_structural_newlines_between_metrics():
    repo = make_repo("苹果净重100 g\n苹果价格不超过20 元/斤")
    body = make_definition(repo, document=True).model_dump()
    body["criteria"][0]["source"]["context"]["quote"] = "苹果净重100 g 苹果价格不超过20 元/斤"
    with pytest.raises(ValueError, match="requirement_quote_incomplete"):
        validation().validate_definition(ChecklistDefinition.model_validate(body), repo)
