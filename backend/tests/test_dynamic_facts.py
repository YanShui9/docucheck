"""Dynamic fact validation and finite rules, independent of legacy fields."""

import importlib
from copy import deepcopy

import pytest
from pydantic import ValidationError

from backend.app.criteria.models import ChecklistDefinition
from backend.tests.test_dynamic_requirements import make_definition, make_repo


def facts_module():
    assert importlib.util.find_spec("backend.app.criteria.facts"), "dynamic facts missing"
    return importlib.import_module("backend.app.criteria.facts")


def candidate(repo, value="120", unit="g", *, index=-1, **updates):
    block = repo.blocks[index]
    return {"criterion_id": "weight", "item_id": "apple", "supplier": block.supplier,
            "value": value, "unit": unit, "evidence_refs": [block.evidence_id],
            "quote": block.text, **updates}


def checked(repo, definition=None, body=None):
    return facts_module().validate_fact(body or candidate(repo), definition or make_definition(), repo, "A")


def evaluate(repo, definition, facts, coverage=None):
    return facts_module().evaluate_checklist(definition, facts, coverage or {"A": True}, snapshot_id=repo.snapshot_id)


def test_fact_id_and_metadata_are_server_owned_and_replayable():
    repo = make_repo()
    fact = checked(repo)
    assert len(fact["fact_id"]) == 64
    assert fact["snapshot_id"] == repo.snapshot_id and fact["case_id"] == repo.case_id
    assert fact["comparator"] == "eq" and fact["ambiguous"] is False
    assert facts_module().revalidate_fact(fact, make_definition(), repo, "A") == fact
    tampered = fact | {"fact_id": "forged"}
    with pytest.raises(ValueError, match="fact_integrity"):
        facts_module().revalidate_fact(tampered, make_definition(), repo, "A")


@pytest.mark.parametrize(('unit', 'calendar', 'expected'), [
    ('自然日', 'natural_days', '不满足'),
    ('个自然日', 'natural_days', '不满足'),
    ('工作日', 'working_days', '待人工确认'),
    ('个工作日', 'working_days', '待人工确认'),
])
def test_chinese_duration_measure_word_preserves_value_and_calendar(unit, calendar, expected):
    day_word = '工作日' if calendar == 'working_days' else '自然日'
    repo = make_repo(offers=[('A', f'苹果交期订单确认后18个{day_word}内交付', None)])
    definition = make_definition(name='交期', rule={'type':'duration', 'operator':'lte',
        'value':'14', 'calendar':'natural_days', 'start_event':'订单确认'})
    fact = checked(repo, definition, candidate(repo, '18', unit, calendar=calendar, start_event='订单确认'))
    assert not fact['ambiguous'] and fact['calendar'] == calendar
    assert evaluate(repo, definition, [fact])[0]['business_status'] == expected
    assert facts_module().revalidate_fact(fact, definition, repo, 'A') == fact


def test_duration_does_not_ignore_an_explicit_start_condition_after_the_number():
    repo = make_repo(offers=[('A', '苹果交期订单确认后18自然日内交付并以收到付款为起算点', None)])
    definition = make_definition(name='交期', rule={'type':'duration', 'operator':'lte',
        'value':'20', 'calendar':'natural_days', 'start_event':'订单确认'})
    fact = checked(repo, definition, candidate(repo, '18', '自然日'))
    assert fact['ambiguous'] and fact['start_event'] is None
    assert evaluate(repo, definition, [fact])[0]['business_status'] == '待人工确认'


