"""Citation normalization reads original identity/location, never expected answers."""
import importlib
import json
from hashlib import sha256

import pytest


def module():
    try:
        return importlib.import_module('evals.agent_interview.observation')
    except ModuleNotFoundError:
        pytest.fail('Agent observation normalizer is missing')


def fixture():
    data = {'anchors': [{'anchor_id': 'a', 'source_id': 's', 'text': '运行内存8GB',
                         'page': 1, 'line': 2, 'sheet': None, 'cell': None}]}
    raw = {'origin': 'reference_fixture', 'task_id': 'sample', 'trial': 1,
           'answer': '运行内存8GB', 'state': {'confirmed': True},
           'documents': {'s': {'id': 'doc', 'sha256': 'a' * 64, 'parser_version': 'p1'}},
           'evidence': {'s': [{'id': 'ref', 'document_id': 'doc', 'page_idx': 0,
                               'parser_version': 'p1', 'sheet': None, 'cell': None, 'source_text': '运行内存8GB'}]},
           'assistant_result': {'result': {'answer': '运行内存8GB', 'citations': [{'evidence_id': 'ref', 'quote': '运行内存8GB'}]}},
           'events': [{'summary': {'tool': 'search_evidence'}}]}
    return data, raw


def test_citation_needs_matching_source_location_and_quote():
    data, raw = fixture()
    result = module().normalize(data, raw)
    assert result['cited_anchor_ids'] == ['a'] and result['verified_anchor_ids'] == ['a']
    assert result['claims'] == []  # Gold facts are never copied to an observation.
    raw['evidence']['s'][0]['page_idx'] = 1
    assert module().normalize(data, raw)['cited_anchor_ids'] == []
    data, raw = fixture()
    raw['assistant_result']['result']['citations'][0]['quote'] = '运行内存16GB'
    assert module().normalize(data, raw)['cited_anchor_ids'] == []


def test_annotation_binds_to_exact_observation_and_resolvable_evidence():
    data, raw = fixture()
    normalized = module().normalize(data, raw)
    digest = sha256(json.dumps(normalized, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    annotation = {'observation_sha256': digest, 'reviewer': 'named-reviewer', 'method': 'manual',
                  'kind': 'answer', 'outcome': 'answered', 'claims': [],
                  'assessments': {'cite_scope': {'verdict': 'pass', 'evidence': ['/answer']}},
                  'semantic_review': {'verdict': 'pass', 'evidence': ['/answer']}}
    result = module().annotate(normalized, annotation)
    assert result['semantic_review']['reviewer'] == 'named-reviewer'
    annotation['assessments']['cite_scope']['evidence'] = ['/does-not-exist']
    with pytest.raises(ValueError, match='assessment_pointer_invalid'):
        module().annotate(normalized, annotation)
    annotation['observation_sha256'] = '0' * 64
    with pytest.raises(ValueError, match='annotation_observation_mismatch'):
        module().annotate(normalized, annotation)


def test_annotation_cannot_change_state_usage_or_manufacture_source():
    data, raw = fixture()
    normalized = module().normalize(data, raw)
    annotation = {'observation_sha256': module().digest(normalized), 'reviewer': 'named', 'method': 'manual',
                  'kind': 'answer', 'outcome': 'answered', 'claims': [], 'assessments': {},
                  'semantic_review': {'verdict': 'pass', 'evidence': ['/answer']}, 'state': {'confirmed': False}}
    with pytest.raises(ValueError, match='annotation_fields_invalid'):
        module().annotate(normalized, annotation)


def test_normalization_rejects_changed_sha_parser_and_raw_artifact(tmp_path):
    data, raw = fixture()
    raw['origin'] = 'live_http'
    responses = [{'response': {'source_sha256': 'a' * 64}}, {'response': {
        'document_id': 'doc', 'parser_version': 'p1', 'evidence': raw['evidence']['s']}},
        {'response': raw['assistant_result']}]
    path = tmp_path / 'requests.json'
    path.write_text(json.dumps({'raw': responses}), encoding='utf-8')
    raw.update(raw_artifact=str(path), raw_sha256=sha256(path.read_bytes()).hexdigest())
    hashes = {'s': 'a' * 64}
    assert module().normalize(data, raw, source_hashes=hashes)['verified_anchor_ids'] == ['a']
    raw['documents']['s']['sha256'] = 'b' * 64
    with pytest.raises(ValueError, match='observation_source_identity'):
        module().normalize(data, raw, source_hashes=hashes)
    raw['documents']['s']['sha256'] = 'a' * 64
    raw['evidence']['s'][0]['parser_version'] = 'changed'
    with pytest.raises(ValueError, match='observation_source_identity'):
        module().normalize(data, raw, source_hashes=hashes)
    raw['evidence']['s'][0]['parser_version'] = 'p1'
    path.write_text('{}', encoding='utf-8')
    with pytest.raises(ValueError, match='observation_raw_identity'):
        module().normalize(data, raw, source_hashes=hashes)


def test_live_normalization_cannot_invent_answer_outside_http_receipts(tmp_path):
    data, raw = fixture()
    raw['origin'] = 'live_http'
    payload = raw['assistant_result']
    payload['result']['answer'] = raw['answer']
    responses = [{'response': {'source_sha256': 'a' * 64}}, {'response': {
        'document_id': 'doc', 'parser_version': 'p1', 'evidence': raw['evidence']['s']}},
        {'response': payload}]
    path = tmp_path / 'journal.json'
    path.write_text(json.dumps({'raw': responses}), encoding='utf-8')
    raw.update(raw_artifact=str(path), raw_sha256=sha256(path.read_bytes()).hexdigest())
    assert module().normalize(data, raw, source_hashes={'s': 'a' * 64})['answer'] == '运行内存8GB'
    raw['answer'] = '运行内存2MB'
    with pytest.raises(ValueError, match='observation_http_artifact_identity'):
        module().normalize(data, raw, source_hashes={'s': 'a' * 64})
