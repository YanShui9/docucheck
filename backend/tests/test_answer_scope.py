"""A checked answer may cite only evidence belonging to its verified result."""

from dataclasses import replace
from pathlib import Path

import pytest
from pydantic import ValidationError

from backend.app.agent.answer import render_answer, resolve_question_scope
from backend.app.agent.tools import AnswerDraft, ToolExecutor
from backend.app.evidence.repository import EvidenceRepository, load_case
from backend.app.extraction.validation import validate_memory
from backend.app.rules.memory import evaluate_memory
from backend.tests.test_field_extraction import StorageCandidates

PACK = Path(__file__).resolve().parents[2] / "examples/dev-pack-001"
PACK_THREE = Path(__file__).resolve().parents[2] / "examples/dev-pack-003"


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


def test_answer_tool_contract_allows_bounded_matrix_results():
    draft = AnswerDraft(kind="check", check_result_ids=["memory", "storage"])
    assert len(draft.check_result_ids) == 2
    with pytest.raises(ValidationError):
        AnswerDraft(kind="check", check_result_ids=[str(i) for i in range(25)])


def test_full_review_can_render_two_verified_fields_for_one_supplier():
    repo = load_case(PACK)
    tools, memory_id = _checked_tools(repo)
    fact_set = tools.execute("extract_facts", {"supplier": "vendor-a",
                                              "field_keys": ["storage"]}, StorageCandidates())
    storage_set_id = fact_set["data"]["fact_sets"][0]["fact_set_id"]
    checked = tools.execute("evaluate_checks", {"supplier": "vendor-a",
                                               "fact_set_ids": [storage_set_id]}, None)
    storage_id = checked["data"]["check_result_ids"][0]

    answer, status = render_answer(
        AnswerDraft(kind="check", check_result_ids=[memory_id, storage_id]),
        tools, "完整审查供应商 A 的内存和存储",
    )

    assert status == "completed"
    assert "内存：资料冲突" in answer
    assert "存储：满足" in answer


@pytest.mark.parametrize("question", [
    "请完整审查供应商 A 的八项指标",
    "全面审查供应商 A 的所有字段",
    "请核对供应商 A 的全部指标",
    "请核对供应商 A 的所有指标",
    "完整审查供应商 A",
])
def test_full_eight_field_review_rejects_a_subset_even_if_other_results_exist(question):
    from backend.app.field_contract import FieldKey
    from backend.tests.test_m2_full_review import SevenFieldCandidates

    repo = load_case(PACK)
    tools, memory_id = _checked_tools(repo)
    extracted = tools.execute("extract_facts", {"supplier": "vendor-a",
                                               "field_keys": list(FieldKey)[1:]},
                              SevenFieldCandidates())
    tools.execute("evaluate_checks", {"supplier": "vendor-a", "fact_set_ids": [
        item["fact_set_id"] for item in extracted["data"]["fact_sets"]]}, None)
    assert len(tools.results) == 8

    with pytest.raises(ValueError, match="answer_invalid"):
        render_answer(AnswerDraft(kind="check", check_result_ids=[memory_id]),
                      tools, question)


def test_all_supplier_review_rejects_one_supplier_only():
    repo = _two_supplier_repo()
    tools, a_id = _checked_tools(repo)

    with pytest.raises(ValueError, match="answer_invalid"):
        render_answer(AnswerDraft(kind="check", check_result_ids=[a_id]),
                      tools, "请审查所有供应商的内存")


@pytest.mark.parametrize("question", [
    "请比较供应商 A 和 B 的内存",
    "比较 A 和 B 的内存",
])
def test_two_named_suppliers_require_both_results(question):
    repo = _two_supplier_repo()
    tools, a_id = _checked_tools(repo)

    with pytest.raises(ValueError, match="answer_invalid"):
        render_answer(AnswerDraft(kind="check", check_result_ids=[a_id]),
                      tools, question)


