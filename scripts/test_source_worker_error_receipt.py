#!/usr/bin/env python3
"""Exercise the real source worker CLI receipt boundary without live services."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'pipelines'))
from daily_chain_v2_contract import classify_error
from daily_chain_v2_adapters import _worker_error_text


class WorkerReceiptTests(unittest.TestCase):
    def worker_failure(self, message):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'attempt.json'
            bootstrap = '''import sys, os, json
sys.path.insert(0, sys.argv[1])
import daily_chain_v2_worker as worker
message, receipt = sys.argv[3], sys.argv[2]
class FakeJournal:
    def __init__(self, path): pass
    def validate_claim(self, key, token):
        return {'source_code': 'pricecharting', 'run_id': 'test',
                'payload_json': json.dumps({'workerKind': 'collect'})}
def fail(*args, **kwargs):
    error = RuntimeError(message)
    error.receipt_extra = {'childInterrupted': True, 'status': 'must-not-overwrite'}
    raise error
worker.Journal = FakeJournal
worker.registered_kinds = lambda: ('collect',)
worker.resolve_worker_runner = lambda kind: fail
worker.start_heartbeat = lambda *args: lambda: None
os.environ.pop('CARDZ_V2_RUN_ID', None)
sys.argv = ['worker', '--state-db', receipt + '.sqlite3', '--task-key', 'test',
            '--claim-token', 'isolated-test', '--receipt', receipt, '--kind', 'collect']
sys.exit(worker.main())
'''
            script = Path(directory) / 'probe.py'
            script.write_text(bootstrap)
            proc = subprocess.run([sys.executable, str(script), str(ROOT / 'pipelines'),
                                   str(output), message], capture_output=True, text=True, timeout=20)
            self.assertEqual(proc.returncode, 1, proc.stderr)
            payload = json.loads(output.read_text())
            self.assertEqual(payload['contract'], 'cardz-source-result-v2')
            self.assertEqual(payload['status'], 'failed')
            self.assertTrue(payload['childInterrupted'])
            return classify_error(_worker_error_text(payload, proc.stderr)), payload

    def test_existing_pc_child_does_not_consume_source_failure_budget(self):
        result, payload = self.worker_failure('errorCode=PC_CHILD_ALREADY_RUNNING child still sweeping')
        self.assertEqual(result.error_code, 'PC_CHILD_ALREADY_RUNNING')
        self.assertTrue(result.contention)
        self.assertEqual(payload['errorCode'], result.error_code)

    def test_real_source_failure_consumes_failure_budget(self):
        result, payload = self.worker_failure('provider request failed')
        self.assertEqual(result.error_code, 'SOURCE_FAILED')
        self.assertFalse(result.contention)
        self.assertEqual(payload['errorCode'], 'SOURCE_FAILED')


if __name__ == '__main__':
    unittest.main()
