"""Offline scripted Agent evaluation on the hash-bound synthetic M2 packs."""

import argparse
import json
import re
import time
from pathlib import Path

from langchain_core.messages import AIMessage

from backend.app.agent.graph import AgentSession
from backend.app.evidence.repository import load_case
from backend.app.field_contract import FieldKey

ROOT = Path(__file__).resolve().parents[1]
PACKS = (ROOT / "examples" / "dev-pack-001", ROOT / "examples" / "dev-pack-003")
LABELS = ROOT / "examples" / "dev-pack-003" / "labels.json"

SOURCE_CASES = (
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


def _call(name: str, args: dict, number: int) -> AIMessage:
    return AIMessage(content="", tool_calls=[{"id": f"script-{number}",
                                              "name": name, "args": args}],
                     usage_metadata={"input_tokens": 150, "output_tokens": 30,
                                     "total_tokens": 180})


class ScriptedProvider:
    """Deterministic development double; no network client or model is used."""

    model_id = "scripted-development"
    context_window = 100000

    def __init__(self, suppliers: tuple[str, ...]):
        self.suppliers = suppliers
        self.step = 0
        self.extract_calls = 0

    def decide(self, messages, tools, **kwargs):
        step = self.step
        self.step += 1
        if step == 0:
            return _call("list_case_sources", {}, step)
        if step <= len(self.suppliers) * 2:
            supplier = self.suppliers[(step - 1) // 2]
            if step % 2:
                return _call("extract_facts", {"supplier": supplier,
                                               "field_keys": list(FieldKey)}, step)
            latest = json.loads(messages[-1].content)["data"]
            return _call("evaluate_checks", {"supplier": supplier,
                "fact_set_ids": [item["fact_set_id"] for item in latest["fact_sets"]]}, step)
        handles = next(message for message in messages if message.type == "system"
                       and message.content.startswith("VERIFIED_CHECK_RESULTS="))
        available = json.loads(handles.content.split("=", 1)[1])
        return _call("submit_answer", {"kind": "check", "check_result_ids": [
            item["check_result_id"] for item in available], "evidence_ids": []}, step)

    def extract(self, messages, schema, **kwargs):
        self.extract_calls += 1
        batch = json.loads(messages[-1].content)["evidence"]
        if schema["function"]["name"] == "MemoryCandidates":
            facts = []
            for block in batch:
                text = block["text"]
                match = re.search(r"已装内存.*?(\d+)\s*GB", text)
                if match:
                    facts.append({"raw_value": match[1], "raw_unit": "GB",
                                  "qualifier": "installed",
                                  "comparator": "gte" if "至少" in text else "eq",
                                  "evidence_refs": [block["evidence_id"]]})
            return _call("MemoryCandidates", {"facts": facts}, self.extract_calls)
        facts = []
        for block in batch:
            for snippet, field, value, unit in SOURCE_CASES:
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
        return _call("FieldCandidates", {"facts": facts}, self.extract_calls)


def evaluate() -> dict:
    expected = json.loads(LABELS.read_text(encoding="utf-8"))["expected_status_by_supplier"]
    scenarios = []
    for pack in PACKS:
        repo = load_case(pack)
        suppliers = tuple(sorted({source["supplier"] for source in repo.sources()
                                  if source["supplier"]}))
        provider = ScriptedProvider(suppliers)
        session = AgentSession(repo, provider, mode="thorough")
        question = ("完整审查全部供应商的所有字段" if len(suppliers) > 1
                    else "完整审查供应商 A 的所有字段")
        started = time.monotonic()
        run = session.ask(question)
        elapsed_ms = round((time.monotonic() - started) * 1000, 2)
        checked = {(result.supplier, str(result.field_key)): result
                   for result in session.tools.results.values()}
        correct = sum(result.business_status == expected[supplier][field]
                      for (supplier, field), result in checked.items())
        expected_cells = len(suppliers) * len(FieldKey)
        reference_checks = [(result.supplier, ref) for result in checked.values()
                            for ref in result.evidence_refs]
        citation_errors = 0
        for supplier, ref in reference_checks:
            try:
                block = repo.read([ref])[0]
                citation_errors += block.supplier not in (None, supplier)
            except ValueError:
                citation_errors += 1
        scenarios.append({
            "case_id": repo.case_id, "supplier_count": len(suppliers),
            "run_status": run.status, "run_error": run.error,
            "agent_task_success": {"numerator": int(run.status == "completed"
                                                  and correct == expected_cells
                                                  and citation_errors == 0),
                                   "denominator": 1},
            "status_accuracy": {"numerator": correct, "denominator": expected_cells},
            "citation_errors": {"numerator": citation_errors,
                                "denominator": len(reference_checks)},
            "scripted_model_calls": run.usage["model_calls"],
            "scripted_usage": {"input_units": run.usage["actual_input"],
                               "output_units": run.usage["actual_output"]},
            "elapsed_ms": elapsed_ms,
        })
    return {"dataset_kind": "synthetic_development",
            "provider_kind": "scripted_no_api",
            "real_model_token_usage": None,
            "scenarios": scenarios,
            "agent_task_success": {"numerator": sum(
                item["agent_task_success"]["numerator"] for item in scenarios),
                "denominator": len(scenarios)},
            "status_accuracy": {"numerator": sum(
                item["status_accuracy"]["numerator"] for item in scenarios),
                "denominator": sum(item["status_accuracy"]["denominator"] for item in scenarios)}}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    payload = json.dumps(evaluate(), ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding="utf-8")
    else:
        print(payload, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
