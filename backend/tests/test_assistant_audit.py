"""Regression evidence from the context, memory and source-window audit."""
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from backend.app.agent.assistant import AssistantAgent
from backend.app.agent.assistant_evidence import supports, verify_windows
from backend.app.agent.policy import input_bound
from backend.app.evidence.repository import EvidenceRepository
from backend.app.evidence.retrieval import Retriever
from backend.app.parsing import EvidenceBlock
from backend.tests.test_assistant_stages import StageProvider, addition, definition
from backend.tests.test_dynamic_agent import call, repository


def long_repository(count=1, text=None):
    original = repository().documents[1]
    blocks = tuple(EvidenceBlock(str(i), original.document_id,
        text or ('quantity ' + 'a' * 780), page_idx=i) for i in range(count))
    return EvidenceRepository('case', 1, [replace(original, parsed=replace(original.parsed, blocks=blocks))])


def test_simple_date_uses_no_model_or_prompt_and_normal_chat_does_not_read_clock():
    provider = StageProvider()
    result = AssistantAgent(EvidenceRepository.empty('case', 'snap'), provider,
        clock=lambda: datetime(2026, 10, 8, 16, 1, tzinfo=UTC)).ask('今天几号？')
    assert result['status'] == 'completed' and '2026-10-09' in result['answer']
    assert result['usage']['model_calls'] == 0 and not provider.requests

    def forbidden_clock():
        raise AssertionError('unneeded_clock')

    provider = StageProvider(('chat', {'answer':'你好'}))
    result = AssistantAgent(EvidenceRepository.empty('case', 'snap'), provider, clock=forbidden_clock).ask('你好')
    assert result['status'] == 'completed'
    assert '服务端运行时日期=' not in provider.requests[0][0][0].content


def test_complex_time_question_can_request_clock_and_keep_it_through_edit_stage():
    provider = StageProvider(('read_context', {'scope':'current_time'}),
        ('prepare_changes', {'criterion_ids':[], 'item_ids':['phone']}), ('propose_changes', addition()))
    result = AssistantAgent(EvidenceRepository.empty('case', 'snap'), provider, definition=definition(1),
        clock=lambda: datetime(2026, 10, 8, 16, 1, tzinfo=UTC)).ask('参考今天的日期，补充交期需要确认的事项')
    assert result['status'] == 'completed'
    assert '2026-10-09' not in provider.requests[0][0][0].content
    assert '2026-10-09' in provider.requests[-1][0][0].content


def test_retrieved_tail_is_read_at_its_actual_offset_and_can_be_cited():
    needle = '交付期限为签约后17个工作日'
    repo = long_repository(text='背景说明。' * 200 + needle)
    block = repo.blocks[0]

    class Provider:
        calls = 0
        def decide(self, messages, schemas, **kwargs):
            self.calls += 1
            if self.calls == 1:
                return call('search_evidence', {'query':'交付期限', 'limit':1})
            assert needle in messages[-1].content
            return call('answer_evidence', {'citations':[{'evidence_id':block.evidence_id,'quote':needle}]})

    result = AssistantAgent(repo, Provider(), retriever=Retriever(repo)).ask('供应商承诺多久交付')
    assert result['status'] == 'completed', result['error']
    assert needle in result['answer']
    assert result['payload']['source_windows'][0]['start_char'] > 0


def test_repeated_quote_is_located_in_the_window_actually_read():
    from backend.app.agent.assistant import checked_citation

    needle = 'DELIVERY 17 WORKDAYS'
    repo = long_repository(text='old example: ' + needle + '\n' + 'background ' * 150
        + '\nlatest commitment: ' + needle)
    ref = repo.blocks[0].evidence_id
    provider = StageProvider(('search_evidence', {'query': 'latest commitment', 'limit': 1}),
        ('answer_evidence', {'citations': [{'evidence_id': ref, 'quote': needle}]}))
    result = AssistantAgent(repo, provider, retriever=Retriever(repo)).ask('最新承诺是多少')
    assert result['status'] == 'completed', result['error']
    payload = result['payload']
    assert payload['citations'][0]['start_char'] == repo.blocks[0].text.rindex(needle)
    assert payload['citation_offsets_version'] == 1
    verify_windows(repo, payload)
    assert checked_citation(repo, ref, needle, windows=payload['source_windows']) == payload['citations'][0]
    payload['citations'][0]['start_char'] = repo.blocks[0].text.index(needle)
    with pytest.raises(ValueError, match='assistant_source_changed'):
        verify_windows(repo, payload)


