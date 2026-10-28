"""Owned loopback HTTP execution. No DB fabrication or gold-to-model path.

The injected client owns legal authentication. A supplied guard reconciles the
cross-task usage ledger before every model-bearing message, including setup.
"""
from __future__ import annotations

import re
import time
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

from evals.dynamic.run import TERMINAL, _identifier, _in_scope, _selector

from .authority import (
    business_checks,
    logical_state,
    observed_usage,
    review_run_count,
    validate_export_sources,
)
from .export_validation import validate_review_artifacts
from .journal import AuditClient, unresolved


def definition_for(setup):
    return {'items': [{'item_id': i['item_id'], 'name': i['name'], 'specification': i['sku']}
                      for i in setup['items']], 'criteria': setup['confirmed_rules']}


def _compact(value):
    return re.sub(r'\s+', '', value)


def binding_identity(selected, item, *, all_evidence=None):
    """Select original identity blocks; author labels are not literal source quotes."""
    names = [r for r in selected if _compact(item['name']) == _compact(r['source_text'])]
    if len(names) > 1 and all_evidence is not None:
        coordinates = [re.fullmatch(r'([A-Z]+)([1-9][0-9]*)', r.get('cell') or '') for r in names]
        # Only resolve distinct columns in one actual row using its named header.
        # Cross-row, duplicate-coordinate and PDF ambiguity remain refusals.
        if (all(coordinates) and len({r.get('sheet') for r in names}) == 1
                and len({m[2] for m in coordinates}) == 1
                and len({m[1] for m in coordinates}) == len(names)):
            named = []
            for row, coordinate in zip(names, coordinates, strict=True):
                headers = [h for h in all_evidence if h.get('sheet') == row.get('sheet')
                           and h.get('cell') == coordinate[1] + '1']
                if (coordinate[2] != '1' and len(headers) == 1
                        and _compact(headers[0]['source_text']) == '品目名称'):
                    named.append(row)
            if len(named) == 1:
                names = named
    if not names:
        names = [r for r in selected if _compact(item['name']) in _compact(r['source_text'])]
    skus = [r for r in selected if item['sku'] == r['source_text'].strip()]
    if not skus:
        skus = [r for r in selected if item['sku'] in r['source_text']]
    if len(names) != 1 or len(skus) != 1:
        joint = [r for r in names if r in skus]
        if (len(joint) == 1 and joint[0].get('page_idx') is not None
                and joint[0].get('sheet') is None and joint[0].get('cell') is None):
            names = skus = joint
    if len(names) != 1 or len(skus) != 1:
        raise ValueError('evaluation_binding_identity_ambiguous')
    def context(block):
        return {'evidence_refs': [block['id']], 'quote': block['source_text']}
    return context(names[0]), context(skus[0])


def validate_identity(value, run, *, status=False):
    expected = {'run_id': run['id'], **{k: run[k] for k in ('case_id', 'snapshot_id', 'revision')}}
    if status:
        expected['run_status'] = run['status']
    if any(value.get(k) != v for k, v in expected.items()) or value.get('is_current') is not True:
        raise ValueError('evaluation_result_identity')


def validate_progress(value, run, checks):
    expected = {'run_id': run['id'], 'run_status': run['status'],
                'checklist_version_id': checks.get('checklist_version_id')}
    expected.update({k: checks[k] for k in ('item_binding_digest', 'item_binding_version_id') if k in checks})
    if any(value.get(k) != v for k, v in expected.items()):
        raise ValueError('evaluation_result_identity')


