"""Adapter preparation cannot substitute fake state for a real precondition."""
import importlib
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[2]


def adapter():
    try:
        return importlib.import_module('evals.agent_interview.http_adapter')
    except ModuleNotFoundError:
        pytest.fail('Agent evaluation HTTP adapter is missing')


def test_unavailable_state_is_blocked_before_http(tmp_path):
    class NoHTTP:
        def request(self, *a, **k):
            pytest.fail('unavailable fixture must not write or send')
    called = []
    target = adapter().HttpAdapter(NoHTTP(), tmp_path, guard=lambda: called.append(1))
    bundle = {'task_id': 'sample', 'setup': {'preconditions': [{
        'kind': 'existing_unknown_dispatch', 'requirement': 'real unknown required',
        'on_unavailable': 'setup_blocked'}]}}
    value = target.run(bundle, 1, tmp_path / 'trial')
    assert value['execution'] == 'blocked'
    assert value['error_code'] == 'setup_precondition_unavailable'
    assert not called


def test_fixture_identity_and_sku_are_business_inputs_not_gold():
    data = json.loads((ROOT / 'evals/agent_interview/dataset.json').read_bytes())
    setup = next(s for s in data['setups'] if s['setup_id'] == 'dev_rag_storage_setup')
    value = adapter().definition_for(setup)
    from backend.app.criteria.models import ChecklistDefinition
    parsed = ChecklistDefinition.model_validate(value)
    assert {i.item_id for i in parsed.items} == {'phone', 'tablet'}
    assert parsed.criteria[0].source.kind == 'user_defined'


def test_logical_state_normalizes_only_equal_frozen_versions():
    before = {'case_id': 'a' * 32, 'snapshot_id': 'b' * 32, 'revision': 4,
              'checklist_revision': 2, 'binding_revision': 1, 'confirmed': True}
    actual = dict(before)
    result = adapter().logical_state('dphone', before, actual, review_count=0)
    assert result['case_id'] == 'dphone' and result['snapshot_version'] == 1
    actual['snapshot_id'] = 'c' * 32
    assert adapter().logical_state('dphone', before, actual, review_count=0)['snapshot_version'] != 1
    actual = dict(before, checklist_revision=3)
    assert adapter().logical_state('dphone', before, actual, review_count=0)['checklist_version'] == 3


def test_review_downloads_must_match_checks_and_inspectable_workbook():
    from backend.app.api.run_export import render_xlsx
    run = {'id': 'r', 'case_id': 'c', 'snapshot_id': 's', 'revision': 4, 'status': 'partial'}
    checks = {'run_id': 'r', 'case_id': 'c', 'snapshot_id': 's', 'revision': 4,
              'run_status': 'partial', 'is_current': True, 'checks': []}
    payload = {'result': checks, 'sources': []}
    binary = render_xlsx(payload)
    adapter().validate_review_artifacts(run, checks, payload, binary)
    with pytest.raises(ValueError, match='evaluation_export_mismatch'):
        adapter().validate_review_artifacts(run, checks, {'result': checks | {'revision': 9}}, binary)
    with pytest.raises(ValueError, match='evaluation_xlsx_invalid'):
        adapter().validate_review_artifacts(run, checks, payload, b'not-a-workbook')


def test_progress_identity_uses_actual_minimal_repository_contract():
    run = {'id': 'r', 'status': 'partial'}
    checks = {'checklist_version_id': 'v', 'item_binding_digest': 'digest'}
    progress = {'run_id': 'r', 'run_status': 'partial', 'workflow': 'review',
                **checks, 'planned_batches': 2, 'validated_batches': 1}
    adapter().validate_progress(progress, run, checks)
    with pytest.raises(ValueError, match='evaluation_result_identity'):
        adapter().validate_progress(progress | {'item_binding_digest': 'other'}, run, checks)


