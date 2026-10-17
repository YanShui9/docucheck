"""Reconstructed apple anchor contamination, not a replay of lost model output."""

import json
from copy import deepcopy

import pytest
from langchain_core.messages import HumanMessage

from backend.app.agent.diagnostics import safe_trace_events
from backend.app.agent.dynamic import DynamicAgent
from backend.app.agent.policy import Budget
from backend.app.criteria.item_bindings import validate_bindings
from backend.app.criteria.models import ChecklistDefinition
from backend.app.evidence.models import Document
from backend.app.evidence.repository import EvidenceRepository, digest
from backend.app.parsing import EvidenceBlock, ParseResult
from backend.app.providers.base import ProviderError
from backend.tests.test_dynamic_agent import call


def mixed_sample(*, anchor="苹果"):
    definition = ChecklistDefinition.model_validate({
        "items": [{"item_id": "apple", "name": "苹果"}, {"item_id": "tshirt", "name": "T恤"}],
        "criteria": [{"criterion_id": f"{item}-{metric}", "item_id": item,
                      "name": name + label, "source": {"kind": "user_defined", "reason": "买方确认"},
                      "rule": rule}
                     for item, name, unit in [("apple", "苹果", "箱"), ("tshirt", "T恤", "件")]
                     for metric, label, rule in [
                         ("qty", "采购数量", {"type": "numeric", "operator": "eq",
                                           "value": "10" if item == "apple" else "20", "unit": unit}),
                         ("price", "含税单价", {"type": "money", "operator": "lte",
                                             "value": "100" if item == "apple" else "25",
                                             "currency": "CNY", "per_unit": unit, "tax_basis": "included"})]],
    })
    cells = [("B2", anchor), ("A3", "采购数量"), ("B3", "10 箱"),
             ("A4", "含税单价"), ("B4", "80 元/箱"), ("B7", "T恤"),
             ("A8", "采购数量"), ("B8", "50 件"), ("A9", "含税单价"), ("B9", "30 元/件")]
    blocks = tuple(EvidenceBlock(cell, "quote", text, sheet="报价", cell=cell) for cell, text in cells)
    parsed = ParseResult("quote", "parser", "complete", blocks,
                         total_sheets=1, processed_sheets=("报价",))
    repo = EvidenceRepository("case", 1, [Document("quote", "a" * 64, "quote", "A", None, parsed)])
    by_cell = {b.cell: b for b in repo.blocks}
    requests = [{"document_id": "quote", "source_sha256": "a" * 64, "parser_version": "parser",
                 "item_id": item, "selector": {"kind": "xlsx", "sheet": "报价", "start_row": lo,
                 "end_row": hi, "start_column": 1, "end_column": 2},
                 "anchor": {"evidence_refs": [by_cell[cell].evidence_id], "quote": text},
                 "identifier_class": "category"}
                for item, lo, hi, cell, text in [("apple", 2, 4, "B2", anchor), ("tshirt", 7, 9, "B7", "T恤")]]
    payload = {"schema_version": 1, "case_id": repo.case_id, "snapshot_id": repo.snapshot_id,
               "checklist_version_id": "confirmed", "checklist_digest": digest(definition.model_dump(mode="json")),
               "entries": validate_bindings(requests, definition, repo).payload()}
    return definition, repo, {"version_id": "binding", "digest": digest(payload), "payload": payload}


class AnchorProvider:
    context_window = 32768

    def __init__(self, *, second="valid", bad="anchor"):
        self.requests = []
        self.second, self.bad = second, bad

    def decide(self, messages, tools, **options):
        return call("review_checklist", {})

    def extract(self, messages, schema, **options):
        self.requests.append(deepcopy(messages))
        data = json.loads(messages[-1].content)
        item = data["items"][0]["item_id"]
        cells = {b["cell"]: b for b in data["evidence"]}
        repair = any(isinstance(m, HumanMessage) and '"rejected_fact_candidates"' in m.content
                     for m in messages[:-1])
        facts = []
        for metric, row, value, unit in [("qty", 3 if item == "apple" else 8,
                                         "10" if item == "apple" else "50", "箱" if item == "apple" else "件"),
                                        ("price", 4 if item == "apple" else 9,
                                         "80" if item == "apple" else "30", "元/箱" if item == "apple" else "元/件")]:
            selected = [cells[f"A{row}"], cells[f"B{row}"]]
            if item == "apple" and (not repair or self.second == "same_error"):
                selected = [cells["B2"], *selected]
                if self.bad == "business_row":
                    selected.append(cells["B4" if metric == "qty" else "B3"])
            if item == "apple" and repair and self.second == "drop_price" and metric == "price":
                continue
            fact = {"criterion_id": f"{item}-{metric}", "item_id": item, "supplier": data["supplier"], "value": value, "unit": unit,
                    "evidence_refs": [b["evidence_id"] for b in selected],
                    "quote": " ".join(b["text"] for b in selected)}
            if item == "apple" and repair and self.second == "swap_source" and metric == "qty":
                fact.update(evidence_refs=[cells["A4"]["evidence_id"], cells["B4"]["evidence_id"]],
                            quote="含税单价 80 元/箱", value="80")
            facts.append(fact)
        return call(schema["function"]["name"], {"facts": facts})


