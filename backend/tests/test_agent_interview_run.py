"""Eval requests preserve unknown writes and never persist auth headers."""
import importlib
import json

import httpx
import pytest


def transport():
    try:
        return importlib.import_module('evals.agent_interview.journal').AuditClient
    except ModuleNotFoundError:
        pytest.fail('Agent evaluation audit transport is missing')


def test_mutation_is_durable_before_send_and_requires_acknowledgement(tmp_path):
    path = tmp_path / 'attempt.inflight.json'
    calls = []

    def handle(request):
        snapshot = json.loads(path.read_bytes())
        assert snapshot['raw'][-1]['state'] == 'sending'
        assert snapshot['outcome_unknown']
        assert 'secret-token' not in path.read_text()
        calls.append(request)
        return httpx.Response(201, json={'id': 'a' * 32})

    with httpx.Client(transport=httpx.MockTransport(handle), base_url='http://127.0.0.1:8000',
                      headers={'Authorization': 'Bearer secret-token'}) as client:
        audit = transport()(client, path, {'task_id': 'sample'})
        result = audit.request('POST', '/api/v1/cases', json={'project_id': 'b' * 32})
        assert result.status_code == 201
        with pytest.raises(ValueError, match='unacknowledged_mutation'):
            audit.request('POST', '/api/v1/cases', json={})
        assert len(calls) == 1
        audit.acknowledge()
        assert not json.loads(path.read_bytes())['outcome_unknown']


def test_unknown_response_stops_new_writes_without_resend(tmp_path):
    path = tmp_path / 'attempt.inflight.json'
    calls = []

    def handle(request):
        calls.append(request)
        raise httpx.ReadTimeout('credential-like-secret must not be logged')

    with httpx.Client(transport=httpx.MockTransport(handle), base_url='http://127.0.0.1:8000') as client:
        audit = transport()(client, path, {})
        with pytest.raises(ValueError, match='evaluation_transport_unknown'):
            audit.request('POST', '/api/v1/cases', json={})
        with pytest.raises(ValueError, match='unacknowledged_mutation'):
            audit.request('POST', '/api/v1/cases', json={})
        assert len(calls) == 1
        snapshot = json.loads(path.read_bytes())
        assert snapshot['needs_reconciliation'] and snapshot['outcome_unknown']
        assert 'credential-like-secret' not in path.read_text()
        with pytest.raises(ValueError, match='receipt_not_received'):
            audit.acknowledge()


def test_existing_journal_cannot_be_reused_and_rejected_write_is_not_unknown(tmp_path):
    path = tmp_path / 'attempt.inflight.json'
    with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(422, json={'error': {'code': 'invalid'}})),
                      base_url='http://127.0.0.1:8000') as client:
        audit = transport()(client, path, {})
        audit.request('POST', '/api/v1/cases', json={})
        assert not json.loads(path.read_bytes())['outcome_unknown']
        with pytest.raises(FileExistsError):
            transport()(client, path, {})


@pytest.mark.parametrize('path', ['https://attacker.test/api/v1/cases', '//attacker.test/',
                                   '/health/live', '/api/v1/../private'])
def test_paths_are_bounded_before_any_request(tmp_path, path):
    def forbidden(request):
        pytest.fail('invalid path reached HTTP')
    with httpx.Client(transport=httpx.MockTransport(forbidden), base_url='http://127.0.0.1:8000') as client:
        audit = transport()(client, tmp_path / 'attempt.json', {})
        with pytest.raises(ValueError, match='evaluation_path_invalid'):
            audit.request('GET', path)


def test_get_transport_failure_does_not_claim_mutation_or_leak_headers(tmp_path):
    def handle(request):
        raise httpx.ConnectError('never persist exception text')
    path = tmp_path / 'attempt.json'
    with httpx.Client(transport=httpx.MockTransport(handle), base_url='http://127.0.0.1:8000') as client:
        audit = transport()(client, path, {})
        with pytest.raises(ValueError, match='evaluation_transport_failed'):
            audit.request('GET', '/api/v1/projects', headers={'Authorization': 'secret'})
        assert not json.loads(path.read_bytes())['outcome_unknown']
        assert 'secret' not in path.read_text()


def test_server_error_is_unknown_and_idempotency_key_is_retained(tmp_path):
    path = tmp_path / 'attempt.json'
    with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500, json={'error': 'failed'})),
                      base_url='http://127.0.0.1:8000') as client:
        audit = transport()(client, path, {})
        audit.request('POST', '/api/v1/cases', json={}, headers={'Idempotency-Key': 'stable-key'})
        saved = json.loads(path.read_bytes())
        assert saved['outcome_unknown'] is True
        assert saved['raw'][-1]['idempotency_key'] == 'stable-key'
        with pytest.raises(ValueError, match='unacknowledged_mutation'):
            audit.request('POST', '/api/v1/cases', json={})


def test_finish_requires_no_unknown_mutation(tmp_path):
    path = tmp_path / 'attempt.json'
    with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(201, json={'id': 'a' * 32})),
                      base_url='http://127.0.0.1:8000') as client:
        audit = transport()(client, path, {})
        audit.request('POST', '/api/v1/cases', json={})
        with pytest.raises(ValueError, match='unacknowledged_mutation'):
            audit.finish()
        audit.acknowledge()
        audit.finish()
        assert json.loads(path.read_bytes())['journal_state'] == 'finished'


def test_recovery_inspection_is_read_only_and_does_not_authorize_resend(tmp_path):
    from evals.agent_interview import journal
    path = tmp_path / 'pending.json'
    content = {'raw': [{'method': 'POST', 'path': '/api/v1/conversations/c/messages',
                        'state': 'sending', 'idempotency_key': 'stable'}], 'outcome_unknown': True}
    path.write_text(json.dumps(content), encoding='utf-8')
    before = path.read_bytes()
    result = journal.inspect_journal(path)
    assert result['needs_reconciliation'] is True
    assert result['automatic_resend_allowed'] is False
    assert path.read_bytes() == before
