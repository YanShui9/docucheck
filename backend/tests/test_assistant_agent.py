"""Offline routing cannot turn retrieval proximity or buyer demands into findings."""

from collections import deque

import pytest

from backend.app.agent.policy import Budget
from backend.app.evidence.repository import EvidenceRepository
from backend.app.evidence.retrieval import Retriever
from backend.tests.test_dynamic_agent import call, definition, repository


class Decisions:
    context_window = 32768

    def __init__(self, *actions):
        self.actions = deque(actions)
        self.messages = []

    def decide(self, messages, schemas, **options):
        self.messages.append(messages)
        # Existing business fixtures describe the intended patch. The protocol
        # now requires explicit preparation before exposing the strict patch tool.
        if self.actions and self.actions[0][0] == "propose_changes" and any(
                s["function"]["name"] == "prepare_changes" for s in schemas):
            targets = [op["criterion_id"] for op in self.actions[0][1].get("operations", [])
                       if op.get("op") in {"update", "remove"}]
            return call("prepare_changes", {"criterion_ids": targets, "item_ids": []})
        name, args = self.actions.popleft()
        return call(name, args)


def agent(repo, provider, **kwargs):
    from backend.app.agent.assistant import AssistantAgent

    return AssistantAgent(repo, provider, **kwargs)


def test_empty_chat_has_no_business_facts_or_checks():
    repo = EvidenceRepository.empty("case", "snap")
    result = agent(repo, Decisions(("chat", {"answer": "你好，可以先说明采购任务。"}))).ask("你好")
    assert result["status"] == "completed"
    assert result["payload"]["kind"] == "chat"
    assert result["payload"]["citations"] == []
    assert result["payload"]["candidate"] is None
    assert result["usage"]["model_calls"] == 1


def test_tls_failure_records_safe_detail_without_replay_or_formal_result():
    from backend.app.providers import ProviderError

    class TLSFailure:
        calls = 0

        def decide(self, *args, **kwargs):
            self.calls += 1
            raise ProviderError('connection_error', outcome_unknown=True,
                                detail='tls_certificate_verification')

    provider = TLSFailure()
    result = agent(EvidenceRepository.empty('case', 'snap'), provider).ask('你好')
    assert provider.calls == 1
    assert result['status'] == 'partial'
    assert result['dynamic_payload'] is None
    assert result['usage']['unknown_usage_calls'] == 1
    failures = [e for e in result['events'] if e.get('error') == 'connection_error']
    assert failures and failures[-1]['failure_detail'] == 'tls_certificate_verification'
    assert failures[-1]['outcome_unknown'] is True


@pytest.mark.parametrize('count,detail', [(0, 'missing_tool_call'), (2, 'multiple_tool_calls')])
def test_invalid_tool_count_keeps_known_usage_and_safe_failure_detail(count, detail):
    from langchain_core.messages import AIMessage

    class InvalidCount:
        def decide(self, *args, **kwargs):
            return AIMessage(content='', tool_calls=[{'name':'chat','args':{'answer':'hello'},'id':str(i)} for i in range(count)],
                usage_metadata={'input_tokens':100,'output_tokens':20,'total_tokens':120})

    result = agent(EvidenceRepository.empty('case','snap'), InvalidCount()).ask('你好')
    assert result['status'] == 'partial' and result['error'] == 'invalid_output'
    expected_calls = 2 if count == 2 else 1
    assert result['usage']['model_calls'] == expected_calls
    assert result['usage']['actual_input'] == 100 * expected_calls
    assert result['usage']['actual_output'] == 20 * expected_calls
    assert result['usage']['unknown_usage_calls'] == 0
    assert result['payload']['candidate'] is None and not result['payload']['citations']
    assert result['events'][-1]['status'] == 'failed'
    assert result['events'][-1]['failure_detail'] == detail


@pytest.mark.parametrize("max_calls", [1, 2])
def test_multiple_tools_execute_none_before_one_bounded_correction(max_calls):
    from langchain_core.messages import AIMessage

    class MultipleThenOne:
        calls = 0

        def decide(self, messages, schemas, **options):
            self.calls += 1
            if self.calls == 1:
                return AIMessage(content="", tool_calls=[
                    {"name": "chat", "args": {"answer": answer}, "id": str(index)}
                    for index, answer in enumerate(("不要执行这个", "也不要执行这个"))],
                    usage_metadata={"input_tokens": 100, "output_tokens": 20, "total_tokens": 120})
            return call("chat", {"answer": "已校正，只执行本条。"})

    provider = MultipleThenOne()
    result = agent(EvidenceRepository.empty("case", "snap"), provider,
                   budget=Budget(max_calls=max_calls)).ask("你好")
    assert provider.calls == max_calls
    assert result["usage"]["unknown_usage_calls"] == 0
    assert result["usage"]["tools"] == (1 if max_calls == 2 else 0)
    assert "不要执行" not in result["answer"]
    if max_calls == 2:
        assert result["status"] == "completed" and result["answer"] == "已校正，只执行本条。"
        assert result["events"][0]["failure_detail"] == "multiple_tool_calls"
    else:
        assert result["error"] == "model_budget"


