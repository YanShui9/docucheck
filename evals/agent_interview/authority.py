"""Rebuild measured authority from exact retained API requests, never annotations."""
from __future__ import annotations

from pathlib import Path


def review_run_count(runs):
    return sum(bool(r.get('checklist_version_id')) or r.get('workflow') == 'review' for r in runs)


def observed_usage(runs, *, active_run=None):
    if not runs:
        return (None, False) if active_run else ({k: 0 for k in (
            'requests', 'input_tokens', 'output_tokens', 'unknown_requests')}, True)
    result = {}
    for target, source in [('requests', 'model_calls'), ('input_tokens', 'actual_input'),
                           ('output_tokens', 'actual_output'), ('unknown_requests', 'unknown_usage_calls')]:
        values = [(r.get('usage') or {}).get(source) for r in runs]
        known = [v for v in values if type(v) is int and v >= 0]
        result[target] = sum(known) if known else None
    complete = not active_run and all(
        isinstance(r.get('usage'), dict) and all(type(r['usage'].get(k)) is int and r['usage'][k] >= 0
        for k in ('model_calls', 'actual_input', 'actual_output', 'unknown_usage_calls'))
        and not r.get('outcome_unknown') for r in runs) and result['unknown_requests'] == 0
    return result, complete


def business_checks(value):
    states = {'满足': 'satisfied', '不满足': 'not_satisfied', '资料冲突': 'conflict',
              '证据不足': 'insufficient_evidence', '待人工确认': 'manual'}
    return [{'supplier_id': c['supplier'], 'item_id': c['item_id'], 'criterion_id': c['criterion_id'],
             'business_status': states.get(c.get('business_status')),
             **{k: c[k] for k in ('processing_status', 'is_formal', 'evidence_refs', 'reason_evidence_refs')}}
            for c in value.get('checks', [])]


def logical_state(group, before, actual, *, review_count):
    return {'case_id': group if actual['case_id'] == before['case_id'] else actual['case_id'],
            'snapshot_version': 1 if (actual['snapshot_id'], actual['revision']) == (
                before['snapshot_id'], before['revision']) else 'changed',
            'checklist_version': actual['checklist_revision'],
            'bindings_version': actual['binding_revision'], 'confirmed': actual['confirmed'],
            'review_count': review_count, 'export_count': 0}