@pytest.mark.parametrize('text', [
    '苹果订单确认后交付期限不超过10自然日',
    '苹果订单确认后交付时间不超过10自然日',
    '苹果交付后交付期限不超过10自然日',
])
def test_duration_delivery_metric_label_is_not_a_second_start_event(text):
    event = '交付' if '交付后' in text else '订单确认'
    repo = make_repo(offers=[('A', text, None)])
    definition = make_definition(name='交付期限', rule={'type': 'duration', 'operator': 'lte',
        'value': '14', 'calendar': 'natural_days', 'start_event': event})
    # Matching the source metric is required independently of event parsing.
    if '交付时间' in text:
        body = definition.model_dump()
        body['criteria'][0]['name'] = '交付时间'
        definition = ChecklistDefinition.model_validate(body)
    fact = checked(repo, definition, candidate(repo, '10', '自然日'))
    assert not fact['ambiguous'] and fact['start_event'] == event
    assert evaluate(repo, definition, [fact])[0]['business_status'] == '满足'


def test_duration_v1_fact_and_checks_keep_their_original_semantics():
    repo = make_repo(offers=[('A', '苹果交期订单确认后18自然日内交付', None)])
    definition = make_definition(name='交期', rule={'type':'duration', 'operator':'lte',
        'value':'14', 'calendar':'natural_days', 'start_event':'订单确认'})
    body = candidate(repo, '18', '自然日')
    old = facts_module().validate_fact(body, definition, repo, 'A', rule_version='dynamic-rules-v1')
    assert old['ambiguous'] and old['start_event'] is None
    assert facts_module().revalidate_fact(old, definition, repo, 'A', rule_version='dynamic-rules-v1') == old
    old_checks = facts_module().evaluate_checklist(definition, [old], {'A':True},
        snapshot_id=repo.snapshot_id, rule_version='dynamic-rules-v1')
    assert old_checks[0]['business_status'] == '待人工确认'
    assert old_checks[0]['rule_version'] == 'dynamic-rules-v1'
    current = checked(repo, definition, body)
    assert evaluate(repo, definition, [current])[0]['business_status'] == '不满足'


def test_duration_measure_word_fix_does_not_relax_numeric_exact_units():
    repo = make_repo(offers=[('A', '苹果期限18个月', None)])
    definition = make_definition(name='期限', rule={'type':'numeric', 'operator':'eq', 'value':'18', 'unit':'月'})
    with pytest.raises(ValueError, match='fact_value'):
        checked(repo, definition, candidate(repo, '18', '月'))


@pytest.mark.parametrize("text", ["苹果 数量25箱", "苹果 数 量25箱"])
def test_chinese_metric_name_accepts_layout_spaces_in_exact_source(text):
    repo = make_repo(offers=[("A", text, None)])
    definition = make_definition(name="苹果数量", rule={
        "type": "numeric", "operator": "gte", "value": "20", "unit": "箱"})
    fact = checked(repo, definition, candidate(repo, "25", "箱"))
    assert evaluate(repo, definition, [fact])[0]["business_status"] == "满足"
    assert fact["quote"] == text


@pytest.mark.parametrize("text", ["苹果，数量25箱", "苹果价格25箱", "苹果 数量25箱；苹果 数量30箱"])
def test_metric_layout_spaces_do_not_join_clauses_or_accept_other_metrics(text):
    repo = make_repo(offers=[("A", text, None)])
    definition = make_definition(name="苹果数量", rule={
        "type": "numeric", "operator": "gte", "value": "20", "unit": "箱"})
    with pytest.raises(ValueError, match="fact_criterion"):
        checked(repo, definition, candidate(repo, "25", "箱"))


def test_exact_metric_with_other_layout_clause_keeps_historical_ambiguity():
    repo = make_repo(offers=[("A", "苹果 数量25箱；苹果数量30箱", None)])
    definition = make_definition(name="苹果数量", rule={
        "type": "numeric", "operator": "gte", "value": "20", "unit": "箱"})
    fact = checked(repo, definition, candidate(repo, "30", "箱"))
    assert fact["ambiguous"] is True
    assert evaluate(repo, definition, [fact])[0]["business_status"] == "待人工确认"
    assert facts_module().revalidate_fact(fact, definition, repo, "A") == fact