def complete_http_script(tmp_path, monkeypatch, fault, *, review=False):
    from hashlib import sha256

    from evals.agent_interview import corpus
    from evals.agent_interview.run import business_bundle

    data = json.loads((ROOT / 'evals/agent_interview/dataset.json').read_bytes())
    task = next(t for t in data['tasks'] if t['task_id'] == 'dev_rag_storage')
    bundle = business_bundle(data, task)
    bundle['sources'] = bundle['sources'][:1]
    setup = bundle['setup']
    setup['bindings'] = setup['bindings'][:1]
    source = bundle['sources'][0]
    content = b'preverified-pdf-fixture'
    file = tmp_path / source['path']
    file.parent.mkdir(parents=True)
    file.write_bytes(content)
    digest = sha256(content).hexdigest()
    monkeypatch.setattr(corpus, 'validate_corpus', lambda root: SimpleNamespace(files=[SimpleNamespace(
        path=source['path'], role='input', sha256=digest)]))
    ids = {k: char * 32 for k, char in [('project', 'a'), ('case', 'b'), ('snapshot', 'c'),
                                       ('doc', 'd'), ('conversation', 'e'), ('run', 'f'), ('attempt', '1'), ('message', '2')]}
    envelope = {'case_id': ids['case'], 'snapshot_id': ids['snapshot'], 'revision': 1}
    definition = adapter().definition_for(setup)
    from backend.app.criteria.models import ChecklistDefinition
    normalized = ChecklistDefinition.model_validate(definition).model_dump(mode='json')
    confirmed = False
    evidence_calls = 0
    requests = []

    review_check = dict(envelope, run_id=ids['run'], run_status='completed', is_current=True,
        checklist_version_id='4' * 32, checks=[{'check_result_id': 'check', 'supplier': 'a',
        'item_id': 'phone', 'criterion_id': 'storage', 'field_key': 'storage', 'criterion_name': '存储',
        'item_name': '教学手机', 'reason_code': 'threshold_met', 'rule_version': 'dynamic-rules-v16',
        'processing_status': 'complete', 'business_status': '满足', 'is_formal': True,
        'evidence_refs': ['3' * 32], 'reason_evidence_refs': [], 'fact_ids': []}])
    export = {'result': review_check, 'sources': [{'evidence_id': '3' * 32, 'document_id': ids['doc'],
        'document_name': Path(source['path']).name, 'page': 1, 'sheet': None, 'cell': None,
        'source_text': '晨星设备 / 教学手机 EDU-P6'}]}
    if fault == 'review_source':
        export['sources'][0].update(source_text='伪造原文', page=999, document_name='forged.pdf')

    def handle(req):
        nonlocal confirmed, evidence_calls
        requests.append(req)
        path, method = req.url.path, req.method
        status = 200
        if path == '/api/v1/projects':
            status, value = 201, {'id': ids['project']}
        elif path == '/api/v1/cases':
            status, value = 201, {'id': ids['case'], 'project_id': ids['project'], 'revision': 0}
        elif path.endswith('/documents') and method == 'POST':
            status, value = 201, dict(envelope, document_id=ids['doc'], source_sha256=digest)
        elif path.endswith('/documents'):
            value = dict(envelope, documents=[{'id': ids['doc'], 'original_name': Path(source['path']).name,
                'source_sha256': digest, 'role': source['role'], 'supplier_handle': source['supplier_id'],
                'parse_status': 'complete'}])
        elif path.endswith('/evidence'):
            evidence_calls += 1
            rows = [] if evidence_calls > 1 else [{'id': '3' * 32, 'ordinal': 0,
                'document_id': ids['doc'], 'parser_version': 'parser-test', 'page_idx': 0,
                'source_text': '晨星设备 / 教学手机 EDU-P6', 'sheet': None, 'cell': None}]
            value = dict(envelope, document_id=ids['doc'], parser_version='parser-test', evidence=rows, next_ordinal=0)
        elif path.endswith('/checklist/confirm'):
            confirmed = True
            value = dict(envelope, checklist_revision=2, confirmed_version={'id': '4' * 32, 'definition': normalized})
        elif path.endswith('/checklist'):
            value = dict(envelope, checklist_revision=2 if confirmed else 1,
                         confirmed_version={'id': '4' * 32} if confirmed else None)
        elif path.endswith('/item-bindings'):
            value = dict(envelope, binding_revision=1, state='confirmed', current_version={'id': '5' * 32})
        elif path.endswith('/indexes'):
            status = 202 if method == 'POST' else 200
            value = {'scheduled': 1, 'worker_configured': True} if method == 'POST' else dict(
                envelope, documents=[{'status': 'ready'}])
        elif path.endswith('/conversations'):
            status, value = 201, {'id': ids['conversation'], 'case_id': ids['case']}
        elif path.endswith('/messages'):
            body = json.loads(req.content)
            assert body['operation'] == 'assistant'
            assert body['content'] == task['prompt']
            assert 'expected' not in body and 'reference_answer' not in req.content.decode()
            status, value = 202, {'run_id': ids['run'], 'attempt_id': ids['attempt'], 'message_id': ids['message']}
        elif path.endswith('/checks'):
            value = review_check
        elif path.endswith('/facts'):
            value = dict(envelope, run_id=ids['run'], is_current=True, facts=[])
        elif path.endswith('/progress'):
            value = {'run_id': ids['run'], 'run_status': 'completed',
                     'checklist_version_id': 'wrong' if fault == 'review_progress' else '4' * 32}
        elif path.endswith('/export'):
            value = export
            if req.url.params['format'] == 'xlsx':
                from backend.app.api.run_export import render_xlsx
                return httpx.Response(200, content=b'bad' if fault == 'review_xlsx' else render_xlsx(export),
                    headers={'content-type': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'})
        elif path.endswith('/events'):
            value = {'events': [], 'next_seq': 0}
        elif path.endswith('/assistant-result'):
            value = dict(envelope, run_id=ids['run'], run_status='completed', is_current=True,
                         result={'kind': 'review' if review else 'chat', 'answer': '先检查资料', 'citations': []})
            if fault == 'identity':
                value['run_id'] = '0' * 32
            elif fault == 'revision':
                value['revision'] = 999
            elif fault == 'status':
                value['run_status'] = 'failed'
            elif fault == 'stale':
                value['is_current'] = False
        elif path == '/api/v1/agent-runs/' + ids['run']:
            value = dict(envelope, id=ids['run'], active_attempt_id=ids['attempt'],
                         conversation_id=ids['conversation'], status='completed' if fault != 'unknown' else 'running', outcome_unknown=fault == 'unknown',
                         answer_text='先检查资料', workflow='assistant', checklist_version_id='4' * 32 if review else None, usage={'model_calls': 1,
                         'actual_input': 71, 'actual_output': 8, 'unknown_usage_calls': 1 if fault == 'unknown' else 0})
        else:
            pytest.fail(f'unexpected request {method} {path}')
        return httpx.Response(status, json=value)

    guarded = []
    def guard():
        guarded.append(1)
        if fault == 'runtime_guard':
            raise RuntimeError('runtime_identity_changed')
        if fault == 'guard':
            raise ValueError('warning_round_requires_consent')
    with httpx.Client(base_url='http://127.0.0.1:8000', transport=httpx.MockTransport(handle)) as client:
        target = adapter().HttpAdapter(client, tmp_path, guard=guard)
        result = target.run(bundle, 1, tmp_path / 'trial')
    if review:
        return result
    if fault in {'guard', 'runtime_guard'}:
        assert result['error_code'] == ('runtime_identity_changed' if fault == 'runtime_guard' else 'warning_round_requires_consent')
        assert result['execution'] == 'blocked' and result['halt_execution'] is True
        assert not any(r.url.path.endswith('/messages') for r in requests)
        assert result['usage']['requests'] == 0
        return
    if fault == 'unknown':
        assert result['execution'] == 'failed' and result['halt_execution'] is True
        assert result['error_code'] == 'evaluation_server_unknown'
        assert result['usage'] == {'requests': 1, 'input_tokens': 71, 'output_tokens': 8, 'unknown_requests': 1}
        assert result['usage_complete'] is False and len(result['submitted_runs']) == 1
        from evals.agent_interview.observation import normalize
        normalize(data, result, source_hashes={source['source_id']: digest})
        return
    if fault:
        assert result['execution'] == 'failed'
        assert result['error_code'] == 'evaluation_result_identity'
        return
    assert result['execution'] == 'executed', result.get('error_code')
    assert result['track'] == 'assistant_routing'
    assert result['usage'] == {'requests': 1, 'input_tokens': 71, 'output_tokens': 8, 'unknown_requests': 0}
    assert guarded == [1]
    assert result['state']['confirmed'] is True
    assert result['state']['snapshot_version'] == 1
    assert (tmp_path / 'trial/requests.inflight.json').exists()
    assert json.loads((tmp_path / 'trial/requests.inflight.json').read_bytes())['journal_state'] == 'finished'
    return result


@pytest.mark.parametrize('field', ['state', 'usage', 'events', 'business_checks'])
def test_normalization_rebuilds_authoritative_derivatives(tmp_path, monkeypatch, field):
    from evals.agent_interview.observation import normalize
    raw = complete_http_script(tmp_path, monkeypatch, None)
    data = json.loads((ROOT / 'evals/agent_interview/dataset.json').read_bytes())
    hashes = {s: d['sha256'] for s, d in raw['documents'].items()}
    normalize(data, raw, source_hashes=hashes)
    if field == 'state':
        raw['state']['checklist_version'] = 999
    elif field == 'usage':
        raw['usage']['requests'] = 777
    elif field == 'events':
        raw['events'].append({'summary': {'tool': 'invented_tool'}})
    else:
        raw['business_checks'] = [{'business_status': 'satisfied'}]
    with pytest.raises(ValueError, match='observation_derived_identity'):
        normalize(data, raw, source_hashes=hashes)


def test_assistant_container_review_is_counted_and_unknown_keeps_known_usage():
    run = {'id': 'r', 'workflow': 'assistant', 'checklist_version_id': 'confirmed',
           'outcome_unknown': True, 'usage': {'model_calls': 2, 'actual_input': 31,
                                            'actual_output': 9, 'unknown_usage_calls': 1}}
    assert adapter().review_run_count([run]) == 1
    usage, complete = adapter().observed_usage([run], active_run='r')
    assert usage == {'requests': 2, 'input_tokens': 31, 'output_tokens': 9, 'unknown_requests': 1}
    assert complete is False


@pytest.mark.parametrize('fault', [None, 'guard', 'identity', 'revision', 'status', 'stale', 'unknown', 'runtime_guard'])
def test_complete_http_script_records_real_receipts_without_gold(tmp_path, monkeypatch, fault):
    complete_http_script(tmp_path, monkeypatch, fault)


@pytest.mark.parametrize('fault', [None, 'review_xlsx', 'review_source', 'review_progress'])
def test_review_export_outcome_retains_checks_and_original_binding(tmp_path, monkeypatch, fault):
    from evals.agent_interview.observation import normalize
    raw = complete_http_script(tmp_path, monkeypatch, fault, review=True)
    data = json.loads((ROOT / 'evals/agent_interview/dataset.json').read_bytes())
    hashes = {s: d['sha256'] for s, d in raw['documents'].items()}
    if fault:
        assert raw['execution'] == 'failed'
        assert raw['error_code'] == {'review_xlsx': 'evaluation_xlsx_invalid',
            'review_source': 'evaluation_export_source_identity', 'review_progress': 'evaluation_result_identity'}[fault]
    else:
        assert raw['execution'] == 'executed' and raw['state']['review_count'] == 1
    normalized = normalize(data, raw, source_hashes=hashes)
    assert len(normalized['business_checks']) == 1


def test_saved_xlsx_is_bound_to_download_journal(tmp_path, monkeypatch):
    from evals.agent_interview.observation import normalize
    raw = complete_http_script(tmp_path, monkeypatch, None, review=True)
    data = json.loads((ROOT / 'evals/agent_interview/dataset.json').read_bytes())
    hashes = {s: d['sha256'] for s, d in raw['documents'].items()}
    normalize(data, raw, source_hashes=hashes)
    (tmp_path / 'trial/report.xlsx').write_bytes(b'replaced after download')
    with pytest.raises(ValueError, match='observation_download_identity'):
        normalize(data, raw, source_hashes=hashes)


def test_trial_cannot_relabel_existing_journal(tmp_path, monkeypatch):
    from evals.agent_interview.observation import normalize
    raw = complete_http_script(tmp_path, monkeypatch, None)
    raw['trial'] = 2
    data = json.loads((ROOT / 'evals/agent_interview/dataset.json').read_bytes())
    with pytest.raises(ValueError, match='observation_raw_identity'):
        normalize(data, raw, source_hashes={s: d['sha256'] for s, d in raw['documents'].items()})


def test_deleting_download_hash_cannot_skip_xlsx_validation(tmp_path, monkeypatch):
    from evals.agent_interview.observation import normalize
    raw = complete_http_script(tmp_path, monkeypatch, None, review=True)
    del raw['xlsx_sha256']
    (tmp_path / 'trial/report.xlsx').write_bytes(b'changed artifact without metadata')
    data = json.loads((ROOT / 'evals/agent_interview/dataset.json').read_bytes())
    with pytest.raises(ValueError, match='observation_download_identity'):
        normalize(data, raw, source_hashes={s: d['sha256'] for s, d in raw['documents'].items()})


@pytest.mark.parametrize('duplicate_exact', [False, True])
def test_actual_long_cell_binding_prefers_unique_exact_identity_cells(duplicate_exact):
    from backend.app.parsing import parse_xlsx
    from evals.agent_interview.corpus import load_dataset
    from evals.agent_interview.http_adapter import binding_identity
    from evals.dynamic.run import _in_scope

    data = load_dataset()
    task = next(t for t in data.tasks if t.task_id == 'measure_rag_table_scope')
    setup = next(s for s in data.setups if s.setup_id == task.setup_id)
    item = next(i for i in setup.items if i.sku == 'MEET-B')
    binding = next(b for b in setup.bindings if b.source_id == 'mmixed_table' and b.item_id == item.item_id)
    source = next(s for s in data.sources if s.source_id == binding.source_id)
    parsed = parse_xlsx(ROOT / 'evals/agent_interview/fixtures' / source.path, document_id=source.source_id)
    rows = [{'id': b.evidence_id, 'source_text': b.text, 'sheet': b.sheet, 'cell': b.cell,
             'page_idx': b.page_idx} for b in parsed.blocks]
    selected = [r for r in rows if _in_scope(r, {'sheet': binding.sheet, 'cell_range': binding.cells[0]})]
    assert any(len(r['source_text']) > 1500 and item.sku in r['source_text'] for r in selected)
    if duplicate_exact:
        exact = next(r for r in selected if r['source_text'] == item.name)
        selected.append(dict(exact, id='another-exact-identity'))
        with pytest.raises(ValueError, match='evaluation_binding_identity_ambiguous'):
            binding_identity(selected, item.model_dump(mode='json'))
    else:
        anchor, sku = binding_identity(selected, item.model_dump(mode='json'))
        assert anchor['quote'] == item.name and sku['quote'] == item.sku


@pytest.mark.parametrize('named_header', [True, False])
def test_actual_chair_name_uses_original_named_column_and_keeps_ambiguity_guard(named_header):
    from backend.app.parsing import parse_xlsx
    from evals.agent_interview.corpus import load_dataset
    from evals.agent_interview.http_adapter import binding_identity
    from evals.dynamic.run import _in_scope

    data = load_dataset()
    source = next(s for s in data.sources if s.source_id == 'mmixed_table')
    parsed = parse_xlsx(ROOT / 'evals/agent_interview/fixtures' / source.path, document_id=source.source_id)
    rows = [{'id': b.evidence_id, 'source_text': b.text, 'sheet': b.sheet, 'cell': b.cell,
             'page_idx': b.page_idx} for b in parsed.blocks]
    selected = [r for r in rows if _in_scope(r, {'sheet': '报价对照', 'cell_range': 'A2:G2'})]
    assert sum(r['source_text'] == '会议椅' for r in selected) == 2
    headers = rows if named_header else [r for r in rows if r['cell'] != 'G1']
    item = {'name': '会议椅', 'sku': 'MEET-C'}
    if named_header:
        anchor, sku = binding_identity(selected, item, all_evidence=headers)
        assert anchor['evidence_refs'] == [next(r['id'] for r in rows if r['cell'] == 'G2')]
        assert sku['quote'] == 'MEET-C'
    else:
        with pytest.raises(ValueError, match='evaluation_binding_identity_ambiguous'):
            binding_identity(selected, item, all_evidence=headers)


@pytest.mark.parametrize('duplicate', [False, True])
def test_pdf_identity_uses_unique_joint_name_and_sku_original_block(duplicate):
    from evals.agent_interview.http_adapter import binding_identity

    # Actual local parser strings from published dphone_a, not expected answers.
    texts = ['晨星设备有限公司 / 教学手机 EDU-P6',
             '教学手机数量 12 台；不含税单价 980 元/台。',
             'EDU-P6 参数另见附件。']
    rows = [{'id': str(i), 'source_text': t, 'page_idx': 0, 'sheet': None, 'cell': None}
            for i, t in enumerate(texts)]
    if duplicate:
        rows.append(dict(rows[0], id='duplicate'))
        with pytest.raises(ValueError, match='evaluation_binding_identity_ambiguous'):
            binding_identity(rows, {'name': '教学手机', 'sku': 'EDU-P6'})
    else:
        anchor, sku = binding_identity(rows, {'name': '教学手机', 'sku': 'EDU-P6'})
        assert anchor == sku == {'evidence_refs': ['0'], 'quote': texts[0]}