class HttpAdapter:
    origin = 'live_http'
    def __init__(self, client, corpus_root: Path, *, guard, timeout_seconds=600, runtime_metadata=None):
        if guard is None or not callable(guard):
            raise ValueError('usage_guard_required')
        if not 0 < timeout_seconds <= 1200:
            raise ValueError('evaluation_deadline_invalid')
        self.client, self.root, self.guard = client, Path(corpus_root), guard
        self.timeout_seconds = timeout_seconds
        self.runtime_metadata = runtime_metadata

    def frozen_runtime(self):
        required = {'application_commit', 'evaluation_commit', 'evaluation_source_sha256',
                    'dependencies_sha256', 'configured_model', 'model_configuration_sha256',
                    'parser_identity', 'vector_configuration_sha256'}
        value = self.runtime_metadata
        if not isinstance(value, dict) or set(value) != required:
            raise ValueError('runtime_identity_required')
        for key in required:
            size = 40 if key.endswith('_commit') else 64 if key.endswith('_sha256') else None
            if (not isinstance(value[key], str) or not value[key].strip()
                    or size and not re.fullmatch('[a-f0-9]{' + str(size) + '}', value[key])
                    or size is None and len(value[key]) > 200):
                raise ValueError('runtime_identity_invalid')
        if value['evaluation_source_sha256'] != evaluation_source_digest():
            raise ValueError('evaluation_source_changed')
        from .corpus import validate_corpus
        manifest = validate_corpus(self.root)
        return dict(value, corpus_manifest_sha256=sha256((self.root / 'manifest.json').read_bytes()).hexdigest(),
                    dataset_sha256=manifest.dataset_sha256, upstream_model_identity_verified=False,
                    parser_note='startup identity; each actual evidence parser_version also retained')

    def run(self, bundle, trial, directory):
        started = time.monotonic()
        started_at = datetime.now(UTC).isoformat()
        task_id, setup = bundle['task_id'], bundle['setup']
        unsupported = [p for p in setup.get('preconditions', [])
                       if p['kind'] != 'history_replay_with_runtime_ids']
        unsupported_history = any(h['role'] != 'user' or h.get('snapshot_version', 1) != 1
                                  for h in setup.get('history', []))
        if unsupported or unsupported_history:
            return {'task_id': task_id, 'trial': trial, 'execution': 'blocked',
                    'origin': self.origin,
                    'timing': {'started_at': started_at, 'total_seconds': time.monotonic() - started},
                    'error_code': 'setup_precondition_unavailable',
                    'missing_preconditions': unsupported or ['real_assistant_history_required'],
                    'usage': {'requests': 0, 'input_tokens': 0, 'output_tokens': 0, 'unknown_requests': 0}}
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=False)
        audit = AuditClient(self.client, directory / 'requests.inflight.json', {
            'task_id': task_id, 'trial': trial, 'automatic_retries': False})
        deadline = time.monotonic() + self.timeout_seconds
        observation = {'task_id': task_id, 'trial': trial, 'execution': 'failed',
                       'origin': 'live_http', 'claims': [], 'assessments': {},
                       'semantic_review': None, 'violations': [], 'tool_names': [],
                       'cited_anchor_ids': [], 'verified_anchor_ids': [], 'business_checks': [],
                       'raw_artifact': str(directory / 'requests.inflight.json')}
        observed_runs = {}
        active_run = None
        guard_refused = False
        prepared_at = None

        def request(method, path, *, status=200, **kwargs):
            if time.monotonic() >= deadline:
                raise ValueError('evaluation_deadline')
            if method in {'PUT', 'POST'}:
                kwargs.setdefault('headers', {'Idempotency-Key': uuid4().hex})
            response = audit.request(method, path, timeout=min(20, max(1, deadline - time.monotonic())), **kwargs)
            if response.status_code != status:
                raise ValueError('evaluation_http_' + str(response.status_code))
            if 'application/json' not in response.headers.get('content-type', ''):
                return response.content
            value = response.json()
            if not isinstance(value, dict):
                raise TypeError('evaluation_api_shape')
            return value

        def acknowledge(predicate):
            if not predicate:
                raise ValueError('evaluation_receipt_mismatch')
            audit.acknowledge()

        def pause():
            if time.monotonic() >= deadline:
                raise ValueError('evaluation_deadline')
            time.sleep(0.5)

        def conversation(case):
            value = request('POST', f'/api/v1/cases/{case}/conversations', status=201)
            identity = _identifier(value['id'])
            acknowledge(value['case_id'] == case)
            return identity

        def submit(conv, content, operation, case, snapshot, revision):
            nonlocal active_run, guard_refused
            try:
                self.guard()
            except (ValueError, RuntimeError) as error:
                guard_refused = True
                code = str(error)
                raise ValueError(code if re.fullmatch(r'[a-z][a-z0-9_]{0,100}', code)
                                 else 'evaluation_guard_refused') from None
            receipt = request('POST', f'/api/v1/conversations/{conv}/messages', status=202,
                              json={'content': content, 'operation': operation, 'mode': 'thorough',
                                    'expected_revision': revision})
            run_id, attempt_id = _identifier(receipt['run_id']), _identifier(receipt['attempt_id'])
            _identifier(receipt['message_id'])
            acknowledge(True)
            active_run = run_id
            path = f'/api/v1/agent-runs/{run_id}'
            while True:
                run = request('GET', path)
                if (run['id'] != run_id or run['active_attempt_id'] != attempt_id
                        or run['case_id'] != case or run['snapshot_id'] != snapshot
                        or run['revision'] != revision or run['conversation_id'] != conv):
                    raise ValueError('evaluation_run_identity')
                observed_runs[run_id] = run
                if run.get('outcome_unknown'):
                    raise ValueError('evaluation_server_unknown')
                if run['status'] in TERMINAL:
                    break
                pause()
            active_run = None
            return run, receipt

        try:
            from .corpus import validate_corpus
            manifest = validate_corpus(self.root)
            file_index = {f.path: f for f in manifest.files if f.role == 'input'}
            originals = {}
            for source in bundle['sources']:
                record = file_index[source['path']]
                content = (self.root / source['path']).read_bytes()
                if sha256(content).hexdigest() != record.sha256:
                    raise ValueError('evaluation_input_changed')
                originals[source['source_id']] = content
            project = request('POST', '/api/v1/projects', status=201,
                              json={'name': f'Agent评测 {task_id} trial{trial}'})
            project_id = _identifier(project['id'])
            acknowledge(True)
            case = request('POST', '/api/v1/cases', status=201, json={'project_id': project_id})
            case_id = _identifier(case['id'])
            acknowledge(case['project_id'] == project_id and case['revision'] == 0)
            observation.update(project_id=project_id, case_id=case_id)
            revision, documents = 0, {}
            suppliers = {s['supplier_id']: s['name'] for s in setup['suppliers']}
            for source in bundle['sources']:
                source_id = source['source_id']
                content = originals[source_id]
                data = {'role': source['role'], 'expected_revision': str(revision)}
                if source['supplier_id'] is not None:
                    data.update(supplier_handle=source['supplier_id'], supplier_name=suppliers[source['supplier_id']])
                mime = 'application/pdf' if source['format'] == 'pdf' else (
                    'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
                receipt = request('POST', f'/api/v1/cases/{case_id}/documents', status=201,
                                  data=data, files={'file': (Path(source['path']).name, content, mime)})
                snapshot = _identifier(receipt['snapshot_id'])
                acknowledge(receipt['case_id'] == case_id and receipt['revision'] == revision + 1
                            and receipt['source_sha256'] == sha256(content).hexdigest())
                revision += 1
                documents[source_id] = dict(source, sha256=receipt['source_sha256'])
            prefix = f'/api/v1/cases/{case_id}/snapshots/{snapshot}'
            while True:
                listed = request('GET', prefix + '/documents')
                if (listed['case_id'], listed['snapshot_id'], listed['revision']) != (case_id, snapshot, revision):
                    raise ValueError('evaluation_snapshot_identity')
                rows = listed['documents']
                if len(rows) != len(documents):
                    raise ValueError('evaluation_document_identity')
                for source_id, doc in documents.items():
                    matches = [r for r in rows if r['original_name'] == Path(doc['path']).name
                               and r['source_sha256'] == doc['sha256']
                               and r['supplier_handle'] == doc['supplier_id'] and r['role'] == doc['role']]
                    if len(matches) != 1:
                        raise ValueError('evaluation_document_identity')
                    doc['id'] = _identifier(matches[0]['id'])
                if any(r['parse_status'] in {'failed', 'partial'} for r in rows):
                    raise ValueError('evaluation_parse_incomplete')
                if all(r['parse_status'] == 'complete' for r in rows):
                    break
                pause()
            evidence = {}
            for source_id, doc in documents.items():
                blocks, after = [], -1
                while True:
                    value = request('GET', prefix + f'/documents/{doc["id"]}/evidence',
                                    params={'after_ordinal': after, 'limit': 100})
                    if (value['document_id'] != doc['id'] or value['snapshot_id'] != snapshot
                            or value['case_id'] != case_id or not value['parser_version']):
                        raise ValueError('evaluation_evidence_identity')
                    doc['parser_version'] = value['parser_version']
                    batch = value['evidence']
                    if not batch:
                        break
                    if any(r['ordinal'] <= after or r['document_id'] != doc['id']
                           or r['parser_version'] != doc['parser_version'] for r in batch):
                        raise ValueError('evaluation_evidence_identity')
                    if value['next_ordinal'] != batch[-1]['ordinal']:
                        raise ValueError('evaluation_evidence_pagination')
                    blocks.extend(batch)
                    after = batch[-1]['ordinal']
                if len({r['id'] for r in blocks}) != len(blocks):
                    raise ValueError('evaluation_evidence_pagination')
                evidence[source_id] = blocks
            confirmed = setup['state'].get('confirmed') is True
            if confirmed:
                definition = definition_for(setup)
                draft = request('PUT', prefix + '/checklist', json={**definition,
                    'expected_revision': revision, 'expected_checklist_revision': 0,
                    'reason': '预定评测业务前置：用户明确要求的检查清单'})
                acknowledge(draft['case_id'] == case_id and draft['snapshot_id'] == snapshot)
                check = request('POST', prefix + '/checklist/confirm', json={
                    'criterion_ids': [c['criterion_id'] for c in definition['criteria']],
                    'confirmed': True, 'expected_revision': revision,
                    'expected_checklist_revision': draft['checklist_revision'],
                    'reason': '显式确认预定评测业务清单；不含预期审查结论'})
                version = _identifier(check['confirmed_version']['id'])
                from backend.app.criteria.models import ChecklistDefinition
                normalized = ChecklistDefinition.model_validate(definition).model_dump(mode='json')
                acknowledge(check['case_id'] == case_id and check['snapshot_id'] == snapshot
                            and ChecklistDefinition.model_validate(check['confirmed_version']['definition']).model_dump(mode='json') == normalized)
                entries = []
                for binding in setup['bindings']:
                    doc = documents[binding['source_id']]
                    if binding['pages']:
                        if binding['pages'] != list(range(min(binding['pages']), max(binding['pages']) + 1)):
                            raise ValueError('evaluation_discontinuous_binding')
                        scope = {'page_start': min(binding['pages']), 'page_end': max(binding['pages'])}
                    elif binding['sheet'] and len(binding['cells']) == 1:
                        scope = {'sheet': binding['sheet'], 'cell_range': binding['cells'][0]}
                    else:
                        raise ValueError('evaluation_binding_scope')
                    selected = [r for r in evidence[binding['source_id']] if _in_scope(r, scope)]
                    item = next(i for i in setup['items'] if i['item_id'] == binding['item_id'])
                    anchor, sku_anchor = binding_identity(
                        selected, item, all_evidence=evidence[binding['source_id']])
                    entries.append({'document_id': doc['id'], 'source_sha256': doc['sha256'],
                                    'parser_version': doc['parser_version'], 'item_id': item['item_id'],
                                    'selector': _selector(scope), 'anchor': anchor,
                                    'identifier_class': 'sku', 'supplier_sku': item['sku'],
                                    'sku_anchor': sku_anchor, 'single_item_confirmed': False})
                bound = request('PUT', prefix + '/item-bindings', json={
                    'entries': entries, 'checklist_version_id': version, 'expected_revision': revision,
                    'expected_checklist_revision': check['checklist_revision'], 'expected_binding_revision': 0,
                    'confirmed': True, 'reason': '明确确认预定采购品目与文件页/行范围'})
                acknowledge(bound['case_id'] == case_id and bound['snapshot_id'] == snapshot
                            and bound['state'] == 'confirmed' and bool(bound['current_version']))
                idx = request('POST', prefix + '/indexes', status=202,
                              json={'expected_revision': revision, 'retry_failed': False})
                acknowledge(type(idx.get('scheduled')) is int and idx['scheduled'] >= 0
                            and type(idx.get('worker_configured')) is bool)
                while True:
                    idx = request('GET', prefix + '/indexes')
                    if (idx['case_id'], idx['snapshot_id'], idx['revision']) != (case_id, snapshot, revision):
                        raise ValueError('evaluation_index_identity')
                    statuses = {r['status'] for r in idx['documents']}
                    if len(idx['documents']) == len(documents) and statuses <= {'ready', 'failed'}:
                        break
                    pause()
                observation['indexes'] = idx
            else:
                check, bound = request('GET', prefix + '/checklist'), request('GET', prefix + '/item-bindings')
                if check.get('confirmed_version') is not None:
                    raise ValueError('evaluation_unconfirmed_fixture_invalid')
            before = {'case_id': case_id, 'snapshot_id': snapshot, 'revision': revision,
                      'checklist_revision': check['checklist_revision'],
                      'binding_revision': bound['binding_revision'], 'confirmed': confirmed}
            observation.update(baseline=before, documents=documents, evidence=evidence,
                               track='formal_review' if bundle.get('evaluation_target') == 'review_business_result'
                               else 'assistant_routing')
            prepared_at = time.monotonic()
            history = []
            for row in setup.get('history', []):
                conv = conversation(case_id)
                history_run, receipt = submit(conv, row['content'], 'assistant', case_id, snapshot, revision)
                history.append({'logical_id': row['message_id'], 'logical_conversation': row['conversation'],
                                'run': history_run, 'receipt': receipt,
                                'messages': request('GET', f'/api/v1/conversations/{conv}/messages')})
            conv = conversation(case_id)
            for turn in bundle['turns']:
                submit(conv, turn['content'], 'assistant', case_id, snapshot, revision)
            operation = 'review' if observation['track'] == 'formal_review' else 'assistant'
            run, receipt = submit(conv, bundle['prompt'], operation, case_id, snapshot, revision)
            path = f'/api/v1/agent-runs/{run["id"]}'
            observation.update(run=run, receipt=receipt, history=history, answer=run.get('answer_text') or '')
            events, seq = [], 0
            while True:
                page = request('GET', path + '/events', params={'after_seq': seq})
                if not page['events']:
                    break
                events.extend(page['events'])
                if page['next_seq'] <= seq:
                    raise ValueError('evaluation_event_pagination')
                seq = page['next_seq']
            observation['events'] = events
            if operation == 'assistant':
                response = request('GET', path + '/assistant-result')
                if (any(response.get(k) != v for k, v in {
                    'run_id': run['id'], 'case_id': case_id, 'snapshot_id': snapshot,
                    'revision': revision, 'run_status': run['status']}.items())
                        or response.get('is_current') is not True):
                    raise ValueError('evaluation_result_identity')
                observation['assistant_result'] = response
                result = response['result']
                observation['kind'] = {'evidence': 'answer', 'review': 'review',
                                       'proposal': 'draft', 'changes': 'draft', 'chat': 'chat'}.get(result['kind'])
                if result.get('evidence_answer'):
                    observation['answer'] = result['evidence_answer']['text']
            if run.get('workflow') == 'review' or operation == 'review' or observation.get('kind') == 'review':
                for artifact in ('facts', 'checks', 'progress'):
                    observation[artifact] = request('GET', path + '/' + artifact)
                    if artifact != 'progress':
                        validate_identity(observation[artifact], run, status=artifact == 'checks')
                    if artifact == 'checks':
                        observation['business_checks'] = business_checks(observation['checks'])
                validate_progress(observation['progress'], run, observation['checks'])
                observation['business_checks'] = business_checks(observation['checks'])
                export = request('GET', path + '/export?format=json')
                observation['export'] = export
                binary = request('GET', path + '/export?format=xlsx')
                validate_export_sources(export, documents, evidence)
                validate_review_artifacts(run, observation['checks'], export, binary)
                with (directory / 'report.xlsx').open('xb') as stream:
                    stream.write(binary)
                observation['export'] = export
                observation['xlsx_sha256'] = sha256(binary).hexdigest()
                observation['business_checks'] = business_checks(observation['checks'])
                observation['kind'] = 'review'
            after_check, after_bound = request('GET', prefix + '/checklist'), request('GET', prefix + '/item-bindings')
            after_docs = request('GET', f'/api/v1/cases/{case_id}/documents')
            actual = {'case_id': after_docs['case_id'], 'snapshot_id': after_docs['snapshot_id'],
                      'revision': after_docs['revision'], 'checklist_revision': after_check['checklist_revision'],
                      'binding_revision': after_bound['binding_revision'],
                      'confirmed': after_check.get('confirmed_version') is not None}
            observation['authority_after'] = {'checklist': after_check, 'bindings': after_bound, 'documents': after_docs}
            observation['state'] = logical_state(setup['group_id'], before, actual,
                review_count=review_run_count(list(observed_runs.values())))
            observation['execution'] = 'executed'
            observation['outcome'] = 'unclassified_requires_review'
            audit.finish()
        except (ValueError, KeyError, OSError, TypeError) as error:
            code = str(error)
            observation['error_code'] = code if re.fullmatch(r'[a-z][a-z0-9_]{0,100}', code) else 'evaluation_adapter_error'
            observation['halt_execution'] = bool(guard_refused or active_run or unresolved(audit.record))
            if guard_refused:
                observation['execution'] = 'blocked'
            observation['active_run_id'] = active_run
            observation['outcome_unknown'] = unresolved(audit.record)
            if not observation['halt_execution']:
                audit.finish()
        observation['submitted_runs'] = list(observed_runs.values())
        observation['usage'], observation['usage_complete'] = observed_usage(
            observation['submitted_runs'], active_run=active_run)
        raw = (directory / 'requests.inflight.json').read_bytes()
        observation['raw_sha256'] = sha256(raw).hexdigest()
        observation['timing'] = {'started_at': started_at, 'finished_at': datetime.now(UTC).isoformat(),
            'total_seconds': time.monotonic() - started,
            'preparation_seconds': None if prepared_at is None else prepared_at - started,
            'task_seconds': None if prepared_at is None else time.monotonic() - prepared_at,
            'includes_failures_and_queueing': True}
        return observation


def evaluation_source_digest():
    root = Path(__file__).parent
    values = [(p.name, sha256(p.read_bytes()).hexdigest()) for p in sorted(root.iterdir()) if p.is_file()]
    from .observation import digest
    return digest(values)
