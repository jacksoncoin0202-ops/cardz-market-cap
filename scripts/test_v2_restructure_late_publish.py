#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""R3 2026-09-25: the run outcome follows the fact, not the clock.

A confirmed live publication whose event occurred at or after the business
date's `final` (17:00 JST) is recorded as PUBLISHED / PUBLISHED_DEGRADED --
the status values the journal and v2_run_observer already know -- and the
lateness rides in the live.confirmed payload (late=true, finalAt).  FAILED_FINAL
is stamped only on a run that has NOT recorded a publication.  Both branches go
through run_tick() past `final`, the same order the daily tick uses:
finalise_live() first, lifecycle_events() second.  The before-`final` cases
call those two methods directly, because a tick before `final` would plan and
execute real stages.

sqlite journal only: no MySQL, no network, no stage subprocess; the live-confirm
receipt is a stub row and alerts go through CARDZ_V2_NOTIFY_DRY_RUN.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))
os.environ["CARDZ_V2_NOTIFY_DRY_RUN"] = "1"

import daily_chain_v2 as v2core  # noqa: E402
from daily_chain_v2 import DailyChainV2, jst_schedule  # noqa: E402
from daily_chain_v2_journal import Journal, iso  # noqa: E402

DAY = date(2026, 8, 20)
SCHEDULE = jst_schedule(DAY)
FINAL = SCHEDULE["final"]
FOLDERS: list[Path] = []
failures: list[str] = []


def check(name: str, condition: bool, detail: Any = "") -> None:
    if condition:
        print(f"POSITIVE_OK {name}")
    else:
        failures.append(name)
        print(f"FAIL {name} {detail}")


def live_row(occurred_at: Any, *, degraded: list[str] | None = None,
             status: str = "COMPLETED", attempts: int = 1) -> dict[str, Any]:
    event = {
        "generationId": "db3308_0123456789abcdef",
        "generatedAt": iso(FINAL - timedelta(hours=1)),
        "contentSha256": "c" * 64,
        "activeCount": 1200,
        "occurredAt": iso(occurred_at),
        "liveUrl": "https://example.invalid/box",
        "sourceHealth": {"snkrdunk": {"status": "DEGRADED"}} if degraded else {},
        "degradedSources": list(degraded or []),
    }
    return {
        "task_key": "stage:live-confirm", "status": status, "attempts": attempts,
        "result_json": json.dumps({"stage": "live-confirm", "eventId": 41, "event": event}),
    }


def new_chain(live: dict[str, Any] | None) -> tuple[Journal, DailyChainV2]:
    folder = Path(tempfile.mkdtemp(prefix="v2-r3-"))
    FOLDERS.append(folder)
    journal = Journal(folder / "chain.sqlite3")
    journal.initialise()
    journal.ensure_run(
        business_date=DAY.isoformat(),
        source_cutoff_at=iso(SCHEDULE["source_cutoff"]),
        sla_at=iso(SCHEDULE["sla"]),
        final_at=iso(FINAL),
    )
    chain = DailyChainV2(
        journal=journal, business_date=DAY, allow_publish=False, notify=False,
        deadline_monotonic=time.monotonic() + 3600, schedule=SCHEDULE,
    )
    chain.stage_row = lambda capability: live if capability == "live-confirm" else None
    chain.recover_expired = lambda: None
    chain.write_health_liveness = lambda *args, **kwargs: None
    return journal, chain


def tick_after_final(chain: DailyChainV2, now: Any) -> None:
    """run_tick() with the wall clock past `final`.

    Past `final` the tick only finalises and never plans or executes, so no
    stage subprocess can start; before `final` the test calls the same two
    methods directly instead.
    """

    assert now >= FINAL
    original = v2core.utc_now
    v2core.utc_now = lambda: now
    try:
        chain.run_tick()
    finally:
        v2core.utc_now = original


