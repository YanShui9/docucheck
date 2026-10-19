"""A document answer is synthesized after retrieval and retains verified sources."""

from dataclasses import replace

import pytest

from backend.app.agent.assistant import AssistantAgent
from backend.app.evidence.repository import EvidenceRepository
from backend.app.evidence.retrieval import Retriever
from backend.app.parsing import EvidenceBlock
from backend.tests import test_dynamic_workflow
from backend.tests.test_assistant_agent import Decisions
from backend.tests.test_dynamic_agent import repository

dynamic_case = test_dynamic_workflow.dynamic_case
ingestion_case = test_dynamic_workflow.ingestion_case


def price_repository():
    original = repository().documents[1]
    blocks = tuple(EvidenceBlock(str(i), original.document_id, text, sheet="报价", cell=cell)
                   for i, (text, cell) in enumerate([
                       ("不含税单价", "A8"), ("25", "B8"), ("元/箱", "C8")]))
    document = replace(original, parsed=replace(original.parsed, blocks=blocks))
    return EvidenceRepository("case", 1, [document])


def ask_price(answer, *, citations=None):
    repo = price_repository()
    citations = citations if citations is not None else [
        {"evidence_id": b.evidence_id, "quote": b.text} for b in repo.blocks]
    provider = Decisions(("search_evidence", {"query": "单价", "supplier": "甲供应商"}),
                         ("answer_evidence", {"answer": answer, "citations": citations}))
    return repo, provider, AssistantAgent(repo, provider, retriever=Retriever(repo)).ask("苹果单价是多少？")


def test_price_answer_synthesizes_label_value_and_unit_without_extra_model_call():
    answer = "供应商的苹果报价为25元/箱，不含税。"
    repo, provider, result = ask_price(answer)
    assert result["status"] == "completed", result["error"]
    assert result["answer"] == answer
    assert result["payload"]["evidence_answer"] == {"version": 1, "text": answer}
    assert [c["quote"] for c in result["payload"]["citations"]] == [b.text for b in repo.blocks]
    assert all(b.evidence_id not in result["answer"] for b in repo.blocks)
    assert len(provider.messages) == result["usage"]["model_calls"] == 2
    assert result["dynamic_payload"] is None


def test_uncited_model_claim_is_replaced_with_no_answer():
    _, _, result = ask_price("苹果单价999元/箱，完全满足要求。", citations=[])
    assert result["status"] == "completed"
    assert result["answer"] == "本次检索未找到可直接回答问题的原文。"
    assert "999" not in result["answer"]
    assert result["payload"]["evidence_answer"]["text"] == result["answer"]


@pytest.mark.parametrize("citation", [
    {"evidence_id": "unread", "quote": "999元/箱"},
    {"evidence_id": "price", "quote": "999"},
])
def test_direct_answer_does_not_bypass_source_validation(citation):
    repo = price_repository()
    if citation["evidence_id"] == "price":
        citation = citation | {"evidence_id": repo.blocks[1].evidence_id}
    _, _, result = ask_price("苹果单价999元/箱。", citations=[citation])
    assert result["status"] == "partial"
    assert "999" not in result["answer"] and result["payload"]["citations"] == []


def test_known_internal_evidence_id_is_rendered_as_short_citation():
    ref = price_repository().blocks[1].evidence_id
    _, _, result = ask_price(f"苹果单价25元/箱，不含税。[{ref}]")
    assert result["status"] == "completed", result["error"]
    assert result["answer"] == "苹果单价25元/箱，不含税。[2]"


def test_answer_tool_requires_a_nonblank_bounded_direct_answer():
    repo = price_repository()
    agent = AssistantAgent(repo, Decisions())
    schema = next(s["function"]["parameters"] for s in agent.schemas
                  if s["function"]["name"] == "answer_evidence")
    assert "answer" in schema["required"]
    assert schema["properties"]["answer"]["maxLength"] == 1200


@pytest.mark.parametrize("answer", ["", "  ", "字" * 1201])
def test_invalid_direct_answer_never_publishes_evidence(answer):
    _, _, result = ask_price(answer)
    assert result["status"] == "partial" and not result["payload"]["citations"]


def test_unknown_internal_reference_cannot_be_published():
    _, _, result = ask_price("报价为25元/箱。[" + "a" * 64 + "]")
    assert result["status"] == "partial" and not result["payload"]["citations"]


