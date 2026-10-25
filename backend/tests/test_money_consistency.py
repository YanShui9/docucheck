"""Repeated prices are equivalent only with independently explicit matching bases."""

from copy import deepcopy

import pytest
from openpyxl.utils.cell import coordinate_to_tuple

from backend.app.agent.dynamic import DynamicAgent, review_plan
from backend.app.criteria.facts import (
    BINDING_RULE_VERSION,
    fact_source_refs,
    revalidate_fact,
)
from backend.app.criteria.item_bindings import validate_bindings
from backend.app.criteria.models import ChecklistDefinition
from backend.app.evidence.models import Document
from backend.app.evidence.repository import EvidenceRepository, digest
from backend.app.parsing import EvidenceBlock, ParseResult
from backend.tests.test_bound_enum_headers import checks, verify
from backend.tests.test_material_agent import MaterialProvider
from backend.tests.test_money_sources import independent, sample


def prices(*, value="5800", other="含税单价 5800 元/台", main_pdf=False, other_pdf=True,
           classification="category", supplier="A", other_item="sample", main_sku=None,
           other_sku=None, extra=(), main_parts=None, criteria=()):
    definition = ChecklistDefinition.model_validate({
        "items": [{"item_id": "sample", "name": "样品"}, {"item_id": "other", "name": "其他商品"}],
        "criteria": [{"criterion_id": "metric", "item_id": "sample", "name": "含税单价",
                      "rule": {"type": "money", "operator": "lte", "value": "6000",
                               "currency": "CNY", "per_unit": "台", "tax_basis": "included"},
                      "source": {"kind": "user_defined", "reason": "买方确认"}}, *criteria]})
    documents = []
    for doc_id, pdf, owner, item_id, sku, parts in [
            ("main", main_pdf, "A", "sample", main_sku,
             main_parts or ([f"含税单价 {value} 元/台"] if main_pdf else
                            ["含税单价", f"含税单价 {value} 元/台"])),
            ("second", other_pdf, supplier, other_item, other_sku, [other])]:
        anchor_text = "样品" if item_id == "sample" else "其他商品"
        if sku:
            anchor_text += f"；型号：{sku}"
        texts = [("A1", anchor_text), *((f"{chr(65 + i)}6", text) for i, text in enumerate(parts))]
        if doc_id == "second":
            texts.extend((f"A{8 + i}", text) for i, text in enumerate(extra))
        blocks = tuple(EvidenceBlock(str(i), doc_id, text,
                                     **({"page_idx": 0} if pdf else {"sheet": "报价", "cell": cell}))
                       for i, (cell, text) in enumerate(texts))
        documents.append(Document(doc_id, ("a" if doc_id == "main" else "b") * 64,
                                  "quote" if doc_id == "main" else "spec", owner, sku,
                                  ParseResult(doc_id, "parser", "complete", blocks,
                                              **({"total_pages": 1, "processed_pages": (0,)} if pdf else
                                                 {"total_sheets": 1, "processed_sheets": ("报价",)}))))
    repo = EvidenceRepository("case", 1, documents)
    requests = []
    for doc, pdf, item_id, sku in [(documents[0], main_pdf, "sample", main_sku),
                                   (documents[1], other_pdf, other_item, other_sku)]:
        anchor = next(b for b in repo.blocks if b.document_id == doc.document_id)
        context = {"evidence_refs": [anchor.evidence_id], "quote": anchor.text}
        positions = [coordinate_to_tuple(b.cell) for b in repo.blocks if b.document_id == doc.document_id and b.cell]
        requests.append({"document_id": doc.document_id, "source_sha256": doc.source_hash,
                         "parser_version": "parser", "item_id": item_id,
                         "anchor": context, "identifier_class": "sku" if sku else
                         classification if doc.document_id == "second" else "category",
                         **({"supplier_sku": sku, "sku_anchor": context} if sku else {}),
                         "selector": {"kind": "pdf", "start_page": 1, "end_page": 1} if pdf else
                         {"kind": "xlsx", "sheet": "报价", "start_row": 6,
                          "end_row": max(p[0] for p in positions),
                          "start_column": 1, "end_column": max(p[1] for p in positions)}})
    entries = validate_bindings(requests, definition, repo).payload()
    payload = {"schema_version": 1, "case_id": "case", "snapshot_id": repo.snapshot_id,
               "checklist_version_id": "confirmed", "checklist_digest": digest(definition.model_dump(mode="json")),
               "entries": entries}
    built = definition, repo, {"version_id": "binding-a", "digest": digest(payload), "payload": payload}
    blocks = [b for b in repo.blocks if b.document_id == "main"][1:]
    body = {"criterion_id": "metric", "item_id": "sample", "supplier": "A", "value": value,
            "unit": "元/台", "evidence_refs": [b.evidence_id for b in blocks],
            "quote": " ".join(b.text for b in blocks)}
    return built, body


