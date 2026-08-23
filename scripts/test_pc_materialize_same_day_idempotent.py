#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Same business day + same evidence => the PC price materializer writes nothing.

A05 2026-08-23 [KNOWN, DB]: every run re-stamped `asOf` into the hashed
pc_psa10_current_price_v1 payload, so all 1238 source observations and price
rows were rewritten each run even when the page, price, artifact and sales
were identical.  Downstream that rewrote 626k history-acceptance rows, minted
1618 bootstrap quotes and 35,949 legacy quote rows per run (660k rows of
churn by 08-23).  The V2 contract needs one capture per business day
(quote.checked_at inside business_window_utc), not one per run.

Contract for changed_rows():
  * V2 run, existing row inside the business window, same evidence
    (payload minus asOf), same price/priority/status/id  -> unchanged
  * any evidence difference (artifact, method, sales, price) -> changed
  * existing row outside the window (yesterday's capture)   -> changed
  * no V2 business date in the environment                  -> old exact rule

Run: python -X utf8 scripts/test_pc_materialize_same_day_idempotent.py
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

import pc_psa10_price_materialize as mat  # noqa: E402
from daily_chain_v2_db import business_window_utc  # noqa: E402

FAILED: list[str] = []
CHECKS = 0
BUSINESS_DATE = "2026-08-23"


def check(label: str, got, want) -> None:
    global CHECKS
    CHECKS += 1
    if got != want:
        FAILED.append(f"FAIL {label}\n  got  {got!r}\n  want {want!r}")


class FakeCursor:
    def __init__(self, rows):
        self.rows = rows
        self.sql = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self.sql.append(" ".join(str(sql).split()))

    def fetchall(self):
        return [dict(row) for row in self.rows]


class FakeConnection:
    def __init__(self, rows):
        self.rows = rows
        self.cursors = []

    def cursor(self):
        cur = FakeCursor(self.rows)
        self.cursors.append(cur)
        return cur


def payload(as_of: str, **over):
    base = {
        "contract": "pc_psa10_current_price_v1",
        "variantId": 906,
        "source": "pricecharting",
        "externalEntityId": "2618188",
        "method": "pricecharting_explicit_psa10_field_v1",
        "field": "VGPC.chart_data.manualonly.last",
        "artifactSha256": "a" * 64,
        "sourceUrl": "https://www.pricecharting.com/game/pokemon-promo/x-85",
        "selectedSaleFingerprints": [],
        "latestSoldDate": None,
        "asOf": as_of,
    }
    base.update(over)
    return base


def planned(as_of: str, **over):
    body = payload(as_of, **over)
    return {
        "variantId": 906,
        "sourceCode": "pricecharting",
        "observedDate": "2026-08-01",
        "priceUsd": over.pop("priceUsd", "69034.690000") if "priceUsd" in over else "69034.690000",
        "effectiveAt": as_of,
        "sourcePriority": 95,
        "metricStatus": "ready",
        "payloadSha256": mat.sha256(body),
        "payload": body,
    }


def existing(as_of: str, **over):
    body = payload(as_of, **over)
    stamp = datetime.fromisoformat(as_of.replace("Z", "+00:00")).replace(tzinfo=None)
    return {
        "variant_id": 906,
        "source_code": "pricecharting",
        "observed_date": "2026-08-01",
        "effective_at": stamp,
        "price_usd": "69034.690000",
        "source_priority": 95,
        "metric_status": "ready",
        "payload_sha256": mat.sha256(body),
        "source_external_entity_id": "2618188",
        "source_observation_id": 5464232,
        "observation_source_code": "pricecharting",
        "observation_external_entity_id": "2618188",
        "observation_payload_sha256": mat.sha256(body),
        "observation_payload_json": json.dumps(body, sort_keys=True),
    }


def main() -> int:
    os.environ["CARDZ_V2_BUSINESS_DATE"] = BUSINESS_DATE
    os.environ.pop("CARDZ_V2_RUN_STARTED_AT", None)
    start, end = business_window_utc(BUSINESS_DATE)
    first_run = (start + timedelta(hours=3, minutes=40)).isoformat() + "Z"
    second_run = (start + timedelta(hours=8, minutes=35)).isoformat() + "Z"
    yesterday = (start - timedelta(hours=6)).isoformat() + "Z"

    # 1. same day, same evidence, later asOf -> unchanged (fires on the old code)
    row = planned(second_run)
    conn = FakeConnection([existing(first_run)])
    check("same day + same evidence -> no write", mat.changed_rows(conn, [row]), [])

    # 2. same day but the page changed (new artifact) -> changed
    conn = FakeConnection([existing(first_run)])
    check("same day + new artifact -> write",
          len(mat.changed_rows(conn, [planned(second_run, artifactSha256="b" * 64)])), 1)

    # 3. same day, same artifact, different price -> changed
    row = planned(second_run)
    row["priceUsd"] = "70000.000000"
    conn = FakeConnection([existing(first_run)])
    check("same day + new price -> write", len(mat.changed_rows(conn, [row])), 1)

    # 4. same day, eBay median with different sales -> changed
    conn = FakeConnection([existing(first_run, selectedSaleFingerprints=["f1"], latestSoldDate="2026-08-20")])
    check("same day + new sale fingerprints -> write",
          len(mat.changed_rows(conn, [planned(second_run, selectedSaleFingerprints=["f1", "f2"], latestSoldDate="2026-08-22")])), 1)

    # 5. yesterday's capture, same evidence -> changed (the daily capture the contract needs)
    conn = FakeConnection([existing(yesterday)])
    check("yesterday's capture -> write", len(mat.changed_rows(conn, [planned(second_run)])), 1)

    # 6. no existing row -> changed
    conn = FakeConnection([])
    check("no existing row -> write", len(mat.changed_rows(conn, [planned(second_run)])), 1)

    # 7. existing row's observation link broken -> changed
    bad = existing(first_run)
    bad["observation_external_entity_id"] = "999"
    conn = FakeConnection([bad])
    check("broken observation link -> write", len(mat.changed_rows(conn, [planned(second_run)])), 1)

    # 8. quarantined existing row -> changed (status differs)
    q = existing(first_run)
    q["metric_status"] = "quarantined"
    conn = FakeConnection([q])
    check("quarantined row -> write", len(mat.changed_rows(conn, [planned(second_run)])), 1)

    # 9. run started before the day (manual window) still counts as the same window
    os.environ["CARDZ_V2_RUN_STARTED_AT"] = (start - timedelta(hours=2)).isoformat() + "Z"
    early = (start - timedelta(hours=1)).isoformat() + "Z"
    conn = FakeConnection([existing(early)])
    check("capture after run start, before JST midnight -> no write", mat.changed_rows(conn, [planned(second_run)]), [])
    os.environ.pop("CARDZ_V2_RUN_STARTED_AT", None)

    # 10. outside V2 (no business date) the old exact rule still applies
    os.environ.pop("CARDZ_V2_BUSINESS_DATE", None)
    conn = FakeConnection([existing(first_run)])
    check("no V2 business date -> old exact rule writes", len(mat.changed_rows(conn, [planned(second_run)])), 1)
    conn = FakeConnection([existing(second_run)])
    check("no V2 business date + identical stamp -> no write", mat.changed_rows(conn, [planned(second_run)]), [])

    for line in FAILED:
        print(line)
    print(f"{'FAIL' if FAILED else 'OK'} checks={CHECKS} failed={len(FAILED)}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