@pytest.mark.parametrize("updates", [
    {"fact_id": "forged"}, {"comparator": "eq"}, {"value": 120}, {"unit": 2},
    {"evidence_refs": []}, {"quote": "  "}, {"supplier": " A "},
])
def test_candidate_rejects_model_metadata_coercion_and_empty_evidence(updates):
    repo = make_repo()
    with pytest.raises(ValidationError):
        facts_module().FactCandidate.model_validate(candidate(repo, **updates))


@pytest.mark.parametrize("scope", ["unknown_criterion", "unknown_item", "supplier", "requirement", "snapshot"])
def test_facts_reject_foreign_supplier_snapshot_requirement_or_identity(scope):
    repo = make_repo(offers=[("A", "苹果净重120 g", None), ("B", "苹果净重120 g", None)])
    body = candidate(repo, index=1)
    if scope == "unknown_criterion":
        body["criterion_id"] = "missing"
    elif scope == "unknown_item":
        body["item_id"] = "pear"
    elif scope == "supplier":
        body = candidate(repo)
    elif scope == "requirement":
        body = candidate(repo, index=0, supplier="A")
    else:
        body = candidate(make_repo(case="other"))
    with pytest.raises(ValueError):
        checked(repo, body=body)


@pytest.mark.parametrize("body_change", [{"value": "999"}, {"unit": "kg"},
                                         {"quote": "苹果净重999 g"}])
def test_model_cannot_invent_value_unit_or_quote(body_change):
    repo = make_repo()
    with pytest.raises(ValueError):
        checked(repo, body=candidate(repo, **body_change))


def test_adjacent_cells_are_supported_but_nonadjacent_cells_cannot_be_joined():
    repo = make_repo(offers=[("A", [("A1", "苹果净重"), ("B1", "120 g")], None)])
    refs = [block.evidence_id for block in repo.blocks[1:]]
    body = candidate(repo, evidence_refs=refs, quote="苹果净重 120 g")
    assert checked(repo, body=body)["value"] == "120"
    foreign = make_repo(offers=[("A", [("A1", "苹果净重"), ("C1", "120 g")], None)])
    body.update(evidence_refs=[b.evidence_id for b in foreign.blocks[1:]])
    with pytest.raises(ValueError, match="evidence_relationship"):
        checked(foreign, body=body)


def test_multi_item_fact_must_name_the_applicable_item():
    repo = make_repo(offers=[("A", "净重120 g", None)])
    body = make_definition().model_dump()
    body["items"].append({"item_id": "pear", "name": "梨"})
    definition = ChecklistDefinition.model_validate(body)
    with pytest.raises(ValueError, match="fact_item"):
        checked(repo, definition)


@pytest.mark.parametrize(("rule", "text", "value", "unit", "status"), [
    ({"type": "numeric", "operator": "gte", "value": "100", "unit": "g"}, "苹果净重120 g", "120", "g", "满足"),
    ({"type": "numeric", "operator": "gt", "value": "100", "unit": "g"}, "苹果净重100 g", "100", "g", "不满足"),
    ({"type": "numeric", "operator": "between", "lower": "100", "upper": "200", "unit": "g"}, "苹果净重0.12 kg", "0.12", "kg", "满足"),
    ({"type": "numeric", "operator": "eq", "value": "0.1", "unit": "g"}, "苹果净重0.1 g", "0.1", "g", "满足"),
    ({"type": "numeric", "operator": "gte", "value": "100", "unit": "g"}, "苹果净重120 袋", "120", "袋", "待人工确认"),
])
def test_generic_numeric_rules_use_decimal_and_finite_unit_conversion(rule, text, value, unit, status):
    repo = make_repo(offers=[("A", text, None)])
    definition = make_definition(rule=rule)
    checks = evaluate(repo, definition, [checked(repo, definition, candidate(repo, value, unit))])
    assert checks[0]["business_status"] == status
    assert checks[0]["field_key"] == checks[0]["criterion_id"] == "weight"
    assert checks[0]["rule_version"] == "dynamic-rules-v2"


