#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Prove an identity-completeness tick-budget skip spends no attempt.

2026-09-25 (run cardz-v2:2026-09-26): attempt 1 ran 31 min and failed, the same
tick re-claimed the row with too little budget left, and the stage skipped in
2 s as SKIPPED_INSUFFICIENT_TICK_BUDGET.  That skip was read as SOURCE_FAILED and
spent attempt 2 of 3, so the day got two real fetches, not three.
  - the skip refunds the claim (RETRY, attempts unchanged) and closes claiming
    for this tick, exactly like identity-census's CENSUS_TICK_BUDGET_DEFERRED;
  - a real inventory failure still spends an attempt (SOURCE_FAILED).
No MySQL, no network: the journal is a temp sqlite file and the GemRate run is faked.
"""
from __future__ import annotations

import shutil
import sys
import tempfile
import time
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

import daily_chain_v2_stage as stage_module  # noqa: E402
import gemrate_completeness_daily as completeness_module  # noqa: E402
from daily_chain_v2 import DailyChainV2  # noqa: E402
from daily_chain_v2_journal import Journal  # noqa: E402

DAY = date(2026, 9, 26)
DAY_TEXT = DAY.isoformat()
RUN_ID = f"cardz-v2:{DAY_TEXT}"
WORKSPACE = Path(tempfile.mkdtemp(prefix="cardz-v2-completeness-defer-"))


def new_task(name: str) -> tuple[Journal, str]:
    journal = Journal(WORKSPACE / f"{name}.sqlite3")
    journal.initialise()
    journal.ensure_run(
        business_date=DAY_TEXT,
        source_cutoff_at=f"{DAY_TEXT}T01:15:00+00:00",
        sla_at=f"{DAY_TEXT}T04:00:00+00:00",
        final_at=f"{DAY_TEXT}T08:00:00+00:00",
        run_id=RUN_ID,
    )
    key = journal.add_raw_task(
        run_id=RUN_ID,
        business_date=DAY_TEXT,
        phase="identity",
        source_code="system",
        capability="identity-completeness",
        required_class="extra",
        concurrency_group="host:gemrate",
        max_attempts=3,
        input_revision="rev-1",
        shard="all",
        payload={"kind": "stage", "stageName": "identity-completeness"},
    )
    return journal, key


def run_once(journal: Journal, key: str, fake_run: Any) -> DailyChainV2:
    tick = DailyChainV2(
        journal=journal, business_date=DAY, allow_publish=False, notify=False,
        deadline_monotonic=time.monotonic() + 2100,
    )
    tick._task_paths = lambda row: (WORKSPACE / "test.log", WORKSPACE / "test.json")
    tick._run_stage_process = lambda row: stage_module.stage_identity_completeness(
        SimpleNamespace(business_date=DAY_TEXT)
    )
    rows = [row for row in journal.claim_ready(RUN_ID) if str(row["task_key"]) == key]
    assert len(rows) == 1, rows
    with patch.object(completeness_module, "run_daily_completeness", side_effect=fake_run):
        tick.execute_claim(rows[0])
    return tick


def skipped(**kwargs: Any) -> dict[str, Any]:
    # The real early return of run_daily_completeness when budget < 1200 s.
    return {"stage": "identity-completeness", "latestAdvanced": False,
            "status": "SKIPPED_INSUFFICIENT_TICK_BUDGET", "budgetSeconds": 118.0}


def inventory_failed(**kwargs: Any) -> dict[str, Any]:
    raise RuntimeError('GemRate inventory build failed: {"status": "INCOMPLETE_CENSUS"}')


def main() -> int:
    try:
        journal, key = new_task("skip")
        # More skips than max_attempts must still spend no attempt.
        for round_no in range(4):
            tick = run_once(journal, key, skipped)
            state = journal.task(key)
            assert state["status"] == "RETRY" and state["attempts"] == 0, state
            assert state["last_error_code"] == "IDENTITY_COMPLETENESS_TICK_BUDGET_DEFERRED", state
            assert tick._claim_batch(1) == [], "a budget skip must end claiming in this tick"
            print(f"OK   skip {round_no + 1}: RETRY attempts=0, same tick closed")

        journal, key = new_task("fail")
        run_once(journal, key, inventory_failed)
        state = journal.task(key)
        assert state["attempts"] == 1 and state["last_error_code"] == "SOURCE_FAILED", state
        print("OK   a real inventory failure still spends one attempt (SOURCE_FAILED)")
        print("PASS identity-completeness tick-budget skip refunds its attempt")
        return 0
    finally:
        shutil.rmtree(WORKSPACE, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
