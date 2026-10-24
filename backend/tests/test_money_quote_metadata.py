"""Quote metadata must not hide a price's independently proven tax conflict."""

import pytest

from backend.app.agent.dynamic import review_plan
from backend.app.criteria.facts import BINDING_RULE_VERSION, revalidate_fact
from backend.tests.test_money_sources import V13, checks, sample, verify


@pytest.mark.parametrize("label,value", [
    ("报价日期", "2026-10-19 00:00:00"),
    ("报价时间", "2026-10-19 12:30:00"),
    ("报价有效期", "30天"),
    ("报价编号", "Q20261019"),
])
def test_current_version_excludes_explicit_quote_metadata_from_other_prices(label, value):
    built, body = sample(tax="含税", extra=[("A10", label), ("B10", value)])
    fact = verify(built, body, BINDING_RULE_VERSION)
    result = checks(built, [fact], BINDING_RULE_VERSION)[0]
    assert fact["price_sources"] == []
    assert fact["price_scope_state"] == "resolved"
    assert fact["tax_conflict"]
    assert (result["business_status"], result["reason_code"]) == ("资料冲突", "money_tax_conflict")
    assert not {b.evidence_id for b in built[1].blocks if b.cell in {"A10", "B10"}} & set(result["evidence_refs"])


@pytest.mark.parametrize("label", ["报价", "报价单价", "报价日期说明；另一单价"])
def test_current_version_still_keeps_actual_other_prices(label):
    built, body = sample(tax="含税", extra=[("A10", label), ("B10", "30元/箱")])
    fact = verify(built, body, BINDING_RULE_VERSION)
    assert fact["price_sources"]
    assert checks(built, [fact], BINDING_RULE_VERSION)[0]["reason_code"] == "money_price_scope_unclear"


@pytest.mark.parametrize("extra", [
    [("A10", "报价有效期内：30元/箱")],
    [("A10", "报价日期"), ("B10", "2026-10-19"), ("C10", "30元/箱")],
    [("A10", "报价日期"), ("B10", "2026-10-19；30元/箱")],
    [("A10", "报价编号"), ("B10", "Q20261019"), ("C10", "30"), ("D10", "CNY")],
    [("A10", "报价编号"), ("B10", "Q20261019"), ("C10", "30CNY")],
    [("A10", "报价编号 Q20261019 30 CNY")],
    [("A10", "报价编号"), ("B10", "Q20261019 30CNY")],
])
def test_metadata_label_cannot_hide_another_explicit_amount_without_a_second_price_label(extra):
    built, body = sample(extra=extra)
    fact = verify(built, body, BINDING_RULE_VERSION)
    result = checks(built, [fact], BINDING_RULE_VERSION)[0]
    assert fact["price_sources"]
    assert (result["business_status"], result["reason_code"]) == ("待人工确认", "money_price_scope_unclear")


def test_v13_quote_date_report_keeps_original_fact_check_and_batch_identities():
    built, body = sample(tax="含税", extra=[("A10", "报价日期"), ("B10", "2026-10-19 00:00:00")])
    fact = verify(built, body, V13)
    result = checks(built, [fact], V13)[0]
    manifest = review_plan(built[1], built[0], rule_version=V13, item_bindings=built[2])[0]["A"][0][2]
    assert fact["fact_id"] == "f0b370e8b21fc470dfc4d56c1bb57a396029822f537c0d1a3949c6818c8074f8"
    assert result["check_result_id"] == "fbaf142d1fd69cf6905d85af1ee1af94a9596fdd196f665581cedd771ef68f6d"
    assert manifest["batch_id"] == "f606b38bdadd8d6ed792ea4468f3051716be53f5078d6623364d321610e3186c"
    assert manifest["input_digest"] == "df0f8ca61159c8563426506b00bf21c076645f66f7854d546ed7aee3792c167b"
    assert fact["price_sources"][0]["quote"] == "报价日期 2026-10-19 00:00:00"
    assert result["reason_code"] == "money_price_scope_unclear"
    assert revalidate_fact(fact, built[0], built[1], "A", rule_version=V13, item_bindings=built[2]) == fact
