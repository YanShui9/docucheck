"""The synthetic one-supplier pack exercises all eight result cells offline."""

import json
from pathlib import Path

from langchain_core.messages import AIMessage

from backend.app.evidence.repository import load_case
from backend.app.extraction.fields import extract_fields
from backend.app.field_contract import FieldKey
from backend.app.rules.fields import evaluate_field

PACK = Path(__file__).resolve().parents[2] / "examples" / "dev-pack-001"


class SevenFieldCandidates:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, operation, messages, schema):
        assert operation == "extract"
        self.calls += 1
        batch = json.loads(messages[-1].content)["evidence"]
        facts = []
        source_cases = (
            ("标称存储至少 512 GB", "storage", "512", "GB"),
            ("标称 SSD 1 TB", "storage", "1", "TB"),
            ("屏幕尺寸至少 14 英寸", "screen_size", "14", "英寸"),
            ("屏幕尺寸 14 英寸", "screen_size", "14", "英寸"),
            ("主机重量不超过 1.6 kg", "weight", "1.6", "kg"),
            ("主机重量 1.8 kg", "weight", "1.8", "kg"),
            ("质保期限至少 24 个月", "warranty", "24", "个月"),
            ("采购数量 10 台", "quantity", "10", "台"),
            ("含税单价不超过 6000 元/台", "unit_price", "6000", "元/台"),
            ("订单确认后 14 个自然日内交付", "delivery_days", "14", "自然日"),
        )
        for block in batch:
            for snippet, field, value, unit in source_cases:
                if snippet in block["text"]:
                    facts.append({"field_key": field, "raw_value": value,
                                  "raw_unit": unit,
                                  "evidence_refs": [block["evidence_id"]]})
        by_cell = {item["cell"]: item for item in batch if item["sheet"] == "报价"}
        for label, value_cell, field, value, unit in (
            ("A7", "B7", "quantity", "10", "台"),
            ("A8", "B8", "unit_price", "5800", "元/台"),
            ("A9", "B9", "delivery_days", "10", "工作日"),
        ):
            if label in by_cell and value_cell in by_cell:
                facts.append({"field_key": field, "raw_value": value,
                              "raw_unit": unit,
                              "evidence_refs": [by_cell[label]["evidence_id"],
                                                by_cell[value_cell]["evidence_id"]]})
        return AIMessage(content="", tool_calls=[{"id": f"fields-{self.calls}",
                                                  "name": "FieldCandidates",
                                                  "args": {"facts": facts}}])


def test_one_supplier_seven_new_fields_match_synthetic_gold() -> None:
    repo = load_case(PACK)
    model = SevenFieldCandidates()
    fields = [field for field in FieldKey if field != FieldKey.MEMORY]

    extracted = extract_fields(repo, "vendor-a", fields, model)
    results = {field: evaluate_field(repo, "vendor-a", field, extracted[field].facts,
                                     scan_complete=extracted[field].complete)
               for field in fields}

    assert model.calls >= 2
    assert {field.value: result.business_status for field, result in results.items()} == {
        "storage": "满足", "screen_size": "满足", "weight": "不满足",
        "warranty": "证据不足", "quantity": "满足", "unit_price": "满足",
        "delivery_days": "待人工确认",
    }
    assert all(result.processing_status == "complete" for result in results.values())


def test_self_describing_price_cell_does_not_require_redundant_label_reference() -> None:
    repo = load_case(PACK)
    price = next(block for block in repo.blocks if block.role == "quote" and block.cell == "B8")

    class PriceCellOnly(SevenFieldCandidates):
        def __call__(self, operation, messages, schema):
            reply = super().__call__(operation, messages, schema)
            for fact in reply.tool_calls[0]["args"]["facts"]:
                if fact["field_key"] == "unit_price" and len(fact["evidence_refs"]) == 2:
                    fact["evidence_refs"] = [price.evidence_id]
            return reply

    model = PriceCellOnly()
    extracted = extract_fields(repo, "vendor-a", list(FieldKey)[1:], model)

    assert extracted[FieldKey.UNIT_PRICE].complete
    assert model.calls == 2


def test_focused_split_cell_retry_recovers_missing_delivery_fact() -> None:
    repo = load_case(PACK)
    label = next(block for block in repo.blocks if block.role == "quote" and block.cell == "A9")
    value = next(block for block in repo.blocks if block.role == "quote" and block.cell == "B9")

    class LateDelivery(SevenFieldCandidates):
        def __init__(self):
            super().__init__()
            self.focus_calls = 0

        def __call__(self, operation, messages, schema):
            batch = json.loads(messages[-1].content)["evidence"]
            if {item["evidence_id"] for item in batch} == {label.evidence_id, value.evidence_id}:
                self.focus_calls += 1
                return AIMessage(content="", tool_calls=[{"id": "focused-delivery",
                    "name": "FieldCandidates", "args": {"facts": [{
                        "field_key": "delivery_days", "raw_value": "10", "raw_unit": "工作日",
                        "evidence_refs": [label.evidence_id, value.evidence_id],
                    }]}}])
            reply = super().__call__(operation, messages, schema)
            reply.tool_calls[0]["args"]["facts"] = [
                fact for fact in reply.tool_calls[0]["args"]["facts"]
                if set(fact["evidence_refs"]) != {label.evidence_id, value.evidence_id}
            ]
            return reply

    model = LateDelivery()
    extracted = extract_fields(repo, "vendor-a", list(FieldKey)[1:], model)
    result = evaluate_field(repo, "vendor-a", FieldKey.DELIVERY_DAYS,
                            extracted[FieldKey.DELIVERY_DAYS].facts,
                            scan_complete=extracted[FieldKey.DELIVERY_DAYS].complete)

    assert model.focus_calls == 1
    assert extracted[FieldKey.DELIVERY_DAYS].complete
    assert result.business_status == "待人工确认"
    assert {label.evidence_id, value.evidence_id} <= set(result.evidence_refs)