@pytest.mark.parametrize("main_pdf,other_pdf", [(False, True), (True, False), (True, True), (False, False)])
@pytest.mark.parametrize("value,status", [("5800", "满足"), ("6100", "不满足")])
def test_complete_equivalent_prices_apply_threshold_and_keep_all_original_sources(main_pdf, other_pdf, value, status):
    built, body = prices(value=value, other=f"含税单价 {value} 元/台", main_pdf=main_pdf, other_pdf=other_pdf)
    fact = verify(built, body)
    result = checks(built, [fact])[0]
    assert fact["price_scope_state"] == fact["tax_scope_state"] == "resolved"
    assert result["business_status"] == status
    assert fact["price_sources"] and fact["tax_sources"]
    assert fact["tax_sources"][0]["scope_state"] == "resolved"
    assert {b.evidence_id for b in built[1].blocks if "单价" in b.text} <= set(fact_source_refs(fact))
    assert body["quote"] == fact["quote"] and body["evidence_refs"] == fact["evidence_refs"]
    assert revalidate_fact(fact, built[0], built[1], "A", rule_version=BINDING_RULE_VERSION, item_bindings=built[2]) == fact


@pytest.mark.parametrize("other", ["含税单价5800.00元/台", "含税单价5800 CNY/台", "含税单价5800人民币/台"])
def test_decimal_and_currency_spellings_can_be_equivalent(other):
    built, body = prices(other=other)
    assert checks(built, [verify(built, body)])[0]["business_status"] == "满足"


@pytest.mark.parametrize("other", [
    "含税单价5900元/台", "含税单价5800美元/台", "含税单价5800元/箱", "不含税单价5800元/台",
    "单价5800元/台", "含税单价5800", "含税单价5800/台", "含税单价约5800元/台",
    "含税单价不超过5800元/台", "含税单价5800元/台（订购10台时）", "参考价5800元/台",
    "总价5800元/台", "含税单价5800元/台；另收运费", "含税单价5800元/台 或5800元/箱",
    "含税单价5800元/台；税费另计", "含税单价5800元/台；不含税", "含税单价5800元/台。备注：待确认",
])
def test_equal_amount_alone_cannot_prove_equivalence(other):
    built, body = prices(other=other)
    fact = verify(built, body)
    result = checks(built, [fact])[0]
    assert (result["business_status"], result["reason_code"]) == ("待人工确认", "money_price_scope_unclear")
    assert fact["price_sources"]


@pytest.mark.parametrize("other", ["含税单价58 00元/台", "含税单价5800美 元/台", "含税单价5800欧 元/台"])
def test_price_tokens_cannot_be_joined_across_whitespace(other):
    built, body = prices(other=other)
    assert checks(built, [verify(built, body)])[0]["business_status"] == "待人工确认"


@pytest.mark.parametrize("extra", [
    "备注：本表仅在订单数量达到100台时生效", "单价：待确认", "报价未定", "单价另议",
    "备注：本报价为参考", "单 价6500元/台", "结算按另行通知执行", "备注：另附说明",
])
def test_unknown_scoped_notes_cannot_be_ignored(extra):
    built, body = prices(extra=[extra])
    assert checks(built, [verify(built, body)])[0]["business_status"] == "待人工确认"


