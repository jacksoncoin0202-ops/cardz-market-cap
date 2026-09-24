#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""R1 2026-09-25: a dead identity census must not hold price collection forever.

The census only finds NEW cards; the prices of the cards already carried never
read it.  So when the census stage is dead (PARKED/TERMINAL) and the census
file last written on disk is at most CENSUS_FALLBACK_MAX_AGE_DAYS older than the
business date's 11:00 JST start, plan() journals IDENTITY_CENSUS_STALE once and
plans the provider tasks.  An older or missing file keeps the old fail-closed
CORE_TASK_PARKED park, and a census that is still pending/retrying is still
waited for.  The warning is on record before any identity stage -- the ones
that read the census -- is planned.  Once the fallback opened the run, an
operator unpark of the census re-runs it beside the prices and never closes
plan() again (review fix 2026-09-25).

sqlite journal only: no MySQL, no network, no stage subprocess, and alerts go
through CARDZ_V2_NOTIFY_DRY_RUN.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import time
from contextlib import closing
from datetime import date, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))
os.environ["CARDZ_V2_NOTIFY_DRY_RUN"] = "1"

import daily_chain_v2 as v2core  # noqa: E402
from daily_chain_v2 import ALWAYS_ALERT_EVENTS, DailyChainV2, jst_schedule  # noqa: E402
from daily_chain_v2_journal import Journal, iso  # noqa: E402

DAY = date(2026, 8, 20)
SCHEDULE = jst_schedule(DAY)
START = SCHEDULE["start"]
# Hours after the start: the anchor is the business date, so a late tick opens
# (or keeps closed) exactly the same gate the first one did.
LATE_TICK = START + timedelta(hours=5)
CLAIMABLE = {"PENDING", "READY", "RETRY", "INTERRUPTED"}
failures: list[str] = []


def check(name: str, condition: bool, detail: Any = "") -> None:
    if condition:
        print(f"POSITIVE_OK {name}")
    else:
        failures.append(name)
        print(f"FAIL {name} {detail}")


def new_chain(folder: Path, census_file: Path) -> tuple[Journal, DailyChainV2]:
    journal = Journal(folder / "chain.sqlite3")
    journal.initialise()
    journal.ensure_run(
        business_date=DAY.isoformat(),
        source_cutoff_at=iso(SCHEDULE["source_cutoff"]),
        sla_at=iso(SCHEDULE["sla"]),
        final_at=iso(SCHEDULE["final"]),
    )
    chain = DailyChainV2(
        journal=journal, business_date=DAY, allow_publish=False, notify=False,
        deadline_monotonic=time.monotonic() + 600, schedule=SCHEDULE,
    )
    chain.census_fallback_path = census_file
    return journal, chain


def set_status(journal: Journal, task_key: str, status: str) -> None:
    with closing(sqlite3.connect(str(journal.path))) as conn:
        conn.execute(
            "UPDATE chain_task SET status=?,attempts=2,last_error_code='SOURCE_FAILED',"
            "updated_at=? WHERE task_key=?",
            (status, iso(), task_key),
        )
        conn.commit()


def events(journal: Journal, chain: DailyChainV2, event_type: str) -> list[dict[str, Any]]:
    return [
        {**row, "payload": json.loads(str(row["payload_json"]))}
        for row in journal.pending_events(chain.run_id)
        if str(row["event_type"]) == event_type
    ]


def providers(journal: Journal, chain: DailyChainV2) -> list[dict[str, Any]]:
    return [
        row for row in journal.tasks(chain.run_id, phase="source")
        if str(row["source_code"]) != "system"
    ]


def drive_to_census(journal: Journal, chain: DailyChainV2, census_state: str) -> None:
    """plan() until the census exists, then leave it in `census_state`."""

    for _ in range(40):
        chain.plan(START)
        census = chain.stage_row("identity-census")
        if census is not None:
            set_status(journal, str(census["task_key"]), census_state)
            return
        for row in journal.tasks(chain.run_id):
            if str(row["status"]) in CLAIMABLE:
                set_status(journal, str(row["task_key"]), "COMPLETED")
    raise AssertionError("plan() never planned identity-census")


def census_file(folder: Path, age_days: float | None) -> Path:
    path = folder / "psa10_1000_plus.jsonl"
    if age_days is not None:
        path.write_text('{"psa_id":"1","psa_10":1000}\n', encoding="utf-8")
        stamp = (START - timedelta(days=age_days)).timestamp()
        os.utime(path, (stamp, stamp))
    return path


check("IDENTITY_CENSUS_STALE is an always-alert warn",
      ALWAYS_ALERT_EVENTS.get("IDENTITY_CENSUS_STALE", ("", "", 0))[1] == "warn")
check("the fallback age bound is three days", v2core.CENSUS_FALLBACK_MAX_AGE_DAYS == 3.0)

