"""Audit only business API requests, with durable intent and no implicit resend.

The caller validates successful mutation receipts before acknowledging them.
Auth is owned by the injected client and never copied into the journal.
"""
from __future__ import annotations

import argparse
import json
from hashlib import sha256
from pathlib import Path
from urllib.parse import unquote, urlsplit

import httpx

from evals.dynamic.run import AttemptJournal


def unresolved(record):
    return any(r['method'] in {'POST', 'PUT'} and (
        r['state'] == 'sending' or r.get('http_status', 0) >= 500
        or r['state'] == 'received' and 200 <= r.get('http_status', 0) < 300
        and not r.get('receipt_validated')) for r in record['raw'])


def inspect_journal(path):
    """Read only. A known receipt is evidence, never permission to resend a write."""
    record = json.loads(Path(path).read_bytes())
    return {'journal_sha256': sha256(Path(path).read_bytes()).hexdigest(),
            'needs_reconciliation': unresolved(record), 'automatic_resend_allowed': False,
            'requests': [{k: r[k] for k in ('method', 'path', 'state', 'http_status',
                                          'idempotency_key', 'receipt_validated') if k in r}
                         for r in record['raw']],
            'known_run_receipts': [r['response'] for r in record['raw']
                                   if isinstance(r.get('response'), dict) and 'run_id' in r['response']],
            'next_action': 'read_existing_server_state_and_keep_original_journal_no_automatic_replay'}


class AuditClient:
    def __init__(self, client, path: Path, metadata: dict):
        self.client = client
        self.record = dict(metadata) | {'raw': [], 'outcome_unknown': False}
        self.journal = AttemptJournal(path, self.record)

    def _save(self):
        self.record['outcome_unknown'] = unresolved(self.record)
        self.journal.update(self.record)

    def finish(self):
        if unresolved(self.record):
            raise ValueError('unacknowledged_mutation')
        self.record['outcome_unknown'] = False
        self.journal.update(self.record, finished=True)

    def acknowledge(self):
        """Acknowledge only the latest received successful mutation, after validation."""
        rows = [r for r in self.record['raw'] if r['method'] in {'POST', 'PUT'}]
        if not rows or rows[-1]['state'] != 'received' or not 200 <= rows[-1]['http_status'] < 300:
            raise ValueError('receipt_not_received')
        rows[-1]['receipt_validated'] = True
        self._save()

    def request(self, method: str, path: str, **kwargs):
        method = method.upper()
        parsed = urlsplit(path)
        decoded = unquote(parsed.path)
        if (method not in {'GET', 'POST', 'PUT'} or not path.startswith('/api/v1/')
                or parsed.scheme or parsed.netloc or parsed.fragment
                or '\\' in decoded or any(p in {'.', '..'} for p in decoded.split('/'))):
            raise ValueError('evaluation_path_invalid')
        mutation = method in {'POST', 'PUT'}
        if mutation and unresolved(self.record):
            raise ValueError('unacknowledged_mutation')
        row = {'method': method, 'path': path, 'state': 'sending'}
        if 'headers' in kwargs:
            key = kwargs['headers'].get('Idempotency-Key')
            if key:
                row['idempotency_key'] = key
        if 'params' in kwargs:
            row['params'] = kwargs['params']
        if 'json' in kwargs:
            row['json'] = kwargs['json']
        if 'data' in kwargs:
            row['data'] = kwargs['data']
        if 'files' in kwargs:
            row['files'] = {key: {'name': val[0], 'sha256': sha256(val[1]).hexdigest(),
                                  'bytes': len(val[1])} for key, val in kwargs['files'].items()}
        self.record['raw'].append(row)
        # This must succeed before a request can be sent.
        self._save()
        try:
            response = self.client.request(method, path, **kwargs)
        except httpx.HTTPError:
            # A mutation remains 'sending': its delivery cannot be inferred.
            row['transport_error'] = True
            if not mutation:
                row['state'] = 'transport_failed'
            self._save()
            raise ValueError('evaluation_transport_unknown' if mutation else
                             'evaluation_transport_failed') from None
        row.update(state='received', http_status=response.status_code)
        content = response.content
        row.update(bytes=len(content), sha256=sha256(content).hexdigest())
        if len(content) > 20 * 1024 * 1024:
            self._save()
            raise ValueError('evaluation_response_limit')
        if 'json' in response.headers.get('content-type', '').lower():
            if len(content) > 8 * 1024 * 1024:
                self._save()
                raise ValueError('evaluation_response_limit')
            try:
                row['response'] = response.json()
            except ValueError:
                self._save()
                raise ValueError('evaluation_response_invalid_json') from None
        self._save()
        return response


def main():
    parser = argparse.ArgumentParser(description='Inspect a saved journal without HTTP or replay')
    parser.add_argument('journal', type=Path)
    args = parser.parse_args()
    print(json.dumps(inspect_journal(args.journal), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
