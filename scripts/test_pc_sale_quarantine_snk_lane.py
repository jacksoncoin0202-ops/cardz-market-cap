#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The quarantine receipt's price-spike rule judges the SNKRDUNK lane too (no DB) -- 2026-09-25.

pc_sale_title_quarantine.collect_document used to hand the price rule
(sale_price_outlier.is_isolated_price_outlier) only pricecharting sales.  The
058 history view and, since 2026-09-25, psa10_latest_sale_quote drop a
quarantined sale of ANY source, yet SNKRDUNK's JPY 1,999,999 placeholder
(sale 2452342, variant 126) was only caught by hand.  collect_document now
loads both lanes and groups them by (source, variant id).

  S1 collect_document asks load_candidate_sales for BOTH lanes
  S2 a SNKRDUNK placeholder spike becomes a price_isolated_spike entry,
     with the landing row's transaction value
  S3 the pricecharting spike on the same variant is still an entry
     (a key by variant id alone would let one lane overwrite the other)
  S4 every group the rule judges holds ONE lane: a SNKRDUNK sale is never
     judged against pricecharting prices, or the other way round
  S5 steady sales of either lane are not entries
  M1-M4 a MANUAL table row (reason no rule produces) is carried into the
     regenerated receipt with its exact reason, even with its landing row
     gone, and is no neighbour of the price rule

Driven through collect_document itself with a fake cursor and a stubbed
planner loader, so dropping the snkrdunk load in the builder turns S1/S2 red,
and keeping only rule-reason table rows turns M1/M2 red.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

import pc_sale_title_quarantine as Q  # noqa: E402
import psa10_latest_sale_quote as P  # noqa: E402

FAILED: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    if ok:
        print(f"ok   {label}")
    else:
        FAILED.append(label)
        print(f"FAIL {label}{': ' + detail if detail else ''}")


BASE = datetime(2026, 9, 1)
VARIANT = 126
LANES = ("pricecharting", "snkrdunk")


def landing(sale_id: int, day: int, price: str, lane: str) -> dict:
    """market_sale_observation + identity row, the DETAIL_SQL shape."""

    return {
        "id": sale_id,
        "variant_id": VARIANT,
        "sold_at": BASE + timedelta(days=day, hours=12 if lane == "snkrdunk" else 0),
        "unit_price_usd": price,
        "quantity": 1,
        "transaction_value_usd": price,
        "listing_title": None if lane == "snkrdunk" else "Pokemon card PSA 10",
        "collector_number": "001",
        "_lane": lane,
    }


def planner(row: dict) -> dict:
    """What psa10_latest_sale_quote.load_candidate_sales hands the price rule."""

    return {
        "saleObservationId": row["id"],
        "variantId": row["variant_id"],
        "soldAt": row["sold_at"],
        "unitPriceUsd": row["unit_price_usd"],
        "transactionFingerprint": f"fp{row['id']}",
        "_lane": row["_lane"],
    }


DAYS = [0, 1, 2, 3, 4, 6, 7, 8, 9, 10]
PC_STEADY = [landing(100 + i, day, "17000.00", "pricecharting") for i, day in enumerate(DAYS)]
PC_SPIKE = landing(700, 5, "1485.00", "pricecharting")
SNK_STEADY = [landing(300 + i, day, "145.00", "snkrdunk") for i, day in enumerate(DAYS)]
# The shape of sale 2452342: JPY 1,999,999 = ~$12,973 among ~$145 trades.
SNK_PLACEHOLDER = landing(800, 5, "12972.69", "snkrdunk")

LANE_ROWS = {
    "pricecharting": PC_STEADY + [PC_SPIKE],
    "snkrdunk": SNK_STEADY + [SNK_PLACEHOLDER],
}
DETAILS = {row["id"]: row for rows in LANE_ROWS.values() for row in rows}

# fixture has teeth: each lane, judged on its own, condemns exactly its spike
check("fixture: the rule condemns the pricecharting spike on its own lane",
      sorted(Q.price_spike_verdicts({VARIANT: [planner(r) for r in LANE_ROWS["pricecharting"]]})) == [700])
check("fixture: the rule condemns the SNKRDUNK placeholder on its own lane",
      sorted(Q.price_spike_verdicts({VARIANT: [planner(r) for r in LANE_ROWS["snkrdunk"]]})) == [800])


class FakeCursor:
    """Answers the three SELECTs collect_document itself sends."""

    def __init__(self, stored: list[dict] | None = None) -> None:
        self._rows: list[dict] = []
        self._stored = list(stored or [])

    def execute(self, sql: str, params=None) -> None:
        if "FROM market_pc_sale_title_quarantine" in sql:
            self._rows = list(self._stored)
        elif "WHERE s.id IN" in sql:
            self._rows = [DETAILS[int(i)] for i in (params or ()) if int(i) in DETAILS]
        elif "listing_title IS NOT NULL" in sql:
            self._rows = []  # no title rows: only the price rule is under test here
        else:
            raise AssertionError(f"unexpected SQL: {sql[:80]}")

    def fetchall(self) -> list[dict]:
        return list(self._rows)


asked_lanes: list[str] = []
judged_groups: list[tuple] = []
excluded_by_loader: list[set] = []