def test_complete_review_enumerates_all_enabled_criteria_and_suppliers():
    repo = make_repo()
    body = make_definition().model_dump()
    body["criteria"] = [body["criteria"][0] | {"criterion_id": f"metric-{n}"} for n in range(12)]
    body["criteria"][-1]["enabled"] = False
    definition = ChecklistDefinition.model_validate(body)
    checks = evaluate(repo, definition, [], {"A": True, "B": True})
    assert len(checks) == 22 and {c["supplier"] for c in checks} == {"A", "B"}
    assert all(c["business_status"] == "证据不足" for c in checks)


def test_incomplete_coverage_keeps_facts_but_never_formal_conclusion():
    repo = make_repo()
    result = evaluate(repo, make_definition(), [checked(repo)], {"A": False})[0]
    assert result["processing_status"] == "partial" and result["business_status"] is None
    assert result["is_formal"] is False and result["evidence_refs"]


def test_evaluator_rejects_another_snapshot_fact():
    repo = make_repo()
    foreign = checked(make_repo(case="other"))
    with pytest.raises(ValueError, match="fact_snapshot"):
        evaluate(repo, make_definition(), [foreign])


@pytest.mark.parametrize("wording", ["可选120 g或200 g", "约120 g", "最大120 g", "至少120 g"])
def test_optional_approximate_maximum_or_nonnumeric_guarantees_cannot_be_dangerous_pass(wording):
    repo = make_repo(offers=[("A", f"苹果净重{wording}", None)])
    assert evaluate(repo, make_definition(), [checked(repo)])[0]["business_status"] == "待人工确认"


def test_same_source_multiple_values_are_retained_as_conflict():
    repo = make_repo(offers=[("A", "苹果净重120 g或200 g", None)])
    values = [checked(repo, body=candidate(repo, value)) for value in ("120", "200")]
    result = evaluate(repo, make_definition(), values)[0]
    assert result["business_status"] == "资料冲突" and len(result["fact_ids"]) == 2


def test_two_supplier_documents_with_different_values_are_a_conflict():
    repo = make_repo(offers=[("A", "苹果净重120 g", None), ("A", "苹果净重200 g", None)])
    values = [checked(repo, body=candidate(repo, "120", index=1)), checked(repo, body=candidate(repo, "200"))]
    assert evaluate(repo, make_definition(), values)[0]["business_status"] == "资料冲突"


def test_different_supplier_models_stay_manual_even_with_matching_values():
    repo = make_repo(offers=[("A", "苹果净重120 g", "X1"), ("A", "苹果净重120 g", "X2")])
    values = [checked(repo, body=candidate(repo, index=n)) for n in (1, 2)]
    assert evaluate(repo, make_definition(), values)[0]["business_status"] == "待人工确认"


@pytest.mark.parametrize("manual", ["rule", "conditions"])
def test_manual_or_conditional_requirements_remain_manual(manual):
    repo = make_repo()
    body = make_definition().model_dump()
    if manual == "rule":
        body["criteria"][0]["rule"] = {"type": "manual", "instruction": "人工核验包装等级"}
    else:
        body["criteria"][0]["conditions"] = ["仅限一级果"]
    definition = ChecklistDefinition.model_validate(body)
    assert evaluate(repo, definition, [checked(repo, definition)])[0]["business_status"] == "待人工确认"