def tick_at(live: dict[str, Any] | None, now: Any) -> tuple[Journal, DailyChainV2, dict[str, Any]]:
    journal, chain = new_chain(live)
    if now >= FINAL:
        tick_after_final(chain, now)
    else:
        chain.finalise_live()
        chain.lifecycle_events(now)
    return journal, chain, journal.run(chain.run_id) or {}


def events(journal: Journal, chain: DailyChainV2, event_type: str) -> list[dict[str, Any]]:
    return [
        json.loads(str(row["payload_json"]))
        for row in journal.pending_events(chain.run_id)
        if str(row["event_type"]) == event_type
    ]


AFTER_FINAL = FINAL + timedelta(minutes=10)

# A. The site went live after `final`: published, late=true, no FAILED_FINAL.
for label, degraded, expected in (
    ("late", None, "PUBLISHED"),
    ("late degraded", ["snkrdunk"], "PUBLISHED_DEGRADED"),
):
    journal, chain, run = tick_at(live_row(FINAL + timedelta(minutes=5), degraded=degraded), AFTER_FINAL)
    check(f"{label}: run status is {expected}", run.get("status") == expected, run.get("status"))
    check(f"{label}: publication is recorded", run.get("publication_status") == expected,
          run.get("publication_status"))
    confirmed = events(journal, chain, "live.confirmed")
    check(f"{label}: one live.confirmed event", len(confirmed) == 1, confirmed)
    if confirmed:
        check(f"{label}: the payload says late=true", confirmed[0].get("late") is True, confirmed[0])
        check(f"{label}: the payload names the final cutoff",
              confirmed[0].get("finalAt") == iso(FINAL), confirmed[0])
    check(f"{label}: no FAILED_FINAL event", not events(journal, chain, "FAILED_FINAL"))
    # The next tick sees a finished run and leaves it published.
    tick_after_final(chain, AFTER_FINAL + timedelta(minutes=10))
    again = journal.run(chain.run_id) or {}
    check(f"{label}: a later tick keeps it {expected}", again.get("status") == expected, again.get("status"))
    check(f"{label}: a later tick still raises no FAILED_FINAL", not events(journal, chain, "FAILED_FINAL"))

# B. An on-time confirmation is the same PUBLISHED, with late=false.
journal, chain, run = tick_at(live_row(FINAL - timedelta(hours=1)), FINAL - timedelta(minutes=50))
confirmed = events(journal, chain, "live.confirmed")
check("on time: run status is PUBLISHED", run.get("status") == "PUBLISHED", run.get("status"))
check("on time: the payload says late=false", bool(confirmed) and confirmed[0].get("late") is False, confirmed)

# C. Nothing was published: FAILED_FINAL at `final`, exactly as before.
for label, live in (
    ("no live-confirm row", None),
    ("live-confirm gave up without an event",
     {"task_key": "stage:live-confirm", "status": "TERMINAL", "attempts": 0, "result_json": None}),
):
    journal, chain, run = tick_at(live, AFTER_FINAL)
    check(f"{label}: run status is FAILED_FINAL", run.get("status") == "FAILED_FINAL", run.get("status"))
    check(f"{label}: no publication recorded", not run.get("publication_status"), run.get("publication_status"))
    check(f"{label}: one FAILED_FINAL event", len(events(journal, chain, "FAILED_FINAL")) == 1)
    check(f"{label}: no live.confirmed event", not events(journal, chain, "live.confirmed"))

# D. Before `final`, an unpublished run is not failed yet.
journal, chain, run = tick_at(None, FINAL - timedelta(minutes=1))
check("before final: an unpublished run is not FAILED_FINAL", run.get("status") != "FAILED_FINAL", run.get("status"))
check("before final: no FAILED_FINAL event", not events(journal, chain, "FAILED_FINAL"))

for folder in FOLDERS:
    shutil.rmtree(folder, ignore_errors=True)
if failures:
    print(f"FAILED {len(failures)}: {failures}")
    raise SystemExit(1)
print("ALL_OK test_v2_restructure_late_publish")