def fake_load_candidate_sales(cursor, *, source, variant_ids, quarantined_sale_ids=None):
    asked_lanes.append(source)
    excluded_by_loader.append(set(quarantined_sale_ids or ()))
    rows = LANE_ROWS.get(source, [])
    return {VARIANT: [planner(r) for r in rows]} if VARIANT in variant_ids and rows else {}


real_verdicts = Q.price_spike_verdicts


def spy_verdicts(sales_by_variant):
    for key, sales in sales_by_variant.items():
        judged_groups.append((key, sorted({sale["_lane"] for sale in sales})))
    return real_verdicts(sales_by_variant)


P.current_universe_variant_ids = lambda cursor: [VARIANT]
P.load_candidate_sales = fake_load_candidate_sales
Q.price_spike_verdicts = spy_verdicts
try:
    doc = Q.collect_document(FakeCursor(), stamp="20260925T000000Z")
finally:
    Q.price_spike_verdicts = real_verdicts

by_id = {entry["saleObservationId"]: entry for entry in doc["entries"]}

# S1
check("S1 collect_document loads the pricecharting AND the snkrdunk lane",
      sorted(asked_lanes) == sorted(LANES), str(asked_lanes))

# S2
check("S2 a SNKRDUNK placeholder spike is a price_isolated_spike entry",
      by_id.get(800, {}).get("reason") == Q.REASON_PRICE, str(by_id.get(800)))
check("S2 ...carrying the landing row's transaction value (what the FE subtracts)",
      by_id.get(800, {}).get("transactionValueUsd") == 12972.69, str(by_id.get(800)))

# S3
check("S3 the pricecharting spike on the same variant is still an entry",
      by_id.get(700, {}).get("reason") == Q.REASON_PRICE, str(by_id.get(700)))

# S4
check("S4 every group the price rule judges holds exactly one lane",
      bool(judged_groups) and all(len(lanes) == 1 for _, lanes in judged_groups), str(judged_groups))
check("S4 both lanes of the same variant are judged, as separate groups",
      sorted(lanes[0] for _, lanes in judged_groups if len(lanes) == 1) == sorted(LANES), str(judged_groups))

# S5
steady = {row["id"] for row in PC_STEADY + SNK_STEADY}
check("S5 steady sales of either lane are not entries",
      not (steady & set(by_id)), str(sorted(steady & set(by_id))))
check("S5 exactly the two spikes are entries", sorted(by_id) == [700, 800], str(sorted(by_id)))
check("S5 priceScannedSales counts both lanes",
      doc.get("priceScannedSales") == sum(len(rows) for rows in LANE_ROWS.values()),
      str(doc.get("priceScannedSales")))

# M: a MANUAL quarantine (a table row whose reason no rule produces, e.g. an
# operator/judge-confirmed wrong sale) is a first-class input.  The 02:00
# release regenerates the receipt from collect_document; a manual row missing
# from it turns the L308 DB gate red, and the chain would stop.
MANUAL = "manual_wrong_sale@jev_judge_20260926"


def stored(row: dict, reason: str) -> dict:
    """STORED_SQL shape (LEFT JOIN: landing columns may be None)."""

    keep = ("id", "variant_id", "sold_at", "unit_price_usd", "quantity",
            "transaction_value_usd", "listing_title", "collector_number")
    return {**{k: row.get(k) for k in keep}, "reason": reason,
            "card_language": "en", "set_name": None, "canonical_name": None}


manual_pc = stored(PC_STEADY[3], MANUAL)          # no rule flags this PC sale
manual_snk = stored(SNK_STEADY[3], "snk_trade_withdrawn_by_source@snk_sale_quarantine_20260925")
manual_gone = {**stored(PC_STEADY[0], MANUAL), "id": 990, "sold_at": None, "unit_price_usd": None,
               "quantity": None, "transaction_value_usd": None, "listing_title": None}
excluded_by_loader.clear()
doc_m = Q.collect_document(FakeCursor([manual_pc, manual_snk, manual_gone]), stamp="20260925T000000Z")
by_id_m = {entry["saleObservationId"]: entry for entry in doc_m["entries"]}
manual_ids = {manual_pc["id"], manual_snk["id"], 990}
check("M1 every manual table row is a receipt entry",
      manual_ids <= set(by_id_m), str(sorted(manual_ids - set(by_id_m))))
check("M1 ...with its exact stored reason, whatever the text",
      by_id_m.get(manual_pc["id"], {}).get("reason") == MANUAL
      and by_id_m.get(manual_snk["id"], {}).get("reason") == manual_snk["reason"], str(
          [by_id_m.get(i) for i in (manual_pc["id"], manual_snk["id"])]))
check("M2 a manual row whose landing row is gone is still an entry",
      by_id_m.get(990, {}).get("reason") == MANUAL, str(by_id_m.get(990)))
check("M3 manual rows are no neighbour of the price rule, in either lane",
      len(excluded_by_loader) == len(LANES) and all(manual_ids <= ex for ex in excluded_by_loader),
      str([sorted(ex) for ex in excluded_by_loader]))
check("M4 the rule entries are unchanged beside them",
      {700, 800} <= set(by_id_m) and by_id_m[700]["reason"] == Q.REASON_PRICE, str(sorted(by_id_m)))

print()
if FAILED:
    print(f"FAILED {len(FAILED)}: {FAILED}")
    raise SystemExit(1)
print("all SNKRDUNK-lane quarantine receipt checks passed")
