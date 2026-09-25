#!/usr/bin/env python3
"""Stage subprocess receipts preserve retry identity. No DB/network/browser."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'pipelines'))
from daily_chain_v2_contract import classify_error


class ReceiptTests(unittest.TestCase):
    def stage_failure(self, message):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'attempt.json'
            # Replace only the workload. The real CLI parser, exception handler,
            # atomic receipt writer and subprocess boundary all execute.
            bootstrap = '''import sys
sys.path.insert(0, sys.argv[1])
import daily_chain_v2_stage as stage
message = sys.argv[3]
def fail(args):
    raise RuntimeError(message)
stage.stage_identity_census = fail
sys.argv = ['stage', '--output', sys.argv[2], 'identity-census', '--business-date', '2026-09-13']
sys.exit(stage.main())
'''
            script = Path(directory) / 'stage_probe.py'
            script.write_text(bootstrap)
            proc = subprocess.run([sys.executable, str(script), str(ROOT/'pipelines'), str(output), message],
                                  capture_output=True, text=True, timeout=20)
            self.assertEqual(proc.returncode, 1, proc.stderr)
            payload = json.loads(output.read_text())
            return classify_error(f"RuntimeError:errorCode={payload['errorCode']} {payload['error']}"), payload

    def test_census_budget_refunds_after_real_subprocess(self):
        result, payload = self.stage_failure('errorCode=CENSUS_TICK_BUDGET_DEFERRED: tick-budget-too-short')
        self.assertEqual(result.error_code, 'CENSUS_TICK_BUDGET_DEFERRED')
        self.assertTrue(result.contention)
        self.assertEqual(payload['errorCode'], result.error_code)

    def test_existing_pc_child_refunds_after_real_subprocess(self):
        result, payload = self.stage_failure('errorCode=PC_CHILD_ALREADY_RUNNING child still sweeping')
        self.assertEqual(result.error_code, 'PC_CHILD_ALREADY_RUNNING')
        self.assertTrue(result.contention)
        self.assertEqual(payload['errorCode'], result.error_code)

    def test_real_failure_still_consumes_failure_budget(self):
        result, payload = self.stage_failure('INCOMPLETE_CENSUS: harvest exit=1')
        self.assertEqual(result.error_code, 'SOURCE_FAILED')
        self.assertFalse(result.contention)
        self.assertEqual(payload['errorCode'], 'SOURCE_FAILED')


if __name__ == '__main__':
    unittest.main()