def test_empty_focused_retry_keeps_delivery_incomplete() -> None:
    repo = load_case(PACK)
    pair = {block.evidence_id for block in repo.blocks
            if block.role == "quote" and block.cell in {"A9", "B9"}}

    class EmptyDelivery(SevenFieldCandidates):
        def __init__(self):
            super().__init__()
            self.focus_calls = 0

        def __call__(self, operation, messages, schema):
            batch = json.loads(messages[-1].content)["evidence"]
            if {item["evidence_id"] for item in batch} == pair:
                self.focus_calls += 1
                return AIMessage(content="", tool_calls=[{"id": "focused-empty",
                    "name": "FieldCandidates", "args": {"facts": []}}])
            reply = super().__call__(operation, messages, schema)
            reply.tool_calls[0]["args"]["facts"] = [
                fact for fact in reply.tool_calls[0]["args"]["facts"]
                if set(fact["evidence_refs"]) != pair
            ]
            return reply

    model = EmptyDelivery()
    extracted = extract_fields(repo, "vendor-a", list(FieldKey)[1:], model)

    assert model.focus_calls == 1
    assert not extracted[FieldKey.DELIVERY_DAYS].complete


def test_focused_retry_recovers_omitted_requirements_and_invalid_quote_candidates() -> None:
    repo = load_case(PACK)
    quantity_requirement = next(block for block in repo.blocks
                                if block.role == "requirement" and "采购数量 10 台" in block.text)
    delivery_requirement = next(block for block in repo.blocks
                                if block.role == "requirement" and "14 个自然日" in block.text)
    quantity_label = next(block for block in repo.blocks
                          if block.role == "quote" and block.cell == "A7")
    quantity_value = next(block for block in repo.blocks
                          if block.role == "quote" and block.cell == "B7")
    delivery_label = next(block for block in repo.blocks
                          if block.role == "quote" and block.cell == "A9")
    delivery_value = next(block for block in repo.blocks
                          if block.role == "quote" and block.cell == "B9")

    class IncompleteFirstPass(SevenFieldCandidates):
        def __init__(self) -> None:
            super().__init__()
            self.focus_calls = 0

        def __call__(self, operation, messages, schema):
            payload = json.loads(messages[-1].content)
            if "targets" in payload:
                self.focus_calls += 1
                expected = {
                    ("quantity", (quantity_requirement.evidence_id,)):
                        ("10", "台"),
                    ("quantity", (quantity_label.evidence_id, quantity_value.evidence_id)):
                        ("10", "台"),
                    ("delivery_days", (delivery_requirement.evidence_id,)):
                        ("14", "个自然日"),
                    ("delivery_days", (delivery_label.evidence_id, delivery_value.evidence_id)):
                        ("10", "个工作日"),
                }
                facts = [{"field_key": field, "raw_value": expected[(field, tuple(refs))][0],
                          "raw_unit": expected[(field, tuple(refs))][1],
                          "evidence_refs": refs}
                         for target in payload["targets"]
                         for field, refs in [(target["field_key"], target["evidence_refs"])]
                         if (field, tuple(refs)) in expected]
                return AIMessage(content="", tool_calls=[{"id": "focused-recovery",
                    "name": "FieldCandidates", "args": {"facts": facts}}])
            reply = super().__call__(operation, messages, schema)
            facts = reply.tool_calls[0]["args"]["facts"]
            facts[:] = [fact for fact in facts if fact["evidence_refs"] not in (
                [quantity_requirement.evidence_id], [delivery_requirement.evidence_id])]
            for fact in facts:
                if fact["field_key"] == "quantity" and set(fact["evidence_refs"]) == {
                    quantity_label.evidence_id, quantity_value.evidence_id,
                }:
                    fact["raw_unit"] = "台数"
                if fact["field_key"] == "delivery_days" and set(fact["evidence_refs"]) == {
                    delivery_label.evidence_id, delivery_value.evidence_id,
                }:
                    fact["raw_value"] = "11"
                    facts.append({"field_key": "delivery_days", "raw_value": "11",
                                  "raw_unit": "工作日",
                                  "evidence_refs": [delivery_value.evidence_id]})
            return reply

    model = IncompleteFirstPass()
    extracted = extract_fields(repo, "vendor-a", list(FieldKey)[1:], model)
    quantity = extracted[FieldKey.QUANTITY]
    delivery = extracted[FieldKey.DELIVERY_DAYS]

    assert model.focus_calls == 1
    assert quantity.complete
    assert delivery.complete
    assert evaluate_field(repo, "vendor-a", FieldKey.QUANTITY, quantity.facts,
                          scan_complete=quantity.complete).business_status == "满足"
    assert evaluate_field(repo, "vendor-a", FieldKey.DELIVERY_DAYS, delivery.facts,
                          scan_complete=delivery.complete).business_status == "待人工确认"
