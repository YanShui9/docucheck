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
