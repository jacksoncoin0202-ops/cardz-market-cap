#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Quote revisions go through one gate and one batched door.

A05 2026-08-23: bootstrap_from_eligible_observations minted 1618 revisions one
INSERT + one SELECT each (3236 round trips, ~40 s of the 69 s acceptHistory
step). Contract after the change:

  * bootstrap: 1 SELECT + 2 statements per 500 rows (fails on the per-row code)
  * same rows, same ids, same owner collision check, F-MINT still fires
  * insert_quote_revision (single) and the batch agree on the id

No database: a fake cursor keeps the table in a dict keyed by lineage.
Run: python -X utf8 scripts/test_quote_revision_batch.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

import current_quote_revision as CQ  # noqa: E402

FAILED: list[str] = []
CHECKS = 0


def check(label: str, got, want) -> None:
    global CHECKS
    CHECKS += 1
    if got != want:
        FAILED.append(f"FAIL {label}\n  got  {got!r}\n  want {want!r}")


class FakeTable:
    def __init__(self) -> None:
        self.rows: dict[str, dict] = {}

    def upsert(self, params) -> None:
        lineage = params[9]
        if lineage not in self.rows:
            self.rows[lineage] = {
                "id": len(self.rows) + 1,
                "quote_lineage_sha256": lineage,
                "reconstructed_from_acceptance_id": params[11],
                "variant_id": params[0],
            }


class FakeCursor:
    def __init__(self, table: FakeTable, bootstrap_rows=()) -> None:
        self.table = table
        self.bootstrap_rows = list(bootstrap_rows)
        self.statements: list[str] = []
        self._result: list[dict] = []
        self.lastrowid = 0

    def execute(self, sql, params=None):
        s = " ".join(str(sql).split())
        self.statements.append(s[:70])
        if s.startswith("INSERT INTO market_current_quote_revision"):
            for i in range(len(params) // 13):
                self.table.upsert(params[i * 13:(i + 1) * 13])
            self._result = []
        elif "WHERE quote_lineage_sha256 IN" in s:
            self._result = [dict(self.table.rows[l]) for l in params if l in self.table.rows]
        elif "WHERE quote_lineage_sha256=%s" in s:
            self._result = [dict(self.table.rows[params[0]])] if params[0] in self.table.rows else []
        elif s.startswith("SELECT p.id AS market_price_observation_id"):
            self._result = list(self.bootstrap_rows)
        else:
            self._result = []

    def fetchall(self):
        return list(self._result)

    def fetchone(self):
        return self._result[0] if self._result else None


def main() -> int:
    live_source = CQ.mintable_quote_storage_source_codes()[0]
    n = 1618
    rows = [
        {
            "market_price_observation_id": 1000 + i,
            "variant_id": 1 + (i % 800),
            "source_code": live_source,
            "source_external_entity_id": str(5000 + i),
            "price_usd": "12.340000",
            "observed_date": "2026-08-20",
            "effective_at": "2026-08-21 01:02:03",
            "payload_sha256": f"{i:064x}",
            "source_observation_id": 7000 + i,
            "run_id": 4800,
            "source_observed_at": "2026-08-21 01:02:03",
        }
        for i in range(n)
    ]

    # 1. bootstrap round trips (this is the check that fires on the old code)
    table = FakeTable()
    cur = FakeCursor(table, rows)
    boot = CQ.bootstrap_from_eligible_observations(cur)
    chunks = -(-n // 500)
    check("bootstrap revisionsWritten", boot["revisionsWritten"], n)
    check("bootstrap table rows", len(table.rows), n)
    check(
        f"bootstrap statements <= 1 + 2*{chunks} (was 1 + 2*{n})",
        len(cur.statements) <= 1 + 2 * chunks,
        True,
    )
    # rerun: idempotent, no growth
    cur2 = FakeCursor(table, rows)
    CQ.bootstrap_from_eligible_observations(cur2)
    check("bootstrap rerun adds no rows", len(table.rows), n)

    # 2. batch door semantics
    spec = dict(
        variant_id=1, source_code=live_source, source_external_entity_id="123",
        price_usd="10.000000", source_period_at="2026-08-01",
        checked_at="2026-08-01 00:00:00", payload_sha256="a" * 64,
    )
    table = FakeTable()
    cur = FakeCursor(table)
    ids = CQ.insert_quote_revisions_batch(cur, [spec, dict(spec, payload_sha256="b" * 64), spec])
    check("ids in spec order, duplicate lineage shares the id", ids, [1, 2, 1])
    check("duplicate within a batch is one row", len(table.rows), 2)
    single = CQ.insert_quote_revision(FakeCursor(table), **spec)
    check("single door returns the same id as the batch", single, 1)
    check("empty batch writes nothing", CQ.insert_quote_revisions_batch(FakeCursor(table), []), [])

    # 3. owner collision still raises through the batch door
    lineage, params, _ = CQ._quote_revision_row(**spec)
    table = FakeTable()
    table.upsert(params[:11] + (5,) + params[12:])
    try:
        CQ.insert_quote_revisions_batch(FakeCursor(table), [spec])
        check("owner collision raises", "no error", "RuntimeError")
    except RuntimeError as error:
        check("owner collision message", "collision" in str(error), True)

    # 4. F-MINT: a retired chart source only mints as a legacy reconstruction
    legacy = dict(
        spec, source_code="pricecharting", reconstruction_kind=CQ.LEGACY_KIND,
        reconstructed_from_acceptance_id=9, purpose=CQ.QUOTE_MINT_PURPOSE_LEGACY,
    )
    table = FakeTable()
    ids = CQ.insert_quote_revisions_batch(FakeCursor(table), [legacy])
    check("legacy reconstruction mints with its owner",
          table.rows[next(iter(table.rows))]["reconstructed_from_acceptance_id"], 9)
    try:
        CQ.insert_quote_revisions_batch(FakeCursor(FakeTable()), [dict(legacy, purpose=CQ.QUOTE_MINT_PURPOSE_LIVE)])
        check("F-MINT fires through the batch door", "no error", "ValueError")
    except ValueError as error:
        check("F-MINT message", "retired" in str(error), True)

    # 5. missing row after insert is still an error (cursor that loses rows)
    class LossyCursor(FakeCursor):
        def fetchall(self):
            return []

    try:
        CQ.insert_quote_revisions_batch(LossyCursor(FakeTable()), [spec])
        check("no id raises", "no error", "RuntimeError")
    except RuntimeError as error:
        check("no id message", "returned no id" in str(error), True)

    for line in FAILED:
        print(line)
    print(f"{'FAIL' if FAILED else 'OK'} checks={CHECKS} failed={len(FAILED)}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