@pytest.mark.parametrize("source,answer", [
    ("苹果单价25元/箱，不含税", "苹果单价为25元/箱，不含税。"),
    ("苹果单价25，单位未注明", "苹果单价为25，原文未注明单位。"),
    ("苹果单价25元/箱，仅适用于整车采购", "整车采购时苹果单价为25元/箱。"),
    ("旧价25元/箱；新报价28元/箱，以双方确认版本为准", "原文有25元/箱与28元/箱两种报价，适用版本需确认。"),
])
def test_answer_and_complete_qualified_source_remain_separate(source, answer):
    from backend.tests.test_assistant_audit import long_repository

    repo = long_repository(text=source)
    block = repo.blocks[0]
    result = AssistantAgent(repo, Decisions(
        ("search_evidence", {"query": source}),
        ("answer_evidence", {"answer": answer, "citations": [{"evidence_id": block.evidence_id, "quote": "25"}]})),
        retriever=Retriever(repo)).ask("苹果单价是多少？")
    assert result["status"] == "completed", result["error"]
    assert result["answer"] == answer
    assert result["payload"]["citations"][0]["quote"] == source


def test_multiple_suppliers_keep_distinct_answers_and_citations():
    repo = repository(metric="单价", unit="元/箱", values=("25", "28"))
    blocks = [b for b in repo.blocks if b.role == "quote"]
    answer = "甲供应商报价25元/箱，乙供应商报价28元/箱；这些原文未注明含税口径。"
    result = AssistantAgent(repo, Decisions(
        ("search_evidence", {"query": "苹果单价"}),
        ("answer_evidence", {"answer": answer, "citations": [{"evidence_id": b.evidence_id, "quote": b.text} for b in blocks]})),
        retriever=Retriever(repo)).ask("两家供应商分别报价多少？")
    assert result["status"] == "completed", result["error"]
    assert result["answer"] == answer
    assert [c["supplier"] for c in result["payload"]["citations"]] == ["甲供应商", "乙供应商"]


@pytest.mark.parametrize("dynamic_case", ["苹果单价25元/箱，不含税"], indirect=True)
@pytest.mark.parametrize("tamper", [None, "text", "citations", "retrieval", "downgrade", "legacy"])
def test_direct_answer_persistence_tampering_and_legacy_history(dynamic_case, tamper):
    from copy import deepcopy

    from sqlalchemy import select, update

    from backend.app.agent.assistant import checked_citation, render_evidence
    from backend.app.agent.assistant_answers import render_answer
    from backend.app.db.agent_snapshot import load_agent_snapshot
    from backend.app.db.models import agent_runs, assistant_runs
    from backend.app.evidence.repository import digest
    from backend.tests.test_assistant_workflow import run, send

    client, engine, case, snapshot, _ = dynamic_case
    repo = load_agent_snapshot(engine, case, snapshot)
    block = next(b for b in repo.blocks if b.supplier == "vendor-a")
    answer = "供应商的苹果单价为25元/箱，不含税。"
    receipt = send(dynamic_case, "供应商苹果单价是多少？")
    report = run(dynamic_case, receipt, Decisions(
        ("search_evidence", {"query": "苹果", "supplier": "vendor-a"}),
        ("answer_evidence", {"answer": answer, "citations": [{"evidence_id": block.evidence_id, "quote": block.text}]})))
    assert report.status == "completed", report
    url = f"/api/v1/agent-runs/{receipt['run_id']}/assistant-result"
    response = client.get(url)
    assert response.status_code == 200, response.text
    assert response.json()["result"]["answer"].splitlines()[0] == answer
    assert response.json()["result"]["evidence_answer"]["text"] == answer
    assert response.json()["result"]["citations"][0]["quote"] == block.text
    if tamper is None:
        assert client.get(url).json()["result"] == response.json()["result"]
        return

    payload = deepcopy(response.json()["result"])
    if tamper == "text":
        payload["evidence_answer"]["text"] = "供应商单价999元/箱。"
        payload["answer"] = render_answer(payload["evidence_answer"], payload["citations"], payload["retrieval"])
    elif tamper == "citations":
        other = next(b for b in repo.blocks if b.role == "requirement")
        payload["citations"] = [checked_citation(repo, other.evidence_id, other.text,
            windows=payload["source_windows"], preserve_context=True)]
    elif tamper == "retrieval":
        payload["retrieval"]["degraded_reason"] = "vector_provider_failed"
    else:
        payload.pop("evidence_answer")
        payload["answer"] = render_evidence(payload["citations"], payload["retrieval"])
    with engine.begin() as connection:
        connection.execute(update(assistant_runs).where(assistant_runs.c.run_id == receipt["run_id"]).values(
            payload_json=payload, payload_digest=digest(payload)))
        values = {"answer_text": payload["answer"]}
        if tamper == "legacy":
            checkpoint = deepcopy(connection.scalar(select(agent_runs.c.memory_json).where(agent_runs.c.id == receipt["run_id"])))
            checkpoint.pop("evidence_answer_digest")
            values["memory_json"] = checkpoint
        connection.execute(update(agent_runs).where(agent_runs.c.id == receipt["run_id"]).values(**values))
    reloaded = client.get(url)
    if tamper == "legacy":
        assert reloaded.status_code == 200, reloaded.text
        assert reloaded.json()["result"]["answer"] == payload["answer"]
    else:
        assert reloaded.status_code == 409
