#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The sale-quote mint is idempotent inside one V2 business day.

A06/A07 2026-08-23 [KNOWN, DB probe]: every rehearsal of the same business day
minted 1,753 NULL-kind sale quote revisions (pricecharting_sales 1,176 +
snkrdunk_sales 577) and the bootstrap then minted 1,618 more on top, although
not one chosen sale had moved -- the harvest clock is part of the quote
lineage and psa10_latest_sale_quote.materialize() re-stamped every evidence
row to it.

Contract (psa10_latest_sale_quote.same_day_standing_quotes):
  * outside the chain (no CARDZ_V2_BUSINESS_DATE) every planned row is minted,
    no standing probe runs
  * inside the chain a planned row is SKIPPED only when BOTH stand inside the
    business window with the same payload_sha256: the market_price_observation
    history point (same sale day, metric_status ready, effective_at in window)
    and a market_current_quote_revision (same external entity, checked_at in
    window)
  * anything else -- new sale (different payload), quarantined history point,
    stale effective_at, quote from yesterday -- is minted exactly as before
  * a fully-standing plan opens no ingest run (runId 0) and writes nothing
  * market_ingest_run.observed_count / accepted_count count what was minted
  * the receipt carries quotesStanding next to quotesMinted
  * pc_psa10_price_materialize._v2_business_window delegates to the one
    daily_chain_v2_db.business_window_from_env execution point

Seeded-bug proof: with the standing probe stubbed to "nothing stands" the
same fully-standing plan mints every row again (that is the A07 behaviour).

Run: python -X utf8 scripts/test_sale_quote_same_day_idempotent.py
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

os.environ.pop("CARDZ_V2_BUSINESS_DATE", None)
os.environ.pop("CARDZ_V2_RUN_STARTED_AT", None)

import daily_chain_v2_db  # noqa: E402
import pc_psa10_price_materialize  # noqa: E402
import psa10_latest_sale_quote as mod  # noqa: E402

FAILED: list[str] = []
CHECKS = 0
BUSINESS_DATE = "2026-08-23"
START, END = daily_chain_v2_db.business_window_utc(BUSINESS_DATE)
AS_OF = datetime(2026, 8, 23, 9, 40, 0)          # inside the window
YESTERDAY = datetime(2026, 8, 22, 9, 40, 0)      # before the window opens
SOURCE = "pricecharting_sales"


def check(label: str, got, want) -> None:
    global CHECKS
    CHECKS += 1
    if got != want:
        FAILED.append(f"FAIL {label}\n  got  {got!r}\n  want {want!r}")


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def plan_row(variant_id: int, *, price: str, sale_day: date, entity: str) -> dict:
    payload = {"variantId": variant_id, "priceUsd": price, "soldAt": sale_day.isoformat(), "entity": entity}
    return {
        "variantId": variant_id,
        "sourceCode": "pricecharting",
        "storageSourceCode": SOURCE,
        "externalEntityId": entity,
        "saleObservationId": 1000 + variant_id,
        "priceUsd": Decimal(price),
        "soldAt": datetime.combine(sale_day, datetime.min.time()),
        "observedDate": sale_day,
        "checkedAt": AS_OF,
        "ungated": False,
        "payload": payload,
        "payloadSha256": sha(json.dumps(payload, sort_keys=True)),
    }


def plan_doc(rows: list[dict]) -> dict:
    return {
        "rows": rows,
        "asOf": AS_OF,
        "source": "pricecharting",
        "storageSourceCode": SOURCE,
        "planSha256": sha("plan|" + "|".join(r["payloadSha256"] for r in rows)),
        "variantsPlanned": len(rows),
        "salesScanned": len(rows),
        "rejectedSales": [],
        "fallbackWalkbacks": [],
        "noEligibleSale": [],
    }