# A. Dead census + last good file 2.9 days old: warn once, prices go ahead.
for dead in ("PARKED", "TERMINAL"):
    with tempfile.TemporaryDirectory(prefix="v2-r1-fresh-") as raw:
        folder = Path(raw)
        journal, chain = new_chain(folder, census_file(folder, 2.9))
        drive_to_census(journal, chain, dead)
        chain.plan(START)
        stale = events(journal, chain, "IDENTITY_CENSUS_STALE")
        check(f"{dead}: dead census with a 2.9-day file warns once", len(stale) == 1, stale)
        check(f"{dead}: no CORE_TASK_PARKED for a recently written census file",
              not events(journal, chain, "CORE_TASK_PARKED"))
        check(f"{dead}: provider collection is planned", bool(providers(journal, chain)))
        check(f"{dead}: the warning precedes every identity stage",
              not journal.tasks(chain.run_id, phase="identity"))
        if stale:
            payload = stale[0]["payload"]
            check(f"{dead}: the warning names the file date",
                  payload.get("censusMtime") and str(payload["censusMtime"]) in str(payload.get("reason")),
                  payload)
            check(f"{dead}: the warning carries the census state", payload.get("state") == dead, payload)
        # A tick five hours later still sees the same answer (age is anchored on
        # the business date, not on the tick clock) and does not warn again.
        chain.plan(LATE_TICK)
        chain.plan(LATE_TICK)
        check(f"{dead}: repeated plans stay deduplicated",
              len(events(journal, chain, "IDENTITY_CENSUS_STALE")) == 1)
        check(f"{dead}: a later tick never re-parks the open run",
              not events(journal, chain, "CORE_TASK_PARKED"))
        if dead == "PARKED":
            # The identity stages downstream still read the census, and only
            # after the warning: intake keeps its --census argument.
            for _ in range(80):
                intake = chain.stage_row("identity-intake")
                if intake is not None:
                    break
                for row in journal.tasks(chain.run_id):
                    if str(row["status"]) in CLAIMABLE:
                        set_status(journal, str(row["task_key"]), "COMPLETED")
                chain.plan(LATE_TICK)
            check("PARKED: identity intake is reached past a dead census", intake is not None)
            if intake is not None:
                args = json.loads(str(intake["payload_json"]))["stageArgs"]
                check("PARKED: intake still names its --census input", "--census" in args, args)
                warned_at = str(events(journal, chain, "IDENTITY_CENSUS_STALE")[0]["created_at"])
                first_identity = min(
                    str(row["created_at"]) for row in journal.tasks(chain.run_id, phase="identity")
                )
                check("PARKED: IDENTITY_CENSUS_STALE was journaled before the first identity stage",
                      warned_at <= first_identity, (warned_at, first_identity))

# B. Dead census + last good file 3.5 days old: the old park, no prices.
# C. Dead census + no file at all: the old park, no prices.
for label, age in (("3.5-day file", 3.5), ("missing file", None)):
    with tempfile.TemporaryDirectory(prefix="v2-r1-old-") as raw:
        folder = Path(raw)
        journal, chain = new_chain(folder, census_file(folder, age))
        drive_to_census(journal, chain, "TERMINAL")
        chain.plan(START)
        parked = events(journal, chain, "CORE_TASK_PARKED")
        check(f"{label}: dead census parks exactly as before", len(parked) == 1, parked)
        if parked:
            check(f"{label}: the park still says unpark", "unpark" in parked[0]["payload"]["nextRetry"])
        check(f"{label}: no stale warning", not events(journal, chain, "IDENTITY_CENSUS_STALE"))
        check(f"{label}: no provider collection", not providers(journal, chain))

# D. A census that is still retrying is waited for, however fresh the file.
for waiting in ("PENDING", "RETRY", "RUNNING"):
    with tempfile.TemporaryDirectory(prefix="v2-r1-wait-") as raw:
        folder = Path(raw)
        journal, chain = new_chain(folder, census_file(folder, 0.5))
        drive_to_census(journal, chain, waiting)
        chain.plan(START)
        check(f"{waiting}: a live census is still waited for",
              not providers(journal, chain)
              and not events(journal, chain, "IDENTITY_CENSUS_STALE")
              and not events(journal, chain, "CORE_TASK_PARKED"))

# E. Review fix 2026-09-25: after the fallback opened provider collection, an
# operator unpark turns the census READY again.  It used to close plan() at the
# census gate on every tick behind the half-hour rerun -- no contract, identity
# or publish -- and could push the run past 17:00 JST into FAILED_FINAL.  The
# rerun now goes on beside the prices, and the warning says so.
with tempfile.TemporaryDirectory(prefix="v2-r1-unpark-") as raw:
    folder = Path(raw)
    journal, chain = new_chain(folder, census_file(folder, 1.0))
    drive_to_census(journal, chain, "TERMINAL")
    chain.plan(START)
    census_key = str((chain.stage_row("identity-census") or {}).get("task_key"))
    check("unpark: the fallback opened provider collection", bool(providers(journal, chain)))
    stale = events(journal, chain, "IDENTITY_CENSUS_STALE")
    check("unpark: the warning says prices and publish no longer wait on the census",
          bool(stale) and "no longer wait" in str(stale[0]["payload"].get("nextRetry")), stale)
    revived = journal.unpark(census_key, run_id=chain.run_id, reason="review fixture")
    check("unpark: the operator lever still revives the census",
          revived is not None and str(journal.task(census_key)["status"]) == "READY")
    for row in providers(journal, chain):
        set_status(journal, str(row["task_key"]), "COMPLETED")
    # An hour after the start: before the 14:00 JST cutoff, so the barrier opens
    # on settled providers alone, not on the clock.
    for _ in range(5):
        chain.plan(START + timedelta(hours=1))
    check("unpark: core-contract-pre is planned while the census re-runs",
          chain.stage_row("core-contract-pre") is not None)
    check("unpark: the revived census is still there to be claimed",
          str(journal.task(census_key)["status"]) == "READY")
    check("unpark: nothing parks and the warning is not repeated",
          not events(journal, chain, "CORE_TASK_PARKED")
          and len(events(journal, chain, "IDENTITY_CENSUS_STALE")) == 1)

if failures:
    print(f"FAILED {len(failures)}: {failures}")
    raise SystemExit(1)
print("ALL_OK test_v2_restructure_census")
