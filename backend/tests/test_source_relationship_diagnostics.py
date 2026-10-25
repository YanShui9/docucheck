"""Finite source-shape diagnostics preserve validation and repair boundaries."""

import json
from copy import deepcopy
from types import SimpleNamespace

import pytest

from backend.app.agent.diagnostics import safe_trace_events
from backend.app.agent.dynamic import DynamicAgent
from backend.app.agent.policy import Budget
from backend.app.criteria.validation import source_span
from backend.app.providers.base import ProviderError
from backend.tests.test_dynamic_anchor_repair import AnchorProvider, mixed_sample
from backend.tests.test_dynamic_repair_feedback import candidate_feedback


@pytest.mark.parametrize('changes,detail', [
    ({'document_id': 'OTHER_SECRET'}, 'mixed_document'),
    ({'sheet': 'OTHER_SECRET'}, 'mixed_sheet'),
    ({'role': 'spec'}, 'mixed_role'),
    ({'supplier': 'OTHER_SECRET'}, 'mixed_supplier'),
    ({'model': 'OTHER_SECRET'}, 'mixed_model'),
    ({'cell': None}, 'non_tabular'),
    ({'sheet': None}, 'non_tabular'),
    ({'cell': 'INVALID'}, 'invalid_cell'),
    ({'cell': 'B3'}, 'different_row'),
    ({'cell': 'C2'}, 'non_adjacent_columns'),
])
def test_relationship_rejection_has_finite_reason_without_source_content(changes, detail):
    first = {'document_id': 'doc', 'sheet': '报价', 'role': 'quote', 'supplier': 'A',
             'model': None, 'cell': 'A2', 'text': 'SOURCE_SECRET'}
    second = first | {'cell': 'B2'} | changes
    repo = SimpleNamespace(read=lambda refs: [SimpleNamespace(**first), SimpleNamespace(**second)])
    with pytest.raises(ValueError) as error:
        source_span(repo, ['a', 'b'], 'SOURCE_SECRET')
    assert str(error.value) == 'evidence_relationship'
    assert getattr(error.value, 'relationship_detail', None) == detail
    assert 'SECRET' not in str(error.value)


def test_valid_row_is_still_sorted_and_paragraph_keeps_original_text():
    first = {'document_id': 'doc', 'sheet': '报价', 'role': 'quote', 'supplier': 'A',
             'model': None, 'cell': 'A2', 'text': '含税单价'}
    second = first | {'cell': 'B2', 'text': '5800 元/台'}
    blocks = [SimpleNamespace(**second), SimpleNamespace(**first)]
    repo = SimpleNamespace(read=lambda refs: blocks)
    selected, text = source_span(repo, ['b', 'a'], '5800 元/台')
    assert [b.cell for b in selected] == ['A2', 'B2']
    assert text == '含税单价 5800 元/台'
    blocks[:] = [SimpleNamespace(**(first | {'sheet': None, 'cell': None, 'text': '独立段落'}))]
    assert source_span(repo, ['a'], '独立段落')[1] == '独立段落'


def test_anchor_correction_gets_specific_reason_and_keeps_initial_and_final_counts():
    definition, repo, frozen = mixed_sample()
    provider = AnchorProvider()
    result = DynamicAgent(repo, provider, definition, item_bindings=frozen).ask('审查')
    assert result['status'] == 'completed' and len(provider.requests) == 3
    feedback = candidate_feedback(provider.requests[1])
    assert [c.get('relationship_detail') for c in feedback['candidates']] == ['different_row'] * 2
    batch = result['payload']['batch_coverage'][0]
    assert batch.get('initial_relationship_details') == {'different_row': 2}
    assert batch.get('relationship_details') == {}
    trace = safe_trace_events(result['events'])
    tool = next(e['summary'] for e in trace if e['summary'].get('initial_rejection_codes'))
    assert tool.get('initial_relationship_details') == {'different_row': 2}
    assert tool.get('relationship_details') == {}
    assert 'rejected_fact_candidates' not in json.dumps(result)


def test_unrelated_rows_stay_partial_and_do_not_gain_a_repair_attempt():
    definition, repo, frozen = mixed_sample()
    provider = AnchorProvider(bad='business_row')
    result = DynamicAgent(repo, provider, definition, item_bindings=frozen).ask('审查')
    assert result['status'] == 'partial' and len(provider.requests) == 2
    batch = result['payload']['batch_coverage'][0]
    assert batch.get('initial_relationship_details') == {'different_row': 2}
    assert batch.get('relationship_details') == {'different_row': 2}
    assert not any(c['is_formal'] for c in result['payload']['checks'] if c['item_id'] == 'apple')


