"""Agent tools route source-bound field facts into deterministic checks."""

import json
import re
from dataclasses import replace
from pathlib import Path

from langchain_core.messages import AIMessage

from backend.app.agent.answer import render_answer
from backend.app.agent.graph import AgentSession
from backend.app.agent.tools import AnswerDraft, ToolExecutor
from backend.app.evidence.repository import EvidenceRepository, load_case
from backend.app.field_contract import FieldKey
from backend.tests.test_agent_graph import ScenarioProvider
from backend.tests.test_field_extraction import StorageCandidates
from backend.tests.test_m2_full_review import SevenFieldCandidates
from evals.m2_agent import ScriptedProvider

PACK = Path(__file__).resolve().parents[2] / "examples" / "dev-pack-001"


def test_agent_tools_extract_and_evaluate_storage_without_retrieval_top_k() -> None:
    repo = load_case(PACK)
    tools = ToolExecutor(repo)
    model = StorageCandidates()

    extraction = tools.execute(
        "extract_facts", {"supplier": "vendor-a", "field_keys": ["storage"]}, model,
    )
    fact_set = extraction["data"]["fact_sets"][0]
    assert fact_set["field_key"] == "storage"
    assert fact_set["complete"]
    assert model.seen == {block.evidence_id for block in repo.supplier_blocks("vendor-a")}

    evaluation = tools.execute(
        "evaluate_checks",
        {"supplier": "vendor-a", "fact_set_ids": [fact_set["fact_set_id"]]}, None,
    )
    check = evaluation["data"]["check_results"][0]
    assert check["business_status"] == "满足"
    assert check["field_key"] == "storage"
    assert set(check["evidence_refs"]) == set(extraction["evidence_refs"])


def test_three_supplier_results_keep_independent_fact_sets_and_answer_sources() -> None:
    original = load_case(PACK)
    documents = list(original.documents)
    for suffix in ("b", "c"):
        for document in original.documents:
            if document.role == "requirement":
                continue
            document_id = f"vendor-{suffix}-{document.role}"
            parsed = replace(document.parsed, document_id=document_id, blocks=tuple(
                replace(block, document_id=document_id) for block in document.parsed.blocks
            ))
            documents.append(replace(document, document_id=document_id,
                                     supplier=f"vendor-{suffix}", model=f"DC-{suffix}",
                                     parsed=parsed))
    repo = EvidenceRepository(original.case_id, original.revision + 1, documents)
    tools = ToolExecutor(repo)
    result_ids = []
    fact_ids = []
    for supplier in ("vendor-a", "vendor-b", "vendor-c"):
        extraction = tools.execute("extract_facts", {"supplier": supplier,
                                                   "field_keys": ["storage"]},
                                   StorageCandidates())
        fact_set_id = extraction["data"]["fact_sets"][0]["fact_set_id"]
        fact_ids.append(fact_set_id)
        evaluation = tools.execute("evaluate_checks", {"supplier": supplier,
                                                     "fact_set_ids": [fact_set_id]}, None)
        result_ids.extend(evaluation["data"]["check_result_ids"])

    assert len(set(fact_ids)) == len(set(result_ids)) == 3
    answer, status = render_answer(
        AnswerDraft(kind="check", check_result_ids=result_ids),
        tools, "完整审查全部供应商的存储",
    )
    assert status == "completed"
    for supplier in ("vendor-a", "vendor-b", "vendor-c"):
        assert f"{supplier} · 存储：满足" in answer


def test_three_supplier_eight_field_matrix_is_scoped_to_each_source() -> None:
    original = load_case(PACK)
    documents = list(original.documents)
    for suffix in ("b", "c"):
        for document in original.documents:
            if document.role == "requirement":
                continue
            document_id = f"vendor-{suffix}-{document.role}"
            parsed = replace(document.parsed, document_id=document_id, blocks=tuple(
                replace(block, document_id=document_id) for block in document.parsed.blocks
            ))
            documents.append(replace(document, document_id=document_id,
                                     supplier=f"vendor-{suffix}", model=f"DC-{suffix}",
                                     parsed=parsed))
    repo = EvidenceRepository(original.case_id, original.revision + 1, documents)
    tools = ToolExecutor(repo)
    memory = ScenarioProvider()
    other = SevenFieldCandidates()

    def extract(operation, messages, schema):
        if schema["function"]["name"] == "MemoryCandidates":
            return memory.extract(messages, schema)
        return other(operation, messages, schema)

    all_ids = []
    for supplier in ("vendor-a", "vendor-b", "vendor-c"):
        extraction = tools.execute(
            "extract_facts", {"supplier": supplier, "field_keys": list(FieldKey)}, extract,
        )
        fact_sets = extraction["data"]["fact_sets"]
        assert len(fact_sets) == 8
        assert all(item["complete"] for item in fact_sets)
        evaluation = tools.execute(
            "evaluate_checks", {"supplier": supplier,
                                "fact_set_ids": [item["fact_set_id"] for item in fact_sets]},
            None,
        )
        results = evaluation["data"]["check_results"]
        assert len(results) == 8
        assert {(item["supplier"], item["field_key"]) for item in results} == {
            (supplier, field.value) for field in FieldKey
        }
        for item in results:
            assert all(repo.read([ref])[0].supplier in (None, supplier)
                       for ref in item["evidence_refs"])
        all_ids.extend(evaluation["data"]["check_result_ids"])

    assert len(set(all_ids)) == 24
    answer, status = render_answer(
        AnswerDraft(kind="check", check_result_ids=all_ids),
        tools, "完整审查全部供应商的所有字段",
    )
    assert status == "completed"
    assert len(re.findall(r"^vendor-[abc] · ", answer, re.MULTILINE)) == 24
    assert "vendor-a · 存储：满足。存储符合明确的需求阈值。" in answer