def test_source_question_naming_two_suppliers_requires_both_sources():
    repo = _two_supplier_repo()
    tools, _ = _checked_tools(repo)
    quotes = {block.supplier: block for block in repo.blocks
              if block.role == "quote" and block.cell == "B6"}
    tools.execute("read_source", {"evidence_ids": [block.evidence_id
                                                   for block in quotes.values()]}, None)
    question = "请列出供应商 A 和 B 的报价原文"

    with pytest.raises(ValueError, match="answer_invalid"):
        render_answer(AnswerDraft(kind="evidence",
                                  evidence_ids=[quotes["vendor-b"].evidence_id]),
                      tools, question)
    answer, status = render_answer(AnswerDraft(kind="evidence",
                                              evidence_ids=[block.evidence_id
                                                            for block in quotes.values()]),
                                   tools, question)
    assert status == "completed"
    assert "vendor-a-quote.xlsx" in answer and "vendor-b-quote" in answer


def test_evidence_only_answer_cannot_show_another_named_supplier():
    repo = _two_supplier_repo()
    tools, _ = _checked_tools(repo)
    b_quote = next(block for block in repo.blocks
                   if block.supplier == "vendor-b" and block.role == "quote"
                   and block.cell == "B6")
    tools.execute("read_source", {"evidence_ids": [b_quote.evidence_id]}, None)

    with pytest.raises(ValueError, match="answer_invalid"):
        render_answer(AnswerDraft(kind="evidence", evidence_ids=[b_quote.evidence_id]),
                      tools, "供应商 A 的报价原文是什么？")


def test_all_fields_request_cannot_be_answered_with_only_a_source_quote():
    repo = load_case(PACK)
    tools, _ = _checked_tools(repo)
    quote = next(block for block in repo.blocks
                 if block.supplier == "vendor-a" and block.role == "quote" and block.cell == "B6")
    tools.execute("read_source", {"evidence_ids": [quote.evidence_id]}, None)

    with pytest.raises(ValueError, match="answer_invalid"):
        render_answer(AnswerDraft(kind="evidence", evidence_ids=[quote.evidence_id]),
                      tools, "请核对供应商 A 的所有指标")


def test_full_review_with_requested_sources_still_requires_results():
    repo = load_case(PACK)
    tools, _ = _checked_tools(repo)
    quote = next(block for block in repo.blocks
                 if block.supplier == "vendor-a" and block.role == "quote" and block.cell == "B6")
    tools.execute("read_source", {"evidence_ids": [quote.evidence_id]}, None)

    with pytest.raises(ValueError, match="answer_invalid"):
        render_answer(AnswerDraft(kind="evidence", evidence_ids=[quote.evidence_id]),
                      tools, "请审查供应商 A 的八项指标并列出来源")


def test_question_about_one_of_eight_fields_requires_only_that_field():
    repo = load_case(PACK)
    tools, memory_id = _checked_tools(repo)

    answer, status = render_answer(AnswerDraft(kind="check", check_result_ids=[memory_id]),
                                   tools, "供应商 A 八项中的内存是否达标？")

    assert status == "completed"
    assert "内存：资料冲突" in answer


def test_explicit_supplier_pair_overrides_previous_conversation_focus():
    tools = ToolExecutor(load_case(PACK_THREE))

    scope = resolve_question_scope("比较 A 和 B 的内存", tools,
                                   required_supplier="vendor-c")

    assert scope.suppliers == frozenset({"vendor-a", "vendor-b"})


@pytest.mark.parametrize("question, expected", [
    ("A 的报价原文是什么？", {"vendor-a"}),
    ("A 和 B 的报价原文是什么？", {"vendor-a", "vendor-b"}),
])
def test_source_question_with_short_supplier_names_has_explicit_scope(question, expected):
    tools = ToolExecutor(load_case(PACK_THREE))

    scope = resolve_question_scope(question, tools, required_supplier="vendor-c")

    assert scope.suppliers == frozenset(expected)


def test_supplier_handle_does_not_match_prefix_of_another_handle():
    original = load_case(PACK)
    documents = list(original.documents)
    for document in original.documents:
        if document.role == "requirement":
            continue
        document_id = f"vendor-abc-{document.role}"
        parsed = replace(document.parsed, document_id=document_id, blocks=tuple(
            replace(block, document_id=document_id) for block in document.parsed.blocks
        ))
        documents.append(replace(document, document_id=document_id, supplier="vendor-abc",
                                 model="ABC-1", parsed=parsed))
    tools = ToolExecutor(EvidenceRepository(original.case_id, original.revision + 1, documents))

    scope = resolve_question_scope("vendor-abc 的报价原文", tools)

    assert scope.suppliers == frozenset({"vendor-abc"})


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
