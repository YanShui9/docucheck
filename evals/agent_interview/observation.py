"""Normalize observed sources and bind review labels to immutable raw output.

Gold anchors are used only on the local scoring side. Answer facts are never
filled from expected facts. A semantic reviewer must label the actual answer.
"""
from __future__ import annotations

import json
import re
from copy import deepcopy
from hashlib import sha256
from pathlib import Path


def digest(value):
    return sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _text(value):
    return re.sub(r'\s+', '', value)


def verify_raw(observation):
    try:
        raw = Path(observation['raw_artifact']).read_bytes()
        if sha256(raw).hexdigest() != observation['raw_sha256']:
            raise ValueError('changed')
        record = json.loads(raw)
        if observation.get('execution') in {'executed', 'failed', 'blocked'} and any(
                record.get(k) != observation.get(k) for k in ('task_id', 'trial')):
            raise ValueError('trial')
        return record['raw']
    except (KeyError, TypeError, ValueError, OSError):
        raise ValueError('observation_raw_identity') from None


def normalize(dataset, observation, *, source_hashes=None):
    if hasattr(dataset, 'model_dump'):
        dataset = dataset.model_dump(mode='json')
    result = deepcopy(observation)
    responses = None
    if result.get('origin') == 'live_http':
        raw_rows = verify_raw(result)
        responses = [r.get('response') for r in raw_rows]
        if not source_hashes:
            raise ValueError('observation_source_manifest_required')
        for key in ('run', 'assistant_result', 'facts', 'checks', 'progress', 'export'):
            if key in result and result[key] not in responses:
                raise ValueError('observation_http_artifact_identity')
        payload = result.get('assistant_result', {}).get('result') or {}
        allowed_answers = [result.get('run', {}).get('answer_text') or '', payload.get('answer'),
                           (payload.get('evidence_answer') or {}).get('text')]
        if result.get('answer', '') not in allowed_answers:
            raise ValueError('observation_http_artifact_identity')
    if result.get('origin') == 'live_http' and result.get('execution') in {'executed', 'failed', 'blocked'}:
        from .authority import verify_derived
        verify_derived(dataset, result, raw_rows)
    if result.get('origin') == 'live_http' and ('xlsx_sha256' in result
            or result.get('execution') == 'executed' and 'checks' in result):
        try:
            binary = (Path(result['raw_artifact']).parent / 'report.xlsx').read_bytes()
            binary_sha = sha256(binary).hexdigest()
            path = '/api/v1/agent-runs/' + result['run']['id'] + '/export?format=xlsx'
            downloads = [r for r in raw_rows if r.get('method') == 'GET' and r.get('path') == path
                         and r.get('state') == 'received' and r.get('http_status') == 200]
            if (not downloads or downloads[-1]['sha256'] != binary_sha
                    or result.get('xlsx_sha256') != binary_sha):
                raise ValueError('binary')
            from .authority import validate_export_sources
            from .export_validation import validate_review_artifacts
            validate_export_sources(result['export'], result['documents'], result['evidence'])
            validate_review_artifacts(result['run'], result['checks'], result['export'], binary)
        except (KeyError, OSError, ValueError):
            raise ValueError('observation_download_identity') from None
    blocks, mapped = {}, {}
    for source, rows in result.get('evidence', {}).items():
        document = result.get('documents', {}).get(source)
        if not document:
            continue
        if responses is not None and (source_hashes.get(source) != document['sha256']
                    or not document.get('parser_version')
                    or not any(isinstance(r, dict) and r.get('source_sha256') == document['sha256']
                               for r in responses)):
            raise ValueError('observation_source_identity')
        for block in rows:
            if responses is not None and (
                block.get('parser_version') != document['parser_version']
                or not any(isinstance(r, dict) and r.get('document_id') == document['id']
                           and r.get('parser_version') == document['parser_version']
                           and block in r.get('evidence', []) for r in responses)):
                raise ValueError('observation_source_identity')
            if block['document_id'] == document['id']:
                blocks[block['id']] = (source, block)
    for anchor in dataset['anchors']:
        for ref, (source, block) in blocks.items():
            if (anchor['source_id'] == source
                    and anchor.get('page') == (block['page_idx'] + 1 if block.get('page_idx') is not None else None)
                    and anchor.get('sheet') == block.get('sheet') and anchor.get('cell') == block.get('cell')
                    and _text(anchor['text']) in _text(block['source_text'])):
                mapped.setdefault(ref, []).append(anchor)
    citations = []
    payload = result.get('assistant_result', {}).get('result') or {}
    for citation in payload.get('citations', []):
        ref, quote = citation.get('evidence_id'), citation.get('quote', '')
        if (ref in blocks and quote and _text(quote) in _text(blocks[ref][1]['source_text'])):
            citations += [a['anchor_id'] for a in mapped.get(ref, []) if _text(a['text']) in _text(quote)]
    for check in result.get('checks', {}).get('checks', []):
        for ref in check.get('evidence_refs', []) + check.get('reason_evidence_refs', []):
            citations += [a['anchor_id'] for a in mapped.get(ref, [])]
    result['cited_anchor_ids'] = sorted(set(citations))
    result['verified_anchor_ids'] = sorted({a['anchor_id'] for anchors in mapped.values() for a in anchors})
    result['anchor_mapping'] = {ref: [a['anchor_id'] for a in rows] for ref, rows in mapped.items()}
    for check in result.get('business_checks', []):
        refs = check.get('evidence_refs', []) + check.get('reason_evidence_refs', [])
        check['anchor_ids'] = sorted({a['anchor_id'] for ref in refs for a in mapped.get(ref, [])})
    result['claims'] = []
    result['assessments'] = {}
    result['semantic_review'] = None
    names = set()
    def visit(value):
        if isinstance(value, dict):
            if isinstance(value.get('tool'), str):
                names.add(value['tool'])
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)
    visit(result.get('events', []))
    result['tool_names'] = sorted(names)
    result['normalization'] = 'original_identity_location_quote_verified_semantic_labels_required'
    result['normalization_source_hashes'] = source_hashes
    return result


