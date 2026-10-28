"""Run the six named engineering scenarios against isolated test fixtures.

These are four shared pytest nodes, not six live product trials. The network
guard disallows live services and model calls. They never enter business scores.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from hashlib import sha256
from pathlib import Path
from xml.etree import ElementTree

from scripts.verify_offline import sanitized_env

ROOT = Path(__file__).resolve().parents[2]


def run_controls(report_path: Path):
    report_path = Path(report_path)
    if report_path.exists():
        raise FileExistsError(report_path)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    junit = report_path.with_suffix('.junit.xml')
    if junit.exists():
        raise FileExistsError(junit)
    protocol = json.loads(Path(__file__).with_name('controls.json').read_bytes())
    nodes = sorted({s['pytest_node'] for s in protocol['scenarios']})
    paths = sorted({node.split('::')[0] for node in nodes})
    before = {p: sha256((ROOT / p).read_bytes()).hexdigest() for p in paths}
    command = [sys.executable, '-m', 'pytest', *nodes, '-q', '-ra', '-p', 'scripts.offline_guard',
               '--junitxml', str(junit)]
    try:
        process = subprocess.run(command, cwd=ROOT, env=sanitized_env(__import__('os').environ),
                                 stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                 text=True, encoding='utf-8', errors='replace', timeout=300, check=False)
        output, code = process.stdout, process.returncode
    except subprocess.TimeoutExpired:
        output, code = 'engineering_controls_timeout', 1
    after = {p: sha256((ROOT / p).read_bytes()).hexdigest() for p in paths}
    attempts = re.findall(r'^offline_network_attempts=(\d+)$', output, re.MULTILINE)
    outcomes = {}
    if junit.is_file():
        for case in ElementTree.parse(junit).iter('testcase'):
            key = case.get('name', '').split('[')[0]
            passed = not any(case.find(tag) is not None for tag in ('failure', 'error', 'skipped'))
            outcomes.setdefault(key, []).append(passed)
    scenarios = []
    for row in protocol['scenarios']:
        results = outcomes.get(row['pytest_node'].split('::')[-1], [])
        scenarios.append({**row, 'passed': bool(results) and all(results)})
    report = {**protocol, 'scenarios': scenarios, 'distinct_test_nodes': len(nodes),
              'command': ['python', *command[1:]], 'exit_code': code, 'output': output,
              'source_before': before, 'source_after': after, 'source_unchanged': before == after,
              'network_attempts': int(attempts[-1]) if attempts else None,
              'passed': code == 0 and before == after and bool(attempts) and int(attempts[-1]) == 0
                        and all(s['passed'] for s in scenarios)}
    with report_path.open('x', encoding='utf-8') as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report', required=True, type=Path)
    args = parser.parse_args()
    result = run_controls(args.report)
    print(json.dumps({'passed': result['passed'], 'report': str(args.report)}))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
