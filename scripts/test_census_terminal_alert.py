#!/usr/bin/env python3
"""The required census must not hide a terminal verdict behind the early barrier."""
from datetime import datetime, timezone
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'pipelines'))
from daily_chain_v2 import DailyChainV2, ALWAYS_ALERT_EVENTS

class AlertTests(unittest.TestCase):
    def tick(self, status):
        # Only prerequisite lookups and the notification sink are replaced.
        # The production plan() early barrier executes without workers or I/O.
        tick=object.__new__(DailyChainV2)
        tick.run_id='cardz-v2:2026-09-13'
        census=dict(task_key='test-census',status=status,last_error_code='SOURCE_FAILED',attempts=2,max_attempts=2)
        tick.schema_capabilities=lambda: ()
        tick.stage_row=lambda key: census if key=='identity-census' else {'status':'COMPLETED'}
        tick.stage_complete=lambda key: key!='identity-census'
        tick.journal_event=Mock()
        tick.plan(datetime.now(timezone.utc))
        return tick

    def test_terminal_required_census_alerts(self):
        for state in ('TERMINAL','PARKED'):
            with self.subTest(state=state):
                tick=self.tick(state)
                tick.journal_event.assert_called_once()
                event,key,payload=tick.journal_event.call_args.args
                self.assertIn(event,ALWAYS_ALERT_EVENTS)
                self.assertEqual(payload['taskKey'],'test-census')
                self.assertEqual(payload['state'],state)
                self.assertIn('unpark',payload['nextRetry'])

    def test_pending_or_budget_retry_does_not_page(self):
        for state in ('PENDING','RUNNING','RETRY'):
            self.tick(state).journal_event.assert_not_called()

if __name__=='__main__':
    unittest.main()