def _resolve(value, pointer):
    if not isinstance(pointer, str) or not pointer.startswith('/'):
        raise ValueError('assessment_pointer_invalid')
    try:
        for part in pointer[1:].split('/'):
            key = part.replace('~1', '/').replace('~0', '~')
            value = value[int(key)] if isinstance(value, list) else value[key]
    except (KeyError, IndexError, TypeError, ValueError):
        raise ValueError('assessment_pointer_invalid') from None
    return value


def annotate(observation, annotation):
    """Apply a separate, hash-bound review. Identities, states and usage stay raw.

    Reviewer/method are declarations, not proof of independent human labeling.
    Reports must disclose how labels were created and any calibration performed.
    """
    allowed = {'observation_sha256', 'reviewer', 'method', 'kind', 'outcome',
               'claims', 'assessments', 'semantic_review'}
    if set(annotation) != allowed:
        raise ValueError('annotation_fields_invalid')
    if annotation['observation_sha256'] != digest(observation):
        raise ValueError('annotation_observation_mismatch')
    if (not isinstance(annotation['reviewer'], str) or not annotation['reviewer'].strip()
            or annotation['method'] not in {'manual', 'calibrated_judge', 'authored_fixture', 'agent_review'}):
        raise ValueError('annotation_reviewer_invalid')
    for assessment in [*annotation['assessments'].values(), annotation['semantic_review']]:
        if (set(assessment) != {'verdict', 'evidence'} or not assessment['evidence']
                or assessment['verdict'] not in {'pass', 'fail', 'present', 'absent'}):
            raise ValueError('annotation_assessment_invalid')
        for pointer in assessment['evidence']:
            _resolve(observation, pointer)
    verified = set(observation['verified_anchor_ids'])
    for claim in annotation['claims']:
        if not claim.get('anchor_ids') or not set(claim['anchor_ids']) <= verified:
            raise ValueError('annotation_source_unverified')
    result = deepcopy(observation)
    for key in ('kind', 'outcome', 'claims', 'assessments', 'semantic_review'):
        result[key] = deepcopy(annotation[key])
    result['semantic_review'].update(reviewer=annotation['reviewer'], method=annotation['method'])
    result['annotation_sha256'] = digest(annotation)
    result['reviewed_observation_sha256'] = digest(observation)
    result['review_binding'] = {'observation': deepcopy(observation), 'annotation': deepcopy(annotation)}
    return result


def verify_review_binding(observation):
    """Detect post-review changes; an editable hash is not an independent signature."""
    try:
        binding = observation['review_binding']
        rebuilt = annotate(binding['observation'], binding['annotation'])
        if rebuilt != observation:
            raise ValueError('changed')
        if observation.get('origin') == 'live_http':
            from .run import _dataset
            base = binding['observation']
            rebuilt_base = normalize(_dataset(), base, source_hashes=base.get('normalization_source_hashes'))
            if rebuilt_base != base:
                raise ValueError('changed_normalization')
    except (KeyError, TypeError, ValueError):
        raise ValueError('observation_review_binding_invalid') from None