def test_followup_without_field_filter_exposes_each_latest_field_result() -> None:
    repo = load_case(PACK)
    tools = ToolExecutor(repo)
    extraction = tools.execute("extract_facts", {"supplier": "vendor-a",
                                                "field_keys": ["storage", "screen_size"]},
                               SevenFieldCandidates())
    ids = [item["fact_set_id"] for item in extraction["data"]["fact_sets"]]
    tools.execute("evaluate_checks", {"supplier": "vendor-a", "fact_set_ids": ids}, None)

    response = tools.execute("get_check_results", {"supplier": "vendor-a"}, None)
    assert {item["field_key"] for item in response["data"]["check_results"]} == {
        FieldKey.STORAGE, FieldKey.SCREEN_SIZE,
    }


def test_graph_preserves_all_three_supplier_result_handles_after_compaction() -> None:
    original = load_case(PACK)
    documents = list(original.documents)
    for suffix in ("b", "c"):
        for document in original.documents:
            if document.role == "requirement":
                continue
            document_id = f"vendor-{suffix}-{document.role}"
            parsed = replace(document.parsed, document_id=document_id, blocks=tuple(
                replace(block, document_id=document_id) for block in document.parsed.blocks
            ))
            documents.append(replace(document, document_id=document_id,
                                     supplier=f"vendor-{suffix}", model=f"DC-{suffix}",
                                     parsed=parsed))
    repo = EvidenceRepository(original.case_id, original.revision + 1, documents)

    class MatrixProvider:
        model_id = "scripted-matrix"
        context_window = 100000

        def __init__(self):
            self.step = 0
            self.memory = ScenarioProvider()
            self.other = SevenFieldCandidates()

        def decide(self, messages, tools, **kwargs):
            step = self.step
            self.step += 1
            if step == 0:
                name, args = "list_case_sources", {}
            elif step in (1, 3, 5):
                supplier = ("vendor-a", "vendor-b", "vendor-c")[(step - 1) // 2]
                name, args = "extract_facts", {"supplier": supplier,
                                               "field_keys": list(FieldKey)}
            elif step in (2, 4, 6):
                supplier = ("vendor-a", "vendor-b", "vendor-c")[(step - 2) // 2]
                latest = json.loads(messages[-1].content)["data"]
                name, args = "evaluate_checks", {"supplier": supplier,
                    "fact_set_ids": [item["fact_set_id"] for item in latest["fact_sets"]]}
            else:
                handles = next(message for message in messages if message.type == "system"
                               and message.content.startswith("VERIFIED_CHECK_RESULTS="))
                available = json.loads(handles.content.split("=", 1)[1])
                assert len(available) == 24
                name, args = "submit_answer", {"kind": "check", "check_result_ids": [
                    item["check_result_id"] for item in available], "evidence_ids": []}
            return AIMessage(content="", tool_calls=[{"id": f"step-{step}",
                                                      "name": name, "args": args}],
                             usage_metadata={"input_tokens": 150, "output_tokens": 30,
                                             "total_tokens": 180})

        def extract(self, messages, schema, **kwargs):
            if schema["function"]["name"] == "MemoryCandidates":
                return self.memory.extract(messages, schema)
            return self.other("extract", messages, schema)

    provider = MatrixProvider()
    result = AgentSession(repo, provider, mode="thorough").ask(
        "完整审查全部供应商的所有字段")
    assert result.status == "completed", (result.error, result.usage)
    assert len(re.findall(r"^vendor-[abc] · ", result.answer, re.MULTILINE)) == 24


def test_partial_full_review_trace_records_only_safe_field_coverage_categories() -> None:
    repo = load_case(PACK)
    delivery_pair = {block.evidence_id for block in repo.blocks
                     if block.role == "quote" and block.cell in {"A9", "B9"}}

    class MissingDelivery(ScriptedProvider):
        def extract(self, messages, schema, **kwargs):
            reply = super().extract(messages, schema, **kwargs)
            if schema["function"]["name"] == "FieldCandidates":
                reply.tool_calls[0]["args"]["facts"] = [
                    fact for fact in reply.tool_calls[0]["args"]["facts"]
                    if set(fact["evidence_refs"]) != delivery_pair
                ]
            return reply

    result = AgentSession(repo, MissingDelivery(("vendor-a",)), mode="thorough").ask(
        "完整审查供应商 A 的八项指标")
    assert result.status == "partial"
    extraction = next(event for event in result.events if event.get("tool") == "extract_facts")
    coverage = {item["field_key"]: item for item in extraction["field_coverage"]}
    assert coverage["unit_price"] == {"field_key": "unit_price", "complete": True,
                                      "issue_types": []}
    assert coverage["delivery_days"]["complete"] is False
    assert "unverified_source" in coverage["delivery_days"]["issue_types"]
    assert "合同签订" not in str(coverage)