def test_repeated_retrieval_is_bounded_and_large_result_exposes_followup_handles():
    repo = long_repository(count=12)
    provider = StageProvider(*[('search_evidence', {'query':'quantity','limit':12})] * 3,
        ('answer_evidence', {'citations':[]}))
    result = AssistantAgent(repo, provider, retriever=Retriever(repo)).ask('查采购数量')
    assert result['status'] == 'completed', result['error']
    assert all(input_bound(m,s) <= 16000 for m,s in provider.requests)
    assert len(result['payload']['source_windows']) <= 12
    assert result['context']['input_bound'] == input_bound(*provider.requests[-1])


def test_history_reader_is_available_and_keeps_returned_context_untrusted():
    received = []
    def read(ids, **kwargs):
        received.append((ids,kwargs))
        return [{'message_id':'old', 'text':'先前买方说要黑色', 'next_char':None, 'truncated':False}]
    provider = StageProvider(('read_conversation_history', {'message_ids':['old'], 'part':'question'}),
        ('chat', {'answer':'你先前提到黑色。'}))
    result = AssistantAgent(EvidenceRepository.empty('case','snap'),provider,history_reader=read).ask('我先前说什么颜色')
    assert result['status'] == 'completed' and received == [(['old'], {'part':'question','start_char':0})]
    assert '先前买方说要黑色' in provider.requests[-1][0][-1].content
    assert result['payload']['candidate'] is None


def test_addition_cannot_target_unprepared_existing_item():
    base = definition(1).model_dump(mode='json')
    base['items'].append({'item_id':'printer','name':'打印机'})
    patch = addition()
    patch['operations'][0]['criterion']['item_id'] = 'printer'
    provider = StageProvider(('prepare_changes', {'criterion_ids':[], 'item_ids':['phone']}), ('propose_changes', patch))
    result = AssistantAgent(EvidenceRepository.empty('case','snap'),provider,definition=base).ask('给手机补一个交期要求')
    assert result['status'] == 'partial' and result['payload']['candidate'] is None


def test_hybrid_source_can_page_past_first_window_and_retains_both_reads():
    import json

    from backend.tests.test_hybrid_retrieval import setup

    needle = '交付期限为签约后17个工作日'
    repo = long_repository(text='背景。' * 100 + needle)
    retriever, _, _ = setup(repo)
    ref = repo.blocks[0].evidence_id

    class Provider:
        calls = 0

        def decide(self, messages, schemas, **kwargs):
            self.calls += 1
            if self.calls == 1:
                return call('search_evidence', {'query': '交付期限', 'limit': 1})
            observation = json.loads(messages[-1].content)
            source = observation['sources'][0]
            if self.calls == 2:
                assert needle not in source['text'] and source['next_char'] > 0
                return call('read_source', {'evidence_ids': [ref], 'start_char': source['next_char']})
            assert needle in source['text']
            return call('answer_evidence', {'citations': [{'evidence_id': ref, 'quote': needle}]})

    result = AssistantAgent(repo, Provider(), retriever=retriever).ask('交付期限是什么')
    assert result['status'] == 'completed', result['error']
    assert len(result['payload']['source_windows']) == 2
    verify_windows(repo, result['payload'])


def test_paging_rejects_unretrieved_source_and_disjoint_quote():
    repo = long_repository(count=2)
    provider = StageProvider(('search_evidence', {'query': 'quantity', 'limit': 1}),
        ('read_source', {'evidence_ids': [repo.blocks[1].evidence_id]}))
    result = AssistantAgent(repo, provider, retriever=Retriever(repo)).ask('采购数量')
    assert result['status'] == 'partial' and not result['payload']['citations']
    assert not supports([{'evidence_id': 'x', 'text': '甲'}, {'evidence_id': 'x', 'text': '乙'}], 'x', '甲乙')


@pytest.mark.parametrize('field,value', [('start_char', 1), ('text', '伪造原文')])
def test_persisted_source_window_rejects_changed_offset_or_text(field, value):
    repo = long_repository(text='quantity 20')
    ref = repo.blocks[0].evidence_id
    payload = {'source_reads': {ref: 'quantity 20'}, 'source_windows': [
        {'evidence_id': ref, 'start_char': 0, 'text': 'quantity 20'}], 'citations': []}
    payload['source_windows'][0][field] = value
    with pytest.raises(ValueError, match='assistant_source_changed'):
        verify_windows(repo, payload)


def test_context_rejection_records_actual_failed_request_size():
    from backend.app.agent.policy import Budget

    provider = StageProvider()
    result = AssistantAgent(EvidenceRepository.empty('case', 'snap'), provider,
        budget=Budget(max_prompt=5000)).ask('很长的当前要求。' * 120)
    assert result['error'] == 'context_budget'
    assert result['context']['mandatory_overflow'] is True
    assert result['context']['input_bound'] > result['context']['prompt_limit']
    assert result['context']['max_attempt_bound'] == result['context']['input_bound']
    assert not provider.requests