def test_relationship_trace_only_keeps_allowlisted_integer_counts_without_mutating_input():
    raw = {'kind': 'tool', 'tool': 'review_checklist', 'status': 'rejected',
           'initial_relationship_details': {'mixed_document': 1, 'SECRET': 2},
           'relationship_details': {'different_row': 2, 'mixed_sheet': True,
                                    'mixed_model': -1, 'non_tabular': 2**40,
                                    'invalid_cell': 'SECRET'},
           'candidate': {'quote': 'SECRET'}, 'evidence_refs': ['SECRET']}
    before = deepcopy(raw)
    summary = safe_trace_events([raw])[0]['summary']
    assert summary.get('initial_relationship_details') == {'mixed_document': 1}
    assert summary.get('relationship_details') == {'different_row': 2}
    assert 'SECRET' not in json.dumps(summary) and raw == before


def test_cross_document_candidates_stay_rejected_without_a_new_retry():
    from backend.tests.test_material_agent import MaterialProvider
    from backend.tests.test_money_consistency import prices

    built, body = prices(other_item='sample', other_pdf=False)
    other = next(b for b in built[1].blocks if b.document_id == 'second' and b.cell == 'A6')
    body['evidence_refs'].append(other.evidence_id)
    body['quote'] += ' ' + other.text
    provider = MaterialProvider([body])
    result = DynamicAgent(built[1], provider, built[0], item_bindings=built[2]).ask('审查')
    assert result['status'] == 'partial' and len(provider.requests) == 1
    assert result['payload']['facts'] == []
    batch = result['payload']['batch_coverage'][0]
    assert batch['initial_relationship_details'] == batch['relationship_details'] == {'mixed_document': 1}


def test_relationship_counts_accumulate_across_request_parts(monkeypatch):
    from backend.app.agent import dynamic

    def split_criteria(data, fits):
        return [{**data, 'criteria': [criterion]} for criterion in data['criteria']]

    class SplitProvider(AnchorProvider):
        def extract(self, messages, schema, **options):
            reply = super().extract(messages, schema, **options)
            allowed = {c['criterion_id'] for c in json.loads(messages[-1].content)['criteria']}
            args = reply.tool_calls[0]['args']
            args['facts'] = [f for f in args['facts'] if f['criterion_id'] in allowed]
            return reply

    monkeypatch.setattr(dynamic, 'repack_review_request', split_criteria)
    definition, repo, frozen = mixed_sample()
    provider = SplitProvider(bad='business_row')
    result = DynamicAgent(repo, provider, definition, item_bindings=frozen).ask('审查')
    assert result['status'] == 'partial' and len(provider.requests) == 4
    batch = result['payload']['batch_coverage'][0]
    assert batch['request_parts'] == 2 and batch['completed_parts'] == 0
    assert batch['initial_relationship_details'] == batch['relationship_details'] == {'different_row': 2}


@pytest.mark.parametrize('unknown,max_calls,error', [
    (False, 2, 'model_budget'), (True, 4, 'connection_error'),
])
def test_interrupted_repair_preserves_observed_reasons_in_trace(unknown, max_calls, error):
    class InterruptedRepair(AnchorProvider):
        def extract(self, messages, schema, **options):
            if unknown and len(self.requests) == 1:
                self.requests.append(deepcopy(messages))
                raise ProviderError('connection_error', outcome_unknown=True)
            return super().extract(messages, schema, **options)

    definition, repo, frozen = mixed_sample()
    provider = InterruptedRepair()
    cached = []
    result = DynamicAgent(repo, provider, definition, item_bindings=frozen,
                          budget=Budget(max_calls=max_calls),
                          cache_save=lambda *args: cached.append(args)).ask('审查')
    assert result['status'] == 'partial' and result['error'] == error
    assert len(provider.requests) == (2 if unknown else 1)
    assert result['usage']['unknown_usage_calls'] == int(unknown)
    assert not cached and not any(c['is_formal'] for c in result['payload']['checks'])
    batch = result['payload']['batch_coverage'][0]
    assert batch['initial_relationship_details'] == {'different_row': 2}
    assert 'relationship_details' not in batch
    observed = [e['summary'] for e in safe_trace_events(result['events'])
                if e['summary'].get('initial_relationship_details')]
    assert len(observed) == 1
    assert observed[0]['tool'] == 'review_checklist' and observed[0]['status'] == 'failed'
    assert observed[0]['error'] == error
    assert observed[0]['initial_relationship_details'] == {'different_row': 2}
    assert 'relationship_details' not in observed[0]
