"""Current numeric rules fail closed; historical rule identities stay frozen."""
import os
import subprocess
import sys

import pytest

from backend.app.criteria.facts import (
    BINDING_RULE_VERSION,
    RULE_VERSION,
    evaluate_checklist,
    revalidate_fact,
    validate_fact,
)
from backend.tests.test_dynamic_facts import candidate
from backend.tests.test_dynamic_requirements import make_definition, make_repo
from backend.tests.test_item_binding_review import candidate as bound_candidate
from backend.tests.test_item_binding_review import sample
from scripts.verify_offline import sanitized_env


@pytest.mark.parametrize("item,unit,wording", [
    ("空调", "台", "不足10台"), ("办公椅", "把", "不到10把"),
    ("布料", "米", "不满10米"), ("电脑", "台", "不提供10台"),
    ("灯具", "套", "未达到10套"), ("电池", "块", "10块并非承诺"),
    ("打印机", "台", "可达10台"), ("工具", "套", "10套；不能保证供货"),
    ("空调", "台", "约10台"), ("空调", "台", "10台以内"),
    ("空调", "台", "不 超 过10台"), ("空调", "台", "至 多10台"),
    ("空调", "台", "10台以 内"), ("空调", "台", "atmost10台"),
])
def test_unresolved_qualifier_is_never_an_exact_offer(item, unit, wording):
    text = f"{item}数量{wording}"
    repo = make_repo(offers=[("A", text, None)])
    definition = make_definition(item_name=item, name="数量", rule={
        "type": "numeric", "operator": "eq", "value": "10", "unit": unit})
    fact = validate_fact(candidate(repo, "10", unit), definition, repo, "A")
    results = evaluate_checklist(definition, [fact], {"A": True}, snapshot_id=repo.snapshot_id)
    assert results[0]["business_status"] == "待人工确认"
    assert fact["ambiguous"] or fact["comparator"] != "eq"


@pytest.mark.parametrize("item,unit", [("空调", "台"), ("办公椅", "把"), ("布料", "米")])
def test_plain_positive_values_remain_generic(item, unit):
    repo = make_repo(offers=[("A", f"{item}数量为10{unit}", None)])
    definition = make_definition(item_name=item, name="数量", rule={
        "type": "numeric", "operator": "eq", "value": "10", "unit": unit})
    fact = validate_fact(candidate(repo, "10", unit), definition, repo, "A")
    assert not fact["ambiguous"] and fact["comparator"] == "eq"
    assert evaluate_checklist(definition, [fact], {"A": True}, snapshot_id=repo.snapshot_id)[0]["business_status"] == "满足"


def test_previous_rule_identity_replays_without_silently_changing_fact_hash():
    repo = make_repo(offers=[("A", "空调数量不足10台", None)])
    definition = make_definition(item_name="空调", name="数量", rule={
        "type": "numeric", "operator": "eq", "value": "10", "unit": "台"})
    old = validate_fact(candidate(repo, "10", "台"), definition, repo, "A", rule_version="dynamic-rules-v3")
    assert RULE_VERSION != "dynamic-rules-v3"
    assert revalidate_fact(old, definition, repo, "A", rule_version="dynamic-rules-v3") == old
    assert old["comparator"] == "eq" and not old["ambiguous"]


def test_document_requirement_negation_is_not_an_equality_rule():
    repo = make_repo("空调数量不足10台", offers=[("A", "空调数量10台", None)])
    definition = make_definition(repo, item_name="空调", name="数量", document=True, rule={
        "type": "numeric", "operator": "eq", "value": "10", "unit": "台"})
    fact = validate_fact(candidate(repo, "10", "台"), definition, repo, "A")
    assert evaluate_checklist(definition, [fact], {"A": True}, snapshot_id=repo.snapshot_id)[0]["business_status"] == "待人工确认"


def test_qualified_range_requirement_cannot_be_reinterpreted_as_plain_between():
    repo = make_repo("空调数量小于5至10台", offers=[("A", "空调数量8台", None)])
    definition = make_definition(repo, item_name="空调", name="数量", document=True, rule={
        "type": "numeric", "operator": "between", "lower": "5", "upper": "10", "unit": "台"})
    fact = validate_fact(candidate(repo, "8", "台"), definition, repo, "A")
    assert evaluate_checklist(definition, [fact], {"A": True}, snapshot_id=repo.snapshot_id)[0]["business_status"] == "待人工确认"