@pytest.mark.parametrize(("text", "extra", "status"), [
    ("苹果单价含税10 元/斤", {"currency": "CNY", "per_unit": "斤", "tax_basis": "included"}, "满足"),
    ("苹果单价未含税10 元/斤", {"currency": "CNY", "per_unit": "斤", "tax_basis": "excluded"}, "待人工确认"),
    ("苹果单价含税10 美元/斤", {"currency": "USD", "per_unit": "斤", "tax_basis": "included"}, "待人工确认"),
    ("苹果单价含税10 元/箱", {"currency": "CNY", "per_unit": "箱", "tax_basis": "included"}, "待人工确认"),
])
def test_money_cannot_compare_different_tax_currency_or_pricing_basis(text, extra, status):
    repo = make_repo(offers=[("A", text, None)])
    rule = {"type": "money", "operator": "lte", "value": "12", "currency": "CNY",
            "per_unit": "斤", "tax_basis": "included"}
    definition = make_definition(rule=rule, name="单价")
    unit = "美元/斤" if extra["currency"] == "USD" else f"元/{extra['per_unit']}"
    fact = checked(repo, definition, candidate(repo, "10", unit, **extra))
    assert evaluate(repo, definition, [fact])[0]["business_status"] == status


def test_money_model_cannot_claim_tax_basis_absent_from_source():
    repo = make_repo(offers=[("A", "苹果单价10 元/斤", None)])
    definition = make_definition(rule={"type": "money", "operator": "lte", "value": "12",
                                      "currency": "CNY", "per_unit": "斤", "tax_basis": "included"}, name="单价")
    with pytest.raises(ValueError, match="fact_tax_basis"):
        checked(repo, definition, candidate(repo, "10", "元/斤", tax_basis="included"))


@pytest.mark.parametrize(("text", "value", "unit", "extra", "status"), [
    ("苹果交期合同签订后不超过7自然日", "7", "自然日", {}, "满足"),
    ("苹果交期合同签订后不超过12自然日", "12", "自然日", {}, "不满足"),
    ("苹果交期合同签订后7工作日", "7", "工作日", {}, "待人工确认"),
    ("苹果交期收到预付款后7自然日", "7", "自然日", {"start_event": "收到预付款"}, "待人工确认"),
    ("苹果交期7自然日", "7", "自然日", {}, "待人工确认"),
])
def test_delivery_commitments_and_duration_basis_are_source_derived(text, value, unit, extra, status):
    repo = make_repo(offers=[("A", text, None)])
    definition = make_definition(rule={"type": "duration", "operator": "lte", "value": "10",
                                      "calendar": "natural_days", "start_event": "合同签订"}, name="交期")
    fact = checked(repo, definition, candidate(repo, value, unit, **extra))
    assert evaluate(repo, definition, [fact])[0]["business_status"] == status


def test_duration_model_cannot_invent_start_event():
    repo = make_repo(offers=[("A", "苹果交期7自然日", None)])
    definition = make_definition(rule={"type": "duration", "operator": "lte", "value": "10",
                                      "calendar": "natural_days", "start_event": "合同签订"}, name="交期")
    with pytest.raises(ValueError, match="fact_start_event"):
        checked(repo, definition, candidate(repo, "7", "自然日", start_event="合同签订"))


@pytest.mark.parametrize(("rule", "name", "text", "value", "status"), [
    ({"type": "enum", "operator": "in", "values": ["纯棉", "羊毛"]}, "面料", "衬衫面料纯棉", "纯棉", "满足"),
    ({"type": "enum", "operator": "eq", "values": ["纯棉"]}, "面料", "衬衫面料涤纶", "涤纶", "不满足"),
    ({"type": "presence", "description": "检测报告", "must_be_present": True}, "检测报告", "衬衫提供检测报告", True, "满足"),
    ({"type": "presence", "description": "检测报告", "must_be_present": True}, "检测报告", "衬衫无检测报告", False, "不满足"),
])
def test_clothing_enum_and_presence_are_not_limited_to_electronic_fields(rule, name, text, value, status):
    repo = make_repo(offers=[("A", text, None)])
    definition = make_definition(rule=rule, name=name, item_name="衬衫")
    fact = checked(repo, definition, candidate(repo, value, ""))
    assert evaluate(repo, definition, [fact])[0]["business_status"] == status