@pytest.mark.parametrize("name,rule,text", [
    ("数量", {"type": "numeric", "operator": "eq", "value": "10", "unit": "台"}, "数量 10 台"),
    ("交付期限", {"type": "duration", "operator": "lte", "value": "14", "calendar": "natural_days",
                  "start_event": "订单确认"}, "交付期限 订单确认后18个自然日内交付"),
    ("商品状态", {"type": "enum", "operator": "eq", "values": ["全新未激活"]}, "商品状态 全新未激活"),
    ("质保承诺书", {"type": "presence", "description": "本采购品目对应的质保承诺书"},
     "质保承诺书 已附本采购品目对应的质保承诺书"),
])
def test_complete_unrelated_metric_allows_price_but_appended_note_does_not(name, rule, text):
    criterion = {"criterion_id": "other_metric", "item_id": "sample", "name": name, "rule": rule,
                 "source": {"kind": "user_defined", "reason": "买方确认"}}
    for suffix, expected in [("", "满足"), ("；本表满100台才生效", "待人工确认")]:
        built, body = prices(extra=[text + suffix], criteria=[criterion])
        price = next(c for c in checks(built, [verify(built, body)]) if c["criterion_id"] == "metric")
        assert price["business_status"] == expected


@pytest.mark.parametrize("extra", ["报价日期：2026-10-25", "报价编号：Q20261025"])
def test_complete_quote_metadata_is_safe_but_its_qualification_is_not(extra):
    for suffix, expected in [("", "满足"), ("；满100台才生效", "待人工确认")]:
        built, body = prices(extra=[extra + suffix])
        assert checks(built, [verify(built, body)])[0]["business_status"] == expected


@pytest.mark.parametrize("name,rule,text", [
    ("适用条件", {"type": "enum", "operator": "eq", "values": ["仅在订购100台时生效"]},
     "适用条件：仅在订购100台时生效"),
    ("商品状态", {"type": "enum", "operator": "eq", "values": ["仅供参考，以最终确认为准"]},
     "商品状态：仅供参考，以最终确认为准"),
    ("优惠门槛", {"type": "numeric", "operator": "eq", "value": "100", "unit": "台"}, "优惠门槛100台"),
    ("成本", {"type": "numeric", "operator": "eq", "value": "6500", "unit": "元/台"}, "成本6500元/台"),
    ("数量", {"type": "numeric", "operator": "eq", "value": "100", "unit": "台"}, "100台"),
    ("质保承诺书", {"type": "presence", "description": "满100台才生效的报价条件"},
     "质保承诺书 已附满100台才生效的报价条件"),
])
def test_buyer_criterion_cannot_authorize_ignoring_price_qualifications(name, rule, text):
    criterion = {"criterion_id": "other_metric", "item_id": "sample", "name": name, "rule": rule,
                 "source": {"kind": "user_defined", "reason": "买方确认"}}
    built, body = prices(extra=[text], criteria=[criterion])
    price = next(c for c in checks(built, [verify(built, body)]) if c["criterion_id"] == "metric")
    assert price["business_status"] == "待人工确认"


@pytest.mark.parametrize("options", [
    {"classification": "unknown"}, {"main_sku": "SKU-A", "other_sku": "SKU-B"},
    {"main_sku": "SKU-A"}, {"other_sku": "SKU-A"},
    {"extra": ["型号：SKU-B"]},
])
def test_unproven_identity_or_mixed_range_remains_manual(options):
    built, body = prices(**options)
    assert checks(built, [verify(built, body)])[0]["business_status"] == "待人工确认"


def test_same_explicit_sku_can_share_complete_price():
    built, body = prices(main_sku="SKU-A", other_sku="SKU-A")
    assert checks(built, [verify(built, body)])[0]["business_status"] == "满足"


@pytest.mark.parametrize("options", [{"supplier": "B"}, {"other_item": "other"}])
def test_other_supplier_or_item_stays_outside_scope(options):
    built, body = prices(other="不含税单价9999元/箱", **options)
    fact = verify(built, body)
    assert not fact["price_sources"] and not fact["tax_sources"]
    assert checks(built, [fact])[0]["business_status"] == "满足"


