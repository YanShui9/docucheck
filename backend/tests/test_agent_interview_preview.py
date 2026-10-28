"""Preview has no authentication, network, mutation, or gold-to-target boundary."""
import importlib
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def module():
    try:
        return importlib.import_module('evals.agent_interview.run')
    except ModuleNotFoundError:
        pytest.fail('Agent evaluation runner is missing')


def test_preview_has_fixed_split_and_no_network(monkeypatch):
    import socket
    monkeypatch.setattr(socket, 'create_connection', lambda *a, **k: pytest.fail('preview used network'))
    result = module().preview(split='measurement')
    assert result['send'] is False
    assert len(result['trials']) == 30
    assert result['model_requests_sent'] == 0
    assert result['gold_sent_to_target'] is False
    assert result['independent_holdout'] is False
    assert len(result['dataset_sha256']) == 64
    assert len(module().preview(split='development')['trials']) == 10


def test_preselected_repeats_add_twelve_not_new_tasks():
    value = module().preview(split='measurement', repeats=True)
    assert len(value['trials']) == 42
    assert sum(t['trial'] > 1 for t in value['trials']) == 12
    with pytest.raises(ValueError, match='repeats_require_measurement'):
        module().preview(split='development', repeats=True)


def test_target_business_bundle_contains_no_expected_or_gold():
    data = json.loads((ROOT / 'evals/agent_interview/dataset.json').read_bytes())
    task = next(t for t in data['tasks'] if t['task_id'] == 'dev_rag_storage')
    value = module().business_bundle(data, task)
    assert value['prompt'] == task['prompt']
    assert 'expected' not in value and 'anchors' not in value
    assert 'reference_answer' not in json.dumps(value)
    assert all(t['role'] == 'user' for t in value['turns'])
    setup = next(s for s in data['setups'] if s['setup_id'] == task['setup_id'])
    setup['context'].update(memory_target_message_id='correct_message', future_gold_leak='secret')
    safe = module().business_bundle(data, task)
    assert 'correct_message' not in json.dumps(safe) and 'secret' not in json.dumps(safe)


def test_send_needs_explicit_authorized_adapter_and_unique_output(tmp_path):
    with pytest.raises(ValueError, match='send_requires_adapter'):
        module().execute(tmp_path / 'run', split='development', send=True)
    result = module().execute(tmp_path / 'preview', split='development')
    assert result['send'] is False
    assert not (tmp_path / 'preview').exists()


def test_halt_preserves_live_origin_and_generates_report(tmp_path):
    class Halt:
        calls = 0
        def run(self, bundle, trial, directory):
            self.calls += 1
            return {'task_id': bundle['task_id'], 'trial': trial, 'origin': 'live_http',
                    'execution': 'blocked', 'halt_execution': True, 'usage': None}
    target = Halt()
    report = module().execute(tmp_path / 'halt', split='development', send=True, adapter=target)
    assert target.calls == 1
    assert report['development']['blocked'] == 1
    assert report['development']['not_executed'] == 9
    assert report['observation_origin'] == 'live_http'
    assert (tmp_path / 'halt/report.json').exists()


def test_cli_send_is_explicit_and_loads_only_named_owned_factory(tmp_path, monkeypatch, capsys):
    import sys
    from types import ModuleType
    fixture = ModuleType('agent_test_owned_factory')
    calls = []
    marker = object()
    def create():
        calls.append('factory')
        return marker
    fixture.create = create
    monkeypatch.setitem(sys.modules, fixture.__name__, fixture)
    def execute(output, **kwargs):
        assert output == tmp_path / 'run' and kwargs['send'] is True
        assert kwargs['adapter'] is marker
        calls.append('execute')
        return {'sent': True}
    monkeypatch.setattr(module(), 'execute', execute)
    monkeypatch.setattr(sys, 'argv', ['run', '--send', '--output', str(tmp_path / 'run'),
                                    '--adapter-factory', 'agent_test_owned_factory:create'])
    module().main()
    assert calls == ['factory', 'execute']
    assert json.loads(capsys.readouterr().out)['sent'] is True


def test_http_execution_requires_frozen_runtime_identity_before_output(tmp_path):
    from evals.agent_interview.http_adapter import HttpAdapter
    target = HttpAdapter(None, tmp_path / 'corpus', guard=lambda: None)
    with pytest.raises(ValueError, match='runtime_identity_required'):
        module().execute(tmp_path / 'run', split='development', send=True, adapter=target)
    assert not (tmp_path / 'run').exists()