def test_result_ids_bind_the_requirement_definition():
    repo = make_repo()
    definition = make_definition()
    fact = checked(repo)
    body = deepcopy(definition.model_dump())
    body["criteria"][0]["rule"]["value"] = "200"
    changed = ChecklistDefinition.model_validate(body)
    assert evaluate(repo, definition, [fact])[0]["check_result_id"] != evaluate(repo, changed, [fact])[0]["check_result_id"]


def test_another_metric_value_cannot_be_attached_to_the_named_criterion():
    repo = make_repo(offers=[("A", "苹果净重0.09 kg，包装重量120 g", None)])
    with pytest.raises(ValueError, match="fact_value"):
        checked(repo)


@pytest.mark.parametrize("text", ["衬衫面料不是纯棉", "衬衫面料不采用纯棉", "衬衫面料非纯棉"])
def test_negated_enum_value_cannot_be_extracted_as_positive_value(text):
    repo = make_repo(offers=[("A", text, None)])
    definition = make_definition(rule={"type": "enum", "operator": "eq", "values": ["纯棉"]},
                                 name="面料", item_name="衬衫")
    with pytest.raises(ValueError, match="fact_value"):
        checked(repo, definition, candidate(repo, "纯棉", ""))


@pytest.mark.parametrize("text", ["衬衫不具备检测报告", "衬衫检测报告未提供"])
def test_presence_negation_before_or_after_name_is_preserved(text):
    repo = make_repo(offers=[("A", text, None)])
    definition = make_definition(rule={"type": "presence", "description": "检测报告", "must_be_present": True},
                                 name="检测报告", item_name="衬衫")
    fact = checked(repo, definition, candidate(repo, False, ""))
    assert evaluate(repo, definition, [fact])[0]["business_status"] == "不满足"


@pytest.mark.parametrize("text", ["衬衫可能提供检测报告", "衬衫计划提供检测报告", "衬衫检测报告不完整"])
def test_uncertain_presence_is_never_a_formal_pass(text):
    repo = make_repo(offers=[("A", text, None)])
    definition = make_definition(rule={"type": "presence", "description": "检测报告", "must_be_present": True},
                                 name="检测报告", item_name="衬衫")
    fact = checked(repo, definition, candidate(repo, True, ""))
    assert evaluate(repo, definition, [fact])[0]["business_status"] == "待人工确认"


def test_bound_electronic_model_disambiguates_multiple_items():
    repo = make_repo(offers=[("A", "型号X1内存32 GB", "X1")])
    body = make_definition(name="内存", item_name="电脑", rule={"type": "numeric", "operator": "gte", "value": "16", "unit": "GB"}).model_dump()
    body["items"][0]["specification"] = "型号X1"
    body["items"].append({"item_id": "tablet", "name": "平板", "specification": "型号T1"})
    definition = ChecklistDefinition.model_validate(body)
    fact = checked(repo, definition, candidate(repo, "32", "GB"))
    assert evaluate(repo, definition, [fact])[0]["business_status"] == "满足"


def test_binary_and_decimal_capacity_are_not_interchangeable():
    repo = make_repo(offers=[("A", "电脑内存32 GiB", None)])
    definition = make_definition(name="内存", item_name="电脑", rule={"type": "numeric", "operator": "gte", "value": "16", "unit": "GB"})
    fact = checked(repo, definition, candidate(repo, "32", "GiB"))
    assert evaluate(repo, definition, [fact])[0]["business_status"] == "待人工确认"


@pytest.mark.parametrize("text", ["苹果净重120 g；参数可选", "苹果净重120 g，200 g"])
def test_supplier_quote_cannot_drop_optional_qualifications_or_second_value(text):
    repo = make_repo(offers=[("A", text, None)])
    fact = checked(repo, body=candidate(repo, quote="苹果净重120 g"))
    assert evaluate(repo, make_definition(), [fact])[0]["business_status"] == "待人工确认"