class FakeCursor:
    """Answers by statement prefix; records every write."""

    def __init__(self, state: dict):
        self.state = state
        self._rows: list[dict] = []
        self.lastrowid = 0
        self.rowcount = 0

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql: str, params=None):
        text = " ".join(str(sql).split())
        params = tuple(params or ())
        self.state["executed"].append(text)
        self._rows = []
        self.lastrowid = 0
        if text.startswith("SET SESSION"):
            return
        if text.startswith("SELECT variant_id, observed_date, payload_sha256, effective_at, metric_status FROM market_price_observation"):
            self.state["probes"] += 1
            source, chunk = params[0], set(params[1:])
            self._rows = [r for r in self.state["price_rows"] if r["variant_id"] in chunk and source == SOURCE]
            return
        if text.startswith("SELECT variant_id, source_external_entity_id, payload_sha256 FROM market_current_quote_revision"):
            self.state["probes"] += 1
            source, start, end, chunk = params[0], params[1], params[2], set(params[3:])
            check("quote probe window is the business window", (start, end), (START, END))
            self._rows = [
                r for r in self.state["quote_rows"]
                if r["variant_id"] in chunk and source == SOURCE and start <= r["checked_at"] < end
            ]
            return
        if text.startswith("INSERT INTO market_ingest_run"):
            self.state["runs"].append(params)
            self.lastrowid = 70
            return
        if text.startswith("INSERT INTO market_source_observation"):
            self.state["source_obs"].append(params)
            self.lastrowid = 11
            return
        if text.startswith("INSERT INTO market_price_observation"):
            self.state["price_obs"].append(params)
            self.rowcount = 1
            return
        if text.startswith("SELECT id FROM market_price_observation"):
            self._rows = [{"id": 5}]
            return
        if text.startswith("INSERT INTO market_current_quote_revision"):
            self.state["quotes"].append(params)
            self.lastrowid = 99
            return
        if text.startswith("SELECT id, variant_id, source_code"):
            self._rows = [{"id": 99, "reconstructed_from_acceptance_id": None}]
            return
        if text.startswith("UPDATE market_ingest_run"):
            self.state["completed"].append(params)
            return
        raise AssertionError("unexpected SQL: " + text[:160])

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


class FakeConnection:
    def __init__(self, state: dict):
        self.state = state
        self.commits = 0
        self.rollbacks = 0

    def cursor(self):
        return FakeCursor(self.state)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


def fresh_state(price_rows=(), quote_rows=()) -> dict:
    return {
        "executed": [], "probes": 0, "runs": [], "source_obs": [], "price_obs": [],
        "quotes": [], "completed": [], "price_rows": list(price_rows), "quote_rows": list(quote_rows),
    }


def standing_price(row: dict, *, effective_at=AS_OF, status="ready", payload_sha=None) -> dict:
    return {
        "variant_id": row["variantId"], "observed_date": row["observedDate"],
        "payload_sha256": payload_sha or row["payloadSha256"], "effective_at": effective_at,
        "metric_status": status,
    }


def standing_quote(row: dict, *, checked_at=AS_OF, payload_sha=None, entity=None) -> dict:
    return {
        "variant_id": row["variantId"], "source_external_entity_id": entity or row["externalEntityId"],
        "payload_sha256": payload_sha or row["payloadSha256"], "checked_at": checked_at,
    }


def run(rows, price_rows=(), quote_rows=()):
    state = fresh_state(price_rows, quote_rows)
    connection = FakeConnection(state)
    result = mod.materialize(connection, plan_doc(rows))
    return result, state, connection