@pytest.mark.parametrize("sources", ["empty", "supplier_only", "buyer"])
def test_buyer_file_proposal_capability_is_only_available_with_buyer_document(sources):
    repo = (EvidenceRepository.empty("case", "snap") if sources == "empty" else repository())
    if sources == "supplier_only":
        repo = EvidenceRepository(repo.case_id, repo.revision,
            [document for document in repo.documents if document.role != "requirement"])
    tools = {schema["function"]["name"] for schema in agent(repo, Decisions()).schemas}
    assert ("propose_checklist" in tools) is (sources == "buyer")
    assert {"prepare_changes", "read_context", "review_checklist"} <= tools


def test_evidence_answer_keeps_verified_original_and_location_in_citations():
    repo = repository()
    block = next(b for b in repo.blocks if b.role == "quote" and b.supplier == "甲供应商")
    provider = Decisions(("search_evidence", {"query": "苹果数量", "supplier": "甲供应商"}),
                         ("answer_evidence", {"answer": block.text, "citations": [{"evidence_id": block.evidence_id, "quote": block.text}]}))
    result = agent(repo, provider, retriever=Retriever(repo)).ask("甲供应商苹果数量多少")
    assert result["status"] == "completed"
    assert result["payload"]["kind"] == "evidence"
    assert result["answer"] == block.text
    assert result["payload"]["citations"][0]["location"] == "资料!A1"
    assert block.evidence_id not in result["answer"]
    assert result["payload"]["citations"][0]["supplier"] == "甲供应商"


@pytest.mark.parametrize("wrong", ["编造的原句", "苹果 数量999箱"])
def test_citation_cannot_fabricate_quote_or_read_unretrieved_evidence(wrong):
    repo = repository()
    block = repo.blocks[0]
    provider = Decisions(("search_evidence", {"query": "苹果数量"}),
                         ("answer_evidence", {"answer": wrong, "citations": [{"evidence_id": block.evidence_id, "quote": wrong}]}))
    result = agent(repo, provider, retriever=Retriever(repo)).ask("数量")
    assert result["status"] == "partial"
    assert wrong not in result["answer"]
    assert result["payload"]["citations"] == []


def test_empty_direct_evidence_marks_this_search_not_found():
    repo = repository()
    result = agent(repo, Decisions(("search_evidence", {"query": "担保机构"}),
                                  ("answer_evidence", {"answer": '本次未找到直接依据。', "citations": []})), retriever=Retriever(repo)).ask("担保机构")
    assert "本次检索未找到" in result["answer"]
    assert "证据不足" not in result["answer"]


def test_formal_delegate_receives_same_budget():
    repo = repository()
    budget = Budget(max_calls=1)
    received = []

    def formal(operation, shared):
        received.append((operation, shared))
        shared.reserve([], [])

    result = agent(repo, Decisions(("review_checklist", {})), definition=definition(),
                   formal=formal, budget=budget).ask("审查")
    assert received == [("review", budget)]
    assert result["error"] == "model_budget"
    assert result["usage"]["model_calls"] == 1


def test_changes_are_preview_and_keep_fixed_base():
    repo, base = repository(), definition()
    criterion = {k: v for k, v in base.criteria[0].model_dump(mode="json").items() if k not in {"criterion_id", "source"}}
    criterion["rule"]["value"] = "20"
    result = agent(repo, Decisions(("propose_changes", {"items": [], "operations": [
        {"op": "update", "criterion_id": "metric-1", "criterion": criterion, "reason": "用户调整"}]})), definition=base).ask("数量改20")
    assert result["status"] == "completed"
    assert result["payload"]["kind"] == "changes"
    assert result["payload"]["candidate"]["criteria"][0]["criterion_id"] == "metric-1"
    assert base.criteria[0].rule.value == "10"


def test_accumulated_source_reads_stop_before_payload_capacity_and_keep_usage():
    from dataclasses import replace

    from backend.app.agent.assistant import AssistantPayload
    from backend.app.parsing import EvidenceBlock

    original = repository().documents[1]
    blocks = tuple(EvidenceBlock(str(i), original.document_id, f"原文{i}", sheet="资料", cell=f"A{i + 1}")
                   for i in range(120))
    document = replace(original, parsed=replace(original.parsed, blocks=blocks))
    repo = EvidenceRepository("case", 1, [document])

    class SuccessiveSources(Retriever):
        calls = 0
        read_calls = 0

        def search(self, query, supplier=None, limit=12):
            refs = [b.evidence_id for b in self.repository.blocks[self.calls * 12:(self.calls + 1) * 12]]
            self.calls += 1
            return [{"evidence_refs": refs}]

        def read_source(self, ids, **kwargs):
            self.read_calls += 1
            return super().read_source(ids, **kwargs)

    retriever = SuccessiveSources(repo)
    provider = Decisions(*[("search_evidence", {"query": "原文", "limit": 12})] * 10,
                         ("answer_evidence", {"answer": '本次未找到直接依据。', "citations": []}))
    shared = Budget(max_decisions=12, max_calls=32, max_tools=24, max_input=200000,
                    max_output=12000, max_prompt=100000, context_window=100000)
    result = agent(repo, provider, mode="thorough", retriever=retriever, budget=shared).ask("查询资料")
    assert result["status"] == "partial" and result["error"] == "source_read_budget"
    assert result["usage"]["model_calls"] == 9
    assert result["usage"]["actual_input"] == 900
    assert retriever.read_calls == 8
    assert len(result["payload"]["source_reads"]) == 96
    assert set(result["payload"]["source_reads"]) == {b.evidence_id for b in repo.blocks[:96]}
    AssistantPayload.model_validate(result["payload"])
