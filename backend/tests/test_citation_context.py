"""An exact substring does not authorize dropping surrounding qualifications."""
from copy import deepcopy

import pytest

from backend.app.agent.assistant import AssistantAgent, checked_citation
from backend.app.agent.assistant_evidence import verify_windows
from backend.app.evidence.retrieval import Retriever
from backend.tests.test_assistant_audit import long_repository
from backend.tests.test_assistant_stages import StageProvider


@pytest.mark.parametrize("text,excerpt", [
    ("空调单价不含税80元/台", "含税80元/台"),
    ("办公椅数量并非10把，而是另行确认", "10把"),
    ("布料不能保证10米；以双方确认数量为准", "10米"),
])
def test_current_answer_retains_complete_source_context(text, excerpt):
    repo = long_repository(text=text)
    ref = repo.blocks[0].evidence_id
    result = AssistantAgent(repo, StageProvider(
        ("search_evidence", {"query": text, "limit": 1}),
        ("answer_evidence", {"citations": [{"evidence_id": ref, "quote": excerpt}]})),
        retriever=Retriever(repo)).ask("供应商怎样承诺？")
    assert result["status"] == "completed", result["error"]
    payload = result["payload"]
    assert payload["citations"][0]["quote"] == text
    assert payload["citations"][0]["start_char"] == 0
    assert text in result["answer"] and payload["citation_context_version"] == 1
    verify_windows(repo, payload)
    shortened = deepcopy(payload)
    shortened["citations"][0].update(quote=excerpt, start_char=text.index(excerpt))
    with pytest.raises(ValueError, match="assistant_source_changed"):
        verify_windows(repo, shortened)


def test_partial_read_cannot_certify_context_before_the_window():
    text = "背景。" * 180 + "不含税80元/台"
    repo = long_repository(text=text)
    ref = repo.blocks[0].evidence_id
    start = text.index("含税80")
    with pytest.raises(ValueError, match="citation_context_unread"):
        checked_citation(repo, ref, "含税80元/台", windows=[{
            "evidence_id": ref, "start_char": start, "text": text[start:]}], preserve_context=True)


def test_oversized_single_clause_cannot_be_presented_as_complete_context():
    text = "不含税" + "说明" * 400 + "80元/台"
    repo = long_repository(text=text)
    ref = repo.blocks[0].evidence_id
    with pytest.raises(ValueError, match="citation_context_limit"):
        checked_citation(repo, ref, "80元/台", preserve_context=True)


def test_previous_citation_protocol_keeps_original_substring():
    text = "空调单价不含税80元/台"
    repo = long_repository(text=text)
    citation = checked_citation(repo, repo.blocks[0].evidence_id, "含税80元/台")
    assert citation["quote"] == "含税80元/台" and citation["start_char"] == text.index("含税80")


def test_long_sentence_context_is_stable_when_cited_and_reverified():
    text = "背景。" * 160 + "空调不含税80元/台。后续说明。"
    repo = long_repository(text=text)
    ref = repo.blocks[0].evidence_id
    start = text.index("空调")
    windows = [{"evidence_id": ref, "start_char": start, "text": text[start:]}]
    citation = checked_citation(repo, ref, "80元/台", windows=windows, preserve_context=True)
    assert citation["quote"] == "空调不含税80元/台。"
    assert checked_citation(repo, ref, citation["quote"], windows=windows, preserve_context=True) == citation


def test_line_wrapping_does_not_drop_a_following_qualification():
    text = "背景。" * 180 + "空调单价80元/台\n不含税。"
    repo = long_repository(text=text)
    ref = repo.blocks[0].evidence_id
    start = text.index("空调")
    citation = checked_citation(repo, ref, "80元/台", windows=[{
        "evidence_id": ref, "start_char": start, "text": text[start:]}], preserve_context=True)
    assert citation["quote"] == "空调单价80元/台\n不含税。"