def main() -> int:
    r1 = plan_row(2159, price="62.00", sale_day=date(2026, 8, 16), entity="pc-2159")
    r2 = plan_row(2160, price="93.19", sale_day=date(2026, 8, 19), entity="pc-2160")

    # 1. outside the chain: every row minted, no standing probe at all
    os.environ.pop("CARDZ_V2_BUSINESS_DATE", None)
    result, state, conn = run([r1, r2], [standing_price(r1)], [standing_quote(r1)])
    check("outside chain mints every row", (result["quotesMinted"], result["quotesStanding"], result["runId"]), (2, 0, 70))
    check("outside chain runs no standing probe", state["probes"], 0)
    check("outside chain ingest run counts the plan", state["runs"][0][5], 2)
    check("outside chain commits once", conn.commits, 1)

    os.environ["CARDZ_V2_BUSINESS_DATE"] = BUSINESS_DATE
    os.environ.pop("CARDZ_V2_RUN_STARTED_AT", None)

    # 2. inside the chain, everything already stands -> zero writes, no ingest run
    result, state, conn = run(
        [r1, r2], [standing_price(r1), standing_price(r2)], [standing_quote(r1), standing_quote(r2)]
    )
    check("standing plan mints nothing", (result["quotesMinted"], result["quotesStanding"], result["runId"]), (0, 2, 0))
    check("standing plan writes nothing", (state["runs"], state["source_obs"], state["price_obs"], state["quotes"], state["completed"]), ([], [], [], [], []))
    check("standing plan still commits (releases the session)", conn.commits, 1)
    check("standing plan probes both tables once per chunk", state["probes"], 2)

    # 3. one variant has a NEW sale (payload moved) -> only that one is minted
    result, state, conn = run(
        [r1, r2],
        [standing_price(r1), standing_price(r2, payload_sha=sha("old sale"))],
        [standing_quote(r1), standing_quote(r2, payload_sha=sha("old sale"))],
    )
    check("moved sale is minted, unchanged one stands", (result["quotesMinted"], result["quotesStanding"]), (1, 1))
    check("only the moved variant reaches the quote door", [p[0] for p in state["quotes"]], [2160])
    check("ingest run counts only what is minted", (state["runs"][0][5], state["completed"][0][0]), (1, 1))

    # 4. history point stands but the quote is from yesterday -> minted
    result, state, conn = run([r1], [standing_price(r1)], [standing_quote(r1, checked_at=YESTERDAY)])
    check("yesterday's quote does not stand", (result["quotesMinted"], result["quotesStanding"]), (1, 0))

    # 5. quote stands but the history point is quarantined -> minted
    result, state, conn = run([r1], [standing_price(r1, status="quarantined")], [standing_quote(r1)])
    check("quarantined history point does not stand", (result["quotesMinted"], result["quotesStanding"]), (1, 0))

    # 6. history point effective_at outside the window -> minted
    result, state, conn = run([r1], [standing_price(r1, effective_at=YESTERDAY)], [standing_quote(r1)])
    check("stale effective_at does not stand", (result["quotesMinted"], result["quotesStanding"]), (1, 0))

    # 7. quote stands under a different external entity -> minted
    result, state, conn = run([r1], [standing_price(r1)], [standing_quote(r1, entity="pc-other")])
    check("other entity's quote does not stand", (result["quotesMinted"], result["quotesStanding"]), (1, 0))

    # 8. a different sale day in the history table -> minted
    moved = dict(standing_price(r1))
    moved["observed_date"] = date(2026, 8, 1)
    result, state, conn = run([r1], [moved], [standing_quote(r1)])
    check("different sale day does not stand", (result["quotesMinted"], result["quotesStanding"]), (1, 0))

    # 9. empty plan -> nothing, no probe
    result, state, conn = run([], [], [])
    check("empty plan", (result["quotesMinted"], result["quotesStanding"], result["runId"], state["probes"]), (0, 0, 0, 0))

    # 10. seeded bug: with the standing probe disabled, the A07 behaviour returns
    real = mod.same_day_standing_quotes
    mod.same_day_standing_quotes = lambda *a, **k: set()
    try:
        result, state, conn = run(
            [r1, r2], [standing_price(r1), standing_price(r2)], [standing_quote(r1), standing_quote(r2)]
        )
        check("without the gate the same plan re-mints every row (A07)", (result["quotesMinted"], len(state["quotes"])), (2, 2))
    finally:
        mod.same_day_standing_quotes = real

    # 11. receipt carries quotesStanding
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "receipt.json"
        mod.write_receipt(plan_doc([r1, r2]), run_id=0, quotes_minted=0, quotes_standing=2, path=target)
        doc = json.loads(target.read_text(encoding="utf-8"))
        check("receipt quotesStanding / quotesMinted", (doc["quotesStanding"], doc["quotesMinted"], doc["runId"]), (2, 0, 0))
        mod.write_receipt(plan_doc([r1]), run_id=70, quotes_minted=1, path=target)
        doc = json.loads(target.read_text(encoding="utf-8"))
        check("receipt default quotesStanding is 0", doc["quotesStanding"], 0)

    # 12. one execution point for the window
    check("env window", daily_chain_v2_db.business_window_from_env(), (START, END))
    check("pc materialize delegates", pc_psa10_price_materialize._v2_business_window(), (START, END))
    check("sale mint delegates", mod._v2_business_window(), (START, END))
    os.environ.pop("CARDZ_V2_BUSINESS_DATE", None)
    check("no env -> no window", (daily_chain_v2_db.business_window_from_env(), pc_psa10_price_materialize._v2_business_window(), mod._v2_business_window()), (None, None, None))

    for line in FAILED:
        print(line)
    print(f"{'FAIL' if FAILED else 'OK'} checks={CHECKS} failed={len(FAILED)}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