@pytest.mark.parametrize("text", ["衬衫面料含纯棉", "衬衫面料纯棉和涤纶"])
def test_partial_enum_membership_cannot_prove_enum_equality(text):
    repo = make_repo(offers=[("A", text, None)])
    definition = make_definition(rule={"type": "enum", "operator": "eq", "values": ["纯棉"]}, name="面料", item_name="衬衫")
    fact = checked(repo, definition, candidate(repo, "纯棉", ""))
    assert evaluate(repo, definition, [fact])[0]["business_status"] == "待人工确认"


@pytest.mark.parametrize("kind", ["money", "duration"])
def test_negative_money_or_duration_cannot_be_a_valid_offer(kind):
    rule = ({"type": "money", "operator": "lte", "value": "10", "currency": "CNY", "per_unit": "斤", "tax_basis": "included"}
            if kind == "money" else {"type": "duration", "operator": "lte", "value": "10", "calendar": "natural_days", "start_event": "合同签订"})
    text, name, unit = (("苹果单价含税-1 元/斤", "单价", "元/斤") if kind == "money"
                        else ("苹果交期合同签订后-1自然日", "交期", "自然日"))
    repo = make_repo(offers=[("A", text, None)])
    definition = make_definition(rule=rule, name=name)
    with pytest.raises(ValueError, match="fact_value"):
        checked(repo, definition, candidate(repo, "-1", unit))


def test_source_bit_unit_cannot_be_relabelled_as_bytes():
    repo = make_repo(offers=[("A", "电脑内存32 Gb", None)])
    definition = make_definition(name="内存", item_name="电脑", rule={"type": "numeric", "operator": "gte", "value": "16", "unit": "GB"})
    with pytest.raises(ValueError, match="fact_value"):
        checked(repo, definition, candidate(repo, "32", "GB"))
    fact = checked(repo, definition, candidate(repo, "32", "Gb"))
    assert evaluate(repo, definition, [fact])[0]["business_status"] == "待人工确认"


def test_exact_unknown_unit_comparison_does_not_apply_a_registry_scale():
    repo = make_repo(offers=[("A", "电脑内存32 Gb", None)])
    definition = make_definition(name="内存", item_name="电脑", rule={"type": "numeric", "operator": "gte", "value": "16", "unit": "Gb"})
    fact = checked(repo, definition, candidate(repo, "32", "Gb"))
    assert evaluate(repo, definition, [fact])[0]["business_status"] == "满足"


@pytest.mark.parametrize(("text", "value", "unit"), [
    ("苹果合同签订后交期2周（14自然日）", "2", "周"),
    ("苹果合同签订后交期2周（14自然日）", "14", "自然日"),
    ("苹果合同签订后交期2 kg（14自然日）", "2", "kg"),
    ("苹果合同签订后交期2 kg（14自然日）", "14", "自然日"),
    ("苹果合同签订后交期2自然周（14自然日）", "2", "自然周"),
    ("苹果合同签订后交期2年（14自然日）", "2", "年"),
    ("苹果合同签订后交期7天（8自然日）", "7", "天"),
])
def test_duration_never_compares_week_or_pseudo_unit_number_as_raw_day_count(text, value, unit):
    repo = make_repo(offers=[("A", text, None)])
    definition = make_definition(name="交期", rule={"type": "duration", "operator": "lte", "value": "7", "calendar": "natural_days", "start_event": "合同签订"})
    fact = checked(repo, definition, candidate(repo, value, unit))
    assert fact["ambiguous"] is True
    assert evaluate(repo, definition, [fact])[0]["business_status"] == "待人工确认"
    assert facts_module().revalidate_fact(fact, definition, repo, "A") == fact


@pytest.mark.parametrize(("unit", "calendar"), [("天", "natural_days"), ("日", "natural_days"),
                                                ("自然日", "natural_days"), ("工作日", "working_days"),
                                                ("月", "months"), ("个月", "months")])