@pytest.mark.parametrize("basis,unit", [("不 含税", "元/箱"), ("未 含税", "元/箱"), ("含税", "美 元/箱")])
def test_money_qualifications_use_the_same_normalization_as_the_grammar(basis, unit):
    repo = make_repo(offers=[("A", f"空调单价{basis}80{unit}", None)])
    definition = make_definition(item_name="空调", name="单价", rule={
        "type": "money", "operator": "lte", "value": "100", "currency": "CNY",
        "per_unit": "箱", "tax_basis": "included"})
    fact = validate_fact(candidate(repo, "80", unit), definition, repo, "A")
    assert evaluate_checklist(definition, [fact], {"A": True}, snapshot_id=repo.snapshot_id)[0]["business_status"] == "待人工确认"


def test_money_basis_keeps_spaced_qualifications_outside_the_metric_fragment():
    repo = make_repo(offers=[("A", "空调单价含税80元/箱；不 含税", None)])
    definition = make_definition(item_name="空调", name="单价", rule={
        "type": "money", "operator": "lte", "value": "100", "currency": "CNY",
        "per_unit": "箱", "tax_basis": "included"})
    fact = validate_fact(candidate(repo, "80", "元/箱"), definition, repo, "A")
    assert fact["tax_basis"] == "unspecified"
    assert evaluate_checklist(definition, [fact], {"A": True}, snapshot_id=repo.snapshot_id)[0]["business_status"] == "待人工确认"


@pytest.mark.parametrize("name,model,text", [
    ("数量", "不提供", "空调数量不提供10台"),
    ("数量", "不到", "空调数量不到10台"),
    ("可达数量", None, "空调可达数量10台"),
])
def test_free_text_labels_cannot_whitelist_a_known_non_assertion(name, model, text):
    repo = make_repo(offers=[("A", text, model)])
    definition = make_definition(item_name="空调", name=name, rule={
        "type": "numeric", "operator": "eq", "value": "10", "unit": "台"})
    fact = validate_fact(candidate(repo, "10", "台"), definition, repo, "A")
    assert fact["ambiguous"]
    assert evaluate_checklist(definition, [fact], {"A": True}, snapshot_id=repo.snapshot_id)[0]["business_status"] == "待人工确认"


def test_excluded_tax_requirement_cannot_be_extracted_as_included_due_to_spaces():
    repo = make_repo("空调单价不 含税不超过100元/箱", offers=[("A", "空调单价含税80元/箱", None)])
    definition = make_definition(repo, item_name="空调", name="单价", document=True, rule={
        "type": "money", "operator": "lte", "value": "100", "currency": "CNY",
        "per_unit": "箱", "tax_basis": "included"})
    fact = validate_fact(candidate(repo, "80", "元/箱"), definition, repo, "A")
    assert evaluate_checklist(definition, [fact], {"A": True}, snapshot_id=repo.snapshot_id)[0]["business_status"] == "待人工确认"


@pytest.mark.parametrize("wording", ["不足10台", "不到10台", "不满10台", "不提供10台", "10台；不能保证"])
def test_confirmed_item_binding_does_not_make_uncertain_numeric_text_exact(wording):
    definition, repo, frozen = sample(specification="", quantity=f"数量{wording}")
    fact = validate_fact(bound_candidate(repo), definition, repo, "A",
        rule_version=BINDING_RULE_VERSION, item_bindings=frozen)
    checks = evaluate_checklist(definition, [fact], {"A": True}, snapshot_id=repo.snapshot_id,
        rule_version=BINDING_RULE_VERSION, item_coverage={"A": {"phone": True}}, item_bindings=frozen)
    assert checks[0]["business_status"] == "待人工确认"
    old = validate_fact(bound_candidate(repo), definition, repo, "A", rule_version="dynamic-rules-v6", item_bindings=frozen)
    assert revalidate_fact(old, definition, repo, "A", rule_version="dynamic-rules-v6", item_bindings=frozen) == old
    assert BINDING_RULE_VERSION != "dynamic-rules-v6"


def test_overlapping_untrusted_labels_cannot_exhaust_the_numeric_validator():
    # A subprocess timeout bounds a denial-of-service regression without a
    # fragile millisecond performance assertion or a test hanging indefinitely.
    script = '''
from backend.app.criteria.validation import strict_source_comparator
from backend.tests.test_dynamic_requirements import make_definition
d = make_definition(item_name="空调空调", name="数量", rule={
    "type":"numeric", "operator":"eq", "value":"10", "unit":"台"})
assert strict_source_comparator("空调" * 120 + "数量无法说明10台", d.criteria[0], d.items[0], "台", model="空调") == "unknown"
'''
    result = subprocess.run([sys.executable, "-c", script], env=sanitized_env(os.environ),
        capture_output=True, timeout=10, check=False)
    assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")