def test_equivalent_prices_do_not_authorize_cross_file_standalone_tax_note():
    built, body = prices(extra=["税费口径：含税"])
    fact = verify(built, body)
    assert fact["price_scope_state"] == "resolved"
    assert fact["tax_scope_state"] == "unclear"
    assert checks(built, [fact])[0]["reason_code"] == "money_tax_scope_unclear"


@pytest.mark.parametrize("field", ["quote", "evidence_refs", "item_binding_entry_digest"])
def test_equivalent_price_provenance_cannot_be_tampered(field):
    built, body = prices()
    fact = deepcopy(verify(built, body))
    fact["price_sources"][0][field] = [] if field == "evidence_refs" else "forged"
    with pytest.raises(ValueError, match="fact_integrity"):
        revalidate_fact(fact, built[0], built[1], "A", rule_version=BINDING_RULE_VERSION, item_bindings=built[2])


def test_formal_processing_cache_and_history_rebuild_equivalent_price_sources():
    built, body = prices()
    definition, repo, bindings = built
    cache = {}
    result = DynamicAgent(repo, MaterialProvider([body]), definition, item_bindings=bindings,
                          cache_save=lambda key, fingerprint, raw: cache.update({(key, fingerprint): deepcopy(raw)})).ask("正式审查")
    assert result["status"] == "completed", result
    assert result["payload"]["checks"][0]["business_status"] == "满足"
    assert result["payload"]["checks"][0]["is_formal"]
    provider = MaterialProvider([], failure=AssertionError("must reuse cached candidates"))
    cached = DynamicAgent(repo, provider, definition, item_bindings=bindings,
                          cache_load=lambda key, fingerprint: cache.get((key, fingerprint))).ask("正式审查")
    assert cached["status"] == "completed" and not provider.requests
    assert cached["payload"]["facts"] == result["payload"]["facts"]
    history = DynamicAgent(repo, MaterialProvider([], decision="get_checklist"), definition,
                           item_bindings=bindings, previous_payload=result["payload"]).ask("查看上次报告")
    assert history["payload"]["facts"] == result["payload"]["facts"]


@pytest.mark.parametrize("name,identities", [
    ("cross", ("4b21cfaf52513dbb5cd1e0c54f025113810a328606ec6038c2e21829bf6edd7b",
               "8c03aa9bb33ac9bdfcbccd0ddd73090f8464b88094589b85935c48da679e6efd",
               "0894221d7f2899872af7973a9daee5db099bc98814dc8e45c990dc55666504d0",
               "ed58db08a10bf2b80937aff0bc997f918da704a90e8e271bd51e068385528510")),
    ("metadata", ("789fd6b803a18b24a18efcbf89d23e27da204c9d61ee46bec4d87198dcd86936",
                  "83f83404cfbe083ae138f41cceb45c519a44798ec4ffb464676bae238a95aa5e",
                  "c297e470fdb0ae3f87243e249ec2a5f34ba4db62096f9d2c7b8c9d972e592700",
                  "00ec86b7d0245bfb63bc63e6b2600b185221ce0d260865b5eb358e07e98bab02")),
])
def test_v14_fact_check_batch_and_input_identities_remain_frozen(name, identities):
    # Captured from 15fca94 before any v15 product edits, not from the new rule.
    built, body = (independent(second_text="不含税单价25元/箱") if name == "cross" else
                   sample(tax="含税", extra=[("A10", "报价日期"), ("B10", "2026-10-19 00:00:00")]))
    version = "dynamic-rules-v14"
    fact = verify(built, body, version)
    check = checks(built, [fact], version)[0]
    manifest = review_plan(built[1], built[0], rule_version=version, item_bindings=built[2])[0]["A"][0][2]
    assert (fact["fact_id"], check["check_result_id"], manifest["batch_id"], manifest["input_digest"]) == identities
    assert revalidate_fact(fact, built[0], built[1], "A", rule_version=version, item_bindings=built[2]) == fact