def test_duration_uses_only_finite_unit_calendar_pairs(unit, calendar):
    repo = make_repo(offers=[("A", f"苹果合同签订后交期2{unit}", None)])
    definition = make_definition(name="交期", rule={"type": "duration", "operator": "lte", "value": "7", "calendar": calendar, "start_event": "合同签订"})
    fact = checked(repo, definition, candidate(repo, "2", unit))
    assert fact["calendar"] == calendar and fact["ambiguous"] is False
    assert evaluate(repo, definition, [fact])[0]["business_status"] == "满足"


def test_duration_model_cannot_claim_days_for_a_week_unit():
    repo = make_repo(offers=[("A", "苹果合同签订后交期2周（14自然日）", None)])
    definition = make_definition(name="交期", rule={"type": "duration", "operator": "lte", "value": "7", "calendar": "natural_days", "start_event": "合同签订"})
    with pytest.raises(ValueError, match="fact_calendar"):
        checked(repo, definition, candidate(repo, "2", "周", calendar="natural_days"))


def test_planned_presence_is_a_claim_to_review_rather_than_proven_provision():
    repo = make_repo(offers=[("A", "衬衫拟提供检测报告", None)])
    definition = make_definition(rule={"type": "presence", "description": "检测报告"}, name="检测报告", item_name="衬衫")
    fact = checked(repo, definition, candidate(repo, True, ""))
    assert evaluate(repo, definition, [fact])[0]["business_status"] == "待人工确认"


@pytest.mark.parametrize("text", ["梨净重120 g", "净重120 g"])
def test_single_item_fact_also_requires_proven_item_association(text):
    repo = make_repo(offers=[("A", text, None)])
    fact = checked(repo)
    assert fact["applicable"] is False and fact["ambiguous"] is True
    assert evaluate(repo, make_definition(), [fact])[0]["business_status"] == "待人工确认"


def test_document_model_binding_can_prove_item_without_repeating_the_item_name():
    repo = make_repo(offers=[("A", "内存32 GB", "X1")])
    body = make_definition(name="内存", item_name="电脑", rule={"type": "numeric", "operator": "gte", "value": "16", "unit": "GB"}).model_dump()
    body["items"][0]["specification"] = "型号X1"
    body["items"].append({"item_id": "tablet", "name": "平板", "specification": "型号T1"})
    definition = ChecklistDefinition.model_validate(body)
    fact = checked(repo, definition, candidate(repo, "32", "GB"))
    assert fact["applicable"] is True and fact["ambiguous"] is False
    assert evaluate(repo, definition, [fact])[0]["business_status"] == "满足"


@pytest.mark.parametrize("text", ["型号X2内存32 GB", "电脑型号X2内存32 GB"])
def test_explicit_foreign_model_cannot_override_a_document_model_binding(text):
    repo = make_repo(offers=[("A", text, "X1")])
    body = make_definition(name="内存", item_name="电脑", rule={"type": "numeric", "operator": "gte", "value": "16", "unit": "GB"}).model_dump()
    body["items"][0]["specification"] = "型号X1"
    definition = ChecklistDefinition.model_validate(body)
    fact = checked(repo, definition, candidate(repo, "32", "GB"))
    assert fact["applicable"] is False and fact["ambiguous"] is True
    assert evaluate(repo, definition, [fact])[0]["business_status"] == "待人工确认"


def test_same_metric_name_on_two_items_has_distinct_item_labels_and_ids():
    repo = make_repo()
    body = make_definition(name="数量", rule={"type": "numeric", "operator": "gte", "value": "100", "unit": "个"}).model_dump()
    body["items"].append({"item_id": "pear", "name": "梨"})
    body["criteria"].append(body["criteria"][0] | {"criterion_id": "pear-quantity", "item_id": "pear"})
    definition = ChecklistDefinition.model_validate(body)
    checks = evaluate(repo, definition, [])
    assert [(check["item_id"], check["item_name"], check["criterion_id"], check["criterion_name"])
            for check in checks] == [("apple", "苹果", "weight", "数量"),
                                    ("pear", "梨", "pear-quantity", "数量")]
    assert checks[0]["check_result_id"] != checks[1]["check_result_id"]