def test_reconstructed_apple_anchor_failure_is_repaired_once_then_both_batches_are_formal():
    definition, repo, frozen = mixed_sample()
    provider = AnchorProvider()
    cache = {}
    result = DynamicAgent(repo, provider, definition, item_bindings=frozen,
                          cache_save=lambda key, fingerprint, raw: cache.update({(key, fingerprint): raw})).ask("完整审查")
    assert result["status"] == "completed", result["error"]
    assert len(provider.requests) == 3  # apple, one semantic correction, tshirt
    payload = result["payload"]
    assert all(b["complete"] for b in payload["batch_coverage"])
    assert payload["batch_coverage"][0]["initial_rejection_codes"] == {"evidence_relationship": 2}
    assert [c["business_status"] for c in payload["checks"]] == ["满足", "满足", "不满足", "不满足"]
    assert all(c["is_formal"] for c in payload["checks"])
    assert len(cache) == 2
    assert all("B2" not in [b.cell for b in repo.read(f["evidence_refs"])] for f in payload["facts"])
    trace = safe_trace_events(result["events"])
    assert any(e["summary"].get("semantic_repair") is True for e in trace)
    # Cache is only verified facts, and revalidation produces the same four checks.
    cached_provider = AnchorProvider()
    cached = DynamicAgent(repo, cached_provider, definition, item_bindings=frozen,
                          cache_load=lambda key, fingerprint: cache.get((key, fingerprint))).ask("完整审查")
    assert cached_provider.requests == []
    assert cached["payload"]["checks"] == payload["checks"]


@pytest.mark.parametrize("second", ["same_error", "drop_price", "swap_source"])
def test_anchor_repair_cannot_erase_or_replace_fact_sources_or_loop(second):
    definition, repo, frozen = mixed_sample()
    provider = AnchorProvider(second=second)
    result = DynamicAgent(repo, provider, definition, item_bindings=frozen).ask("完整审查")
    assert result["status"] == "partial"
    assert len(provider.requests) == 3
    assert result["payload"]["coverage"] == {"A": False}
    assert not any(c["is_formal"] for c in result["payload"]["checks"] if c["item_id"] == "apple")
    assert result["payload"]["batch_coverage"][1]["complete"] is True


@pytest.mark.parametrize("anchor,bad", [("苹果", "business_row"), ("苹果，参考数量20箱", "anchor")])
def test_cross_business_rows_and_nonpure_anchors_are_not_semantically_repaired(anchor, bad):
    definition, repo, frozen = mixed_sample(anchor=anchor)
    provider = AnchorProvider(bad=bad)
    result = DynamicAgent(repo, provider, definition, item_bindings=frozen).ask("完整审查")
    assert result["status"] == "partial"
    assert len(provider.requests) == 2
    assert result["payload"]["batch_coverage"][0]["rejection_codes"] == {"evidence_relationship": 2}


def test_safe_dynamic_trace_retains_finite_failure_counts_without_candidate_data():
    raw = [{"kind": "tool", "tool": "review_checklist", "status": "rejected",
            "error": "candidate_invalid", "rejection_codes": {"evidence_relationship": 2, "SECRET": 3,
            "fact_value": True}, "initial_rejection_codes": {"fact_value": 1},
            "rejected_count": 2, "ordinal": 0, "quote": "SECRET", "arguments": "SECRET"},
           {"kind": "model", "operation": "extract", "status": "completed", "semantic_repair": True}]
    before = deepcopy(raw)
    trace = safe_trace_events(raw)
    assert trace[0]["summary"] == {"tool": "review_checklist", "status": "rejected",
        "error": "candidate_invalid", "rejection_codes": {"evidence_relationship": 2},
        "initial_rejection_codes": {"fact_value": 1}, "rejected_count": 2, "ordinal": 0}
    assert trace[1]["summary"]["semantic_repair"] is True
    assert "SECRET" not in json.dumps(trace)
    assert raw == before


@pytest.mark.parametrize("max_calls,unknown,expected", [(2, False, "model_budget"), (4, True, "connection_error")])
def test_anchor_correction_uses_shared_budget_and_does_not_replay_unknown_output(max_calls, unknown, expected):
    definition, repo, frozen = mixed_sample()

    class StoppedRepair(AnchorProvider):
        def extract(self, messages, schema, **options):
            if len(self.requests) == 1 and unknown:
                self.requests.append(deepcopy(messages))
                raise ProviderError("connection_error", outcome_unknown=True)
            return super().extract(messages, schema, **options)

    provider = StoppedRepair()
    result = DynamicAgent(repo, provider, definition, item_bindings=frozen,
                          budget=Budget(max_calls=max_calls)).ask("完整审查")
    assert result["status"] == "partial" and result["error"] == expected
    assert len(provider.requests) == (2 if unknown else 1)
    assert result["usage"]["unknown_usage_calls"] == int(unknown)
    assert not any(c["is_formal"] for c in result["payload"]["checks"])
