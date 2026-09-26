"""A checked answer may cite only evidence belonging to its verified result."""

from dataclasses import replace
from pathlib import Path

import pytest
from pydantic import ValidationError

from backend.app.agent.answer import render_answer
from backend.app.agent.tools import AnswerDraft, ToolExecutor
from backend.app.evidence.repository import EvidenceRepository, load_case
from backend.app.extraction.validation import validate_memory
from backend.app.rules.memory import evaluate_memory

PACK = Path(__file__).resolve().parents[2] / "examples/dev-pack-001"


def _two_supplier_repo() -> EvidenceRepository:
    original = load_case(PACK)
    documents = list(original.documents)
    for document in original.documents:
        if document.role == "requirement":
            continue
        document_id = f"vendor-b-{document.role}"
        parsed = replace(document.parsed, document_id=document_id, blocks=tuple(
            replace(block, document_id=document_id) for block in document.parsed.blocks
        ))
        documents.append(replace(document, document_id=document_id, supplier="vendor-b",
                                 model="B-1", parsed=parsed))
    return EvidenceRepository(original.case_id, original.revision + 1, documents)


def _checked_tools(repo: EvidenceRepository, supplier: str = "vendor-a") -> tuple[ToolExecutor, str]:
    facts = []
    for block in repo.supplier_blocks(supplier):
        if "已装内存" not in block.text or "GB" not in block.text:
            continue
        facts.append(validate_memory({
            "raw_value": "32" if "32" in block.text else "16",
            "raw_unit": "GB", "qualifier": "installed",
            "comparator": "gte" if block.role == "requirement" else "eq",
            "evidence_refs": [block.evidence_id],
        }, repo, supplier))
    result = evaluate_memory(repo, supplier, facts, scan_complete=True)
    assert result.business_status == "资料冲突"
    tools = ToolExecutor(repo)
    tools.results[result.check_result_id] = result
    tools.execute("get_check_results", {"supplier": supplier}, None)
    return tools, result.check_result_id


def test_checked_answer_rejects_seen_evidence_from_another_supplier():
    repo = _two_supplier_repo()
    tools, result_id = _checked_tools(repo)
    b_quote = next(block for block in repo.blocks
                   if block.supplier == "vendor-b" and block.role == "quote" and block.cell == "B6")
    tools.execute("read_source", {"evidence_ids": [b_quote.evidence_id]}, None)

    draft = AnswerDraft(kind="check", check_result_ids=[result_id],
                        evidence_ids=[b_quote.evidence_id])
    with pytest.raises(ValueError, match="answer_invalid"):
        render_answer(draft, tools, "供应商 A 的内存是否达标？")


def test_one_supplier_check_answer_rejects_mixed_result_ids():
    repo = _two_supplier_repo()
    tools, a_result_id = _checked_tools(repo, "vendor-a")
    b_tools, b_result_id = _checked_tools(repo, "vendor-b")
    tools.results[b_result_id] = b_tools.results[b_result_id]
    tools.execute("get_check_results", {"supplier": "vendor-b"}, None)

    draft = AnswerDraft.model_construct(kind="check",
                                       check_result_ids=[a_result_id, b_result_id], evidence_ids=[])
    with pytest.raises(ValueError, match="answer_invalid"):
        render_answer(draft, tools, "供应商 A 的内存是否达标？")


def test_one_supplier_check_answer_rejects_old_and_new_result_together():
    repo = load_case(PACK)
    tools, result_id = _checked_tools(repo)
    tools.results["older-partial"] = replace(
        tools.results[result_id], check_result_id="older-partial", processing_status="partial",
        business_status=None, reason_code="coverage_incomplete",
    )
    draft = AnswerDraft.model_construct(kind="check",
                                       check_result_ids=["older-partial", result_id], evidence_ids=[])

    with pytest.raises(ValueError, match="answer_invalid"):
        render_answer(draft, tools, "供应商 A 的内存是否达标？")


def test_answer_tool_contract_allows_only_one_check_result():
    with pytest.raises(ValidationError):
        AnswerDraft(kind="check", check_result_ids=["older-partial", "newer-complete"])


@pytest.mark.parametrize("question", ["你根据哪两处判断冲突？", "根据哪两处判断？"])
def test_conflict_source_followup_shows_quote_and_spec_without_requirement(question):
    repo = load_case(PACK)
    tools, result_id = _checked_tools(repo)
    result = tools.results[result_id]
    assert {block.role for block in repo.read(list(result.reason_evidence_refs))} == {"quote", "spec"}
    assert set(result.reason_evidence_refs) < set(result.evidence_refs)

    answer, status = render_answer(
        AnswerDraft(kind="check", check_result_ids=[result_id]),
        tools, question,
    )

    assert status == "completed"
    assert "vendor-a-quote.xlsx" in answer
    assert "vendor-a-spec.pdf" in answer
    assert "requirements.pdf" not in answer


def test_conflict_source_followup_requires_the_verified_check_result():
    repo = _two_supplier_repo()
    tools, _ = _checked_tools(repo)
    b_quote = next(block for block in repo.blocks
                   if block.supplier == "vendor-b" and block.role == "quote" and block.cell == "B6")
    tools.execute("read_source", {"evidence_ids": [b_quote.evidence_id]}, None)

    draft = AnswerDraft(kind="evidence", evidence_ids=[b_quote.evidence_id])
    with pytest.raises(ValueError, match="answer_invalid"):
        render_answer(draft, tools, "你根据哪两处判断冲突？")


def test_memory_answer_rejects_result_for_a_different_field():
    repo = load_case(PACK)
    tools, result_id = _checked_tools(repo)
    tools.results["other-field"] = replace(tools.results[result_id],
                                           check_result_id="other-field", field_key="storage")

    draft = AnswerDraft(kind="check", check_result_ids=["other-field"])
    with pytest.raises(ValueError, match="answer_invalid"):
        render_answer(draft, tools, "供应商 A 的内存是否达标？")
