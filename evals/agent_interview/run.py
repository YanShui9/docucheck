"""Preview a fixed protocol by default; explicit execution requires an adapter.

Credentials and service startup are deliberately outside this reusable module.
Only business inputs cross the injected adapter boundary; grading stays local.
"""
from __future__ import annotations

import argparse
import importlib
import json
import re
import time
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

DATASET = Path(__file__).with_name('dataset.json')


def _dataset():
    from .contracts import Dataset
    return Dataset.model_validate_json(DATASET.read_bytes()).model_dump(mode='json')


def preview(*, split='measurement', repeats=False):
    if split not in {'development', 'measurement'}:
        raise ValueError('invalid_split')
    if repeats and split != 'measurement':
        raise ValueError('repeats_require_measurement')
    data = _dataset()
    tasks = [t for t in data['tasks'] if t['split'] == split]
    trials = [{'task_id': t['task_id'], 'trial': 1, 'category': t['category'],
               'setup_id': t['setup_id']} for t in tasks]
    if repeats:
        trials += [{'task_id': t['task_id'], 'trial': i, 'category': t['category'],
                    'setup_id': t['setup_id']} for t in tasks if t['repeat_selected'] for i in (2, 3)]
    return {'schema_version': 1, 'dataset_id': data['dataset_id'],
            'dataset_sha256': sha256(DATASET.read_bytes()).hexdigest(),
            'split': split, 'send': False, 'model_requests_sent': 0,
            'synthetic': True, 'author_visible': True, 'independent_holdout': False,
            'gold_sent_to_target': False, 'automatic_retries': False,
            'engineering_controls_in_business_denominator': False,
            'trials': trials, 'maximum_requests': None,
            'request_limit_note': 'runtime per-run limits plus reconciled cross-task warning round',
            'environment': 'not_inspected_in_preview'}


def business_bundle(dataset, task):
    """Allowlisted business setup. Never pass expected answers or scoring anchors."""
    setup = next(s for s in dataset['setups'] if s['setup_id'] == task['setup_id'])
    from .corpus import PUBLIC_CONTEXT_KEYS
    allowed = ('setup_id', 'group_id', 'case_title', 'source_ids', 'suppliers', 'items',
               'confirmed_rules', 'bindings', 'history', 'state', 'context', 'preconditions')
    value = {'task_id': task['task_id'], 'prompt': task['prompt'],
            'turns': [t for t in task['turns'] if t['role'] == 'user'],
            'setup': {k: setup[k] for k in allowed},
            'sources': [s for s in dataset['sources'] if s['source_id'] in setup['source_ids']]}
    value['setup']['context'] = {k: v for k, v in setup['context'].items()
                                if k in PUBLIC_CONTEXT_KEYS}
    value['evaluation_target'] = setup['context']['evaluation_target']
    return value


def _write_new(path, value):
    with path.open('x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write('\n')


def execute(output: Path, *, split='measurement', repeats=False, send=False, adapter=None):
    protocol = preview(split=split, repeats=repeats)
    if not send:
        return protocol
    if adapter is None:
        raise ValueError('send_requires_adapter')
    runtime = (adapter.frozen_runtime() if callable(getattr(adapter, 'frozen_runtime', None))
               else getattr(adapter, 'runtime_metadata', {'status': 'not_recorded'}))
    if runtime.get('dataset_sha256', protocol['dataset_sha256']) != protocol['dataset_sha256']:
        raise ValueError('runtime_dataset_mismatch')
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    data = _dataset()
    tasks = {t['task_id']: t for t in data['tasks']}
    protocol.update(send=True, created_at=datetime.now(UTC).isoformat())
    protocol['runtime'] = runtime
    _write_new(output / 'protocol.json', protocol)
    observations = []
    halted = False
    started = time.monotonic()
    origin = getattr(adapter, 'origin', 'live_http')
    for trial in protocol['trials']:
        task_id, number = trial['task_id'], trial['trial']
        if halted:
            obs = {'task_id': task_id, 'trial': number, 'execution': 'not_executed',
                   'origin': origin,
                   'error_code': 'previous_trial_requires_reconciliation'}
        else:
            # Adapter must guard model dispatch, preserve unknown writes, and stop
            # after a live timeout. Exceptions are intentionally not swallowed.
            obs = adapter.run(business_bundle(data, tasks[task_id]), number,
                              output / f'{task_id}-trial-{number}')
            if obs.get('task_id') != task_id or obs.get('trial') != number:
                raise ValueError('adapter_trial_mismatch')
            halted = bool(obs.get('halt_execution'))
            origin = obs.get('origin', origin)
        _write_new(output / f'{task_id}-trial-{number}.json', obs)
        observations.append(obs)
    from .grading import aggregate
    report = aggregate(data, observations)
    report['dataset_sha256'] = protocol['dataset_sha256']
    report['runtime'] = protocol['runtime']
    report['timing'] = {'total_seconds': time.monotonic() - started,
                        'created_at': protocol['created_at'], 'finished_at': datetime.now(UTC).isoformat()}
    _write_new(output / 'report.json', report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--split', choices=['development', 'measurement'], default='measurement')
    parser.add_argument('--repeats', action='store_true')
    parser.add_argument('--send', action='store_true', help='Explicitly execute real owned HTTP trials')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--adapter-factory', help='Explicit trusted local module:function; never a password argument')
    args = parser.parse_args()
    target = None
    if args.send:
        if (args.output is None or not args.adapter_factory
                or not re.fullmatch(r'[a-zA-Z_][\w.]*:[a-zA-Z_]\w*', args.adapter_factory)):
            parser.error('--send requires --output and --adapter-factory module:function')
        name, function = args.adapter_factory.split(':')
        target = getattr(importlib.import_module(name), function)()
    try:
        value = execute(args.output, split=args.split, repeats=args.repeats, send=args.send, adapter=target)
        print(json.dumps(value, ensure_ascii=False, indent=2))
    finally:
        if target is not None and callable(getattr(target, 'close', None)):
            target.close()


if __name__ == '__main__':
    main()