def verify_derived(dataset, observation, rows):
    """Reject edits before semantic review. Journal is local evidence, not a signature."""
    try:
        def responses(method, path):
            return [r['response'] for r in rows if r.get('method') == method and r.get('path') == path
                    and r.get('state') == 'received' and 200 <= r.get('http_status', 0) < 300
                    and isinstance(r.get('response'), dict)]
        receipts = [(r['path'], r['response']) for r in rows if r.get('method') == 'POST'
                    and r.get('path', '').endswith('/messages') and r.get('http_status') == 202
                    and isinstance(r.get('response'), dict)]
        runs = []
        for path, receipt in receipts:
            values = responses('GET', '/api/v1/agent-runs/' + receipt['run_id'])
            if values:
                run = values[-1]
                if (run['id'] != receipt['run_id'] or run['active_attempt_id'] != receipt['attempt_id']
                        or run['conversation_id'] != path.split('/')[-2]):
                    raise ValueError('run')
                runs.append(run)
        if observation.get('submitted_runs') != runs:
            raise ValueError('runs')
        active = observation.get('active_run_id')
        if active and (not receipts or active != receipts[-1][1]['run_id']):
            raise ValueError('active')
        usage, complete = observed_usage(runs, active_run=active)
        if observation.get('usage') != usage or observation.get('usage_complete') is not complete:
            raise ValueError('usage')
        if 'run' in observation and (not runs or observation['run'] != runs[-1]):
            raise ValueError('run')
        events = []
        if 'run' in observation:
            for page in responses('GET', '/api/v1/agent-runs/' + observation['run']['id'] + '/events'):
                events.extend(page['events'])
        if observation.get('events', []) != events:
            raise ValueError('events')
        projected = business_checks(observation.get('checks', {}))
        actual = [{k: v for k, v in c.items() if k != 'anchor_ids'} for c in observation.get('business_checks', [])]
        if actual != projected:
            raise ValueError('checks')
        if observation.get('execution') == 'executed':
            task = next(t for t in dataset['tasks'] if t['task_id'] == observation['task_id'])
            setup = next(s for s in dataset['setups'] if s['setup_id'] == task['setup_id'])
            before = observation['baseline']
            case = observation['case_id']
            created = responses('POST', '/api/v1/cases')
            if not any(c['id'] == case for c in created) or before['case_id'] != case:
                raise ValueError('case')
            uploads = responses('POST', '/api/v1/cases/' + case + '/documents')
            if not uploads or (before['snapshot_id'], before['revision']) != (
                    uploads[-1]['snapshot_id'], uploads[-1]['revision']):
                raise ValueError('baseline')
            prefix = '/api/v1/cases/' + case + '/snapshots/' + before['snapshot_id']
            for source, doc in observation['documents'].items():
                pages = responses('GET', prefix + '/documents/' + doc['id'] + '/evidence')
                evidence = [block for page in pages for block in page['evidence']]
                if observation['evidence'].get(source) != evidence:
                    raise ValueError('evidence')
            # The before state is the last successful authority response before any model message.
            first = next(i for i, r in enumerate(rows) if r.get('method') == 'POST'
                         and r.get('path', '').endswith('/messages'))
            def prior(suffix):
                return [r['response'] for r in rows[:first] if r.get('path') == prefix + suffix
                        and r.get('state') == 'received' and 200 <= r.get('http_status', 0) < 300][-1]
            check = prior('/checklist/confirm') if before['confirmed'] else prior('/checklist')
            bound = prior('/item-bindings')
            if (before['checklist_revision'] != check['checklist_revision']
                    or before['binding_revision'] != bound['binding_revision']
                    or before['confirmed'] != (check.get('confirmed_version') is not None)):
                raise ValueError('baseline')
            after = {'checklist': responses('GET', prefix + '/checklist')[-1],
                     'bindings': responses('GET', prefix + '/item-bindings')[-1],
                     'documents': responses('GET', '/api/v1/cases/' + case + '/documents')[-1]}
            if observation.get('authority_after') != after:
                raise ValueError('after')
            docs = after['documents']
            physical = {**{k: docs[k] for k in ('case_id', 'snapshot_id', 'revision')},
                        'checklist_revision': after['checklist']['checklist_revision'],
                        'binding_revision': after['bindings']['binding_revision'],
                        'confirmed': after['checklist'].get('confirmed_version') is not None}
            if observation.get('state') != logical_state(setup['group_id'], before, physical,
                                                         review_count=review_run_count(runs)):
                raise ValueError('state')
    except (KeyError, TypeError, IndexError, StopIteration, ValueError):
        raise ValueError('observation_derived_identity') from None


def validate_export_sources(export, documents, evidence):
    """Cross-format agreement is separate from agreement with actual parsed originals."""
    try:
        blocks = {b['id']: (doc, b) for source, doc in documents.items() for b in evidence[source]}
        refs = {ref for c in export['result']['checks']
                for key in ('evidence_refs', 'reason_evidence_refs') for ref in c[key]}
        sources = export['sources']
        if len({s['evidence_id'] for s in sources}) != len(sources) or {s['evidence_id'] for s in sources} != refs:
            raise ValueError('refs')
        def check_source(source):
            doc, block = blocks[source['evidence_id']]
            expected = {'document_id': doc['id'], 'document_name': Path(doc['path']).name,
                        'source_text': block['source_text'],
                        'page': None if block.get('page_idx') is None else block['page_idx'] + 1,
                        'sheet': block.get('sheet'), 'cell': block.get('cell')}
            if any(source.get(k) != v for k, v in expected.items()):
                raise ValueError('source')
        for source in sources:
            check_source(source)
        attribution = export.get('item_attribution') or {}
        for source in attribution.get('anchors', []):
            check_source(source)
        for excluded in attribution.get('exclusions', []):
            for source in excluded['sources']:
                doc, block = blocks[source['evidence_id']]
                if (excluded['document_id'] != doc['id'] or excluded['document_name'] != Path(doc['path']).name
                        or excluded['page'] != block['page_idx'] + 1 or source['source_text'] != block['source_text']):
                    raise ValueError('exclusion')
    except (KeyError, TypeError, ValueError):
        raise ValueError('evaluation_export_source_identity') from None
