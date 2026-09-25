#!/usr/bin/env python3
"""The failed harvest stderr survives in its immutable attempt log. No network/DB."""
import contextlib
import io
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'pipelines'))
import daily_chain_v2_stage as stage
import identity_census_stage as census

class EvidenceTests(unittest.TestCase):
    def test_real_failure_preserves_captured_stderr(self):
        result = dict(refreshed=False,error='harvest exit=1',outputTail='SOURCE_TEST_FAILURE exact explanation')
        stream = io.StringIO()
        with patch.object(census,'run_identity_census',return_value=result), contextlib.redirect_stderr(stream):
            with self.assertRaisesRegex(RuntimeError,'INCOMPLETE_CENSUS: harvest exit=1'):
                stage.stage_identity_census(SimpleNamespace(business_date='2026-09-13'))
        evidence = json.loads(stream.getvalue())
        self.assertEqual(evidence['outputTail'],result['outputTail'])
        self.assertEqual(evidence['stage'],'identity-census')

    def test_budget_deferral_stays_precise(self):
        with patch.object(census,'run_identity_census',return_value=dict(refreshed=False,skipReason='tick-budget-too-short')):
            with self.assertRaisesRegex(RuntimeError,'CENSUS_TICK_BUDGET_DEFERRED'):
                stage.stage_identity_census(SimpleNamespace(business_date='2026-09-13'))

if __name__ == '__main__':
    unittest.main()
