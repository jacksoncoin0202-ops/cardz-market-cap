#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PC sale quarantine receipt builder (no DB) -- 2026-09-25.

pipelines/pc_sale_title_quarantine.py now writes two kinds of entry into the one
receipt the planner (psa10_latest_sale_quote.load_title_quarantine) and the FE
(live-db-snapshot.ts loadSaleQuarantine) both subtract:

  * title_collector_contradiction -- the 2026-07-14 title<->card-number rule
  * price_isolated_spike          -- sale_price_outlier.is_isolated_price_outlier

This file pins what the builder promises, with injected rows instead of a DB:

  R1 one entry per sale id; title beats price on the same id
  R2 entries sorted by id; same inputs + same stamp => byte-identical JSON,
     whatever order the rows arrive in
  R3 `discriminators` lists both rules; the old `discriminator` field stays
  R4 sticky: a sale already in market_pc_sale_title_quarantine stays in the
     receipt with its STORED reason even when no rule flags it any more
     (receipt must cover the table, or a flipped verdict re-admits the sale)
  R5 a price entry carries the landing row's transaction_value_usd (what the
     FE subtracts from the day SUM) plus both medians as evidence
  R6 a condemned id with no landing detail row raises; it is never dropped
  R7 load_stored_rows tolerates only error 1146 (058 not applied yet)
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

import pc_sale_title_quarantine as Q  # noqa: E402

FAILED: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    if ok:
        print(f"ok   {label}")
    else:
        FAILED.append(label)
        print(f"FAIL {label}{': ' + detail if detail else ''}")


BASE = datetime(2026, 7, 1)
VARIANT = 1148
COLLECTOR = "170/181"

# The live receipt's entry shape before 2026-09-25; readers index these keys.
LEGACY_ENTRY_KEYS = [
    "saleObservationId", "variantId", "observedDate", "unitPriceUsd", "quantity",
    "transactionValueUsd", "wantedCollectorNumber", "listingTitle", "reason",
]


def landing(sale_id: int, day: int, price: str, title: str, *, value: str | None = None):
    """A market_sale_observation + catalog_printing_identity row (SCAN/DETAIL shape)."""

    return {
        "id": sale_id,
        "variant_id": VARIANT,
        "sold_at": BASE + timedelta(days=day),
        "unit_price_usd": price,
        "quantity": 1,
        "transaction_value_usd": value if value is not None else price,
        "listing_title": title,
        "collector_number": COLLECTOR,
    }


def planner(row):
    """What psa10_latest_sale_quote.load_candidate_sales hands the price rule."""

    return {
        "saleObservationId": row["id"],
        "variantId": row["variant_id"],
        "soldAt": row["sold_at"],
        "unitPriceUsd": row["unit_price_usd"],
        "transactionFingerprint": f"fp{row['id']}",
    }


GOOD = "Pokemon Latias & Latios GX 170/181 Team Up PSA 10"
steady_before = [landing(100 + i, i, "17000", GOOD) for i in range(5)]
steady_after = [landing(200 + i, 30 + i, "16000", GOOD) for i in range(5)]
# 700: a $1,485 sale among ~$16-17k, title looks right -> price entry only.
# transaction_value_usd deliberately differs from unit price: the entry must
# carry the landing value, because that is what the FE subtracts.
spike = landing(700, 15, "1485", GOOD, value="2970")
# 501: wrong card number in the title AND out of band -> title must win.
both = landing(501, 16, "91", "Pokemon Tag Bolt #060/095 PSA 10")
# 502: a clean in-band sale, flagged by nothing.
clean = landing(502, 17, "16500", GOOD)
# 900: stored as a price spike on an earlier run; today no rule flags it.
flipped = landing(900, 18, "16800", GOOD)
# 901: stored with the TITLE reason; today the price rule flags it too.
stored_title = landing(901, 19, "1600", GOOD)

LANDING = steady_before + steady_after + [spike, both, clean, flipped, stored_title]
DETAILS = {row["id"]: row for row in LANDING}
TITLE_ROWS = LANDING
SALES = {VARIANT: [planner(row) for row in LANDING]}
STORED = [
    {**flipped, "reason": Q.REASON_PRICE},
    {**stored_title, "reason": Q.REASON_TITLE},
]
STAMP = "20260925T000000Z"

detail_requests: list[list[int]] = []


def detail_loader(sale_ids):
    detail_requests.append(list(sale_ids))
    return [DETAILS[sale_id] for sale_id in sale_ids if sale_id in DETAILS]


doc = Q.build_document(
    stamp=STAMP,
    title_rows=TITLE_ROWS,
    sales_by_variant=SALES,
    detail_loader=detail_loader,
    stored_rows=STORED,
)
entries = doc["entries"]
by_id = {entry["saleObservationId"]: entry for entry in entries}

# fixture has teeth: the price rule really fires on the ids the checks lean on
verdicts = Q.price_spike_verdicts(SALES)
check("fixture: price rule condemns 700, 501 and 901 and nothing clean",
      sorted(verdicts) == [501, 700, 901], str(sorted(verdicts)))
check("fixture: title rule condemns only 501",
      [entry["saleObservationId"] for entry in Q.build_receipt(TITLE_ROWS)] == [501], "")

# R1
check("R1 one entry per sale id", len(entries) == len(by_id), str([e["saleObservationId"] for e in entries]))
check("R1 title beats price on the same sale",
      by_id.get(501, {}).get("reason") == Q.REASON_TITLE, str(by_id.get(501)))
check("R1 a sale only the price rule flags is a price_isolated_spike entry",
      by_id.get(700, {}).get("reason") == Q.REASON_PRICE, str(by_id.get(700)))
check("R1 clean and steady sales are not entries",
      not ({502} | {row["id"] for row in steady_before + steady_after}) & set(by_id), str(sorted(by_id)))

# R2
check("R2 entries sorted by sale id", [e["saleObservationId"] for e in entries] == sorted(by_id), "")
again = Q.build_document(
    stamp=STAMP,
    title_rows=list(reversed(TITLE_ROWS)),
    sales_by_variant={VARIANT: list(reversed(SALES[VARIANT]))},
    detail_loader=lambda ids: list(reversed(detail_loader(ids))),
    stored_rows=list(reversed(STORED)),
)
check("R2 same inputs in any order + same stamp => byte-identical JSON",
      Q.render(doc) == Q.render(again), "")
check("R2 detail rows are asked for in sorted id order",
      detail_requests and detail_requests[0] == sorted(detail_requests[0]), str(detail_requests[:1]))

# R3
check("R3 discriminators lists every rule",
      doc.get("discriminators") == [Q.DISCRIMINATOR_TITLE, Q.DISCRIMINATOR_LISTING, Q.DISCRIMINATOR_PRICE],
      str(doc.get("discriminators")))
check("R3 old single discriminator field is kept for old readers",
      doc.get("discriminator") == Q.DISCRIMINATOR_TITLE, str(doc.get("discriminator")))
check("R3 reasons count matches entries",
      doc.get("reasons") == {Q.REASON_PRICE: 2, Q.REASON_TITLE: 2}
      and doc.get("quarantinedSales") == len(entries), f"{doc.get('reasons')} {doc.get('quarantinedSales')}")
check("R3 scan counts are stated", doc.get("scannedSales") == len(TITLE_ROWS)
      and doc.get("priceScannedSales") == len(SALES[VARIANT]), "")

# R4
check("R4 stored sale no rule flags any more stays in the receipt",
      900 in by_id, str(sorted(by_id)))
check("R4 ...with its stored reason",
      by_id.get(900, {}).get("reason") == Q.REASON_PRICE, str(by_id.get(900)))
check("R4 a stored title sale the price rule now flags keeps the stored title reason",
      by_id.get(901, {}).get("reason") == Q.REASON_TITLE, str(by_id.get(901)))
orphan = {"id": 999, "variant_id": VARIANT, "reason": Q.REASON_TITLE, "sold_at": None,
          "unit_price_usd": None, "quantity": None, "transaction_value_usd": None,
          "listing_title": None, "collector_number": COLLECTOR}
with_orphan = Q.compose_entries([], {}, [], [orphan])
check("R4 a stored row whose landing row is gone is still an entry (LEFT JOIN, not dropped)",
      [e["saleObservationId"] for e in with_orphan] == [999] and with_orphan[0]["reason"] == Q.REASON_TITLE,
      str(with_orphan))

# R5
price_entry = by_id.get(700, {})
check("R5 price entry subtracts the landing transaction value, not the unit price",
      price_entry.get("transactionValueUsd") == 2970.0 and price_entry.get("unitPriceUsd") == 1485.0,
      str(price_entry))
check("R5 price entry carries direction and both medians",
      price_entry.get("direction") == "below_band"
      and price_entry.get("priorMedianUsd") == "17000.000000"
      and price_entry.get("followingMedianUsd") is not None, str(price_entry))
check("R5 title entries keep the pre-2026-09-25 key order",
      list(by_id.get(501, {}).keys()) == LEGACY_ENTRY_KEYS, str(list(by_id.get(501, {}).keys())))
check("R5 price entries start with the same keys (readers index them)",
      list(price_entry.keys())[: len(LEGACY_ENTRY_KEYS)] == LEGACY_ENTRY_KEYS, str(list(price_entry.keys())))
check("R5 rendered receipt parses back to the same document",
      json.loads(Q.render(doc)) == doc, "")

# R6
try:
    Q.compose_entries([], {12345: {"direction": "below_band"}}, [], [])
    raised = False
except RuntimeError:
    raised = True
check("R6 condemned sale without a landing detail row raises instead of vanishing", raised, "")


# R7
class FakeCursor:
    def __init__(self, error):
        self.error = error

    def execute(self, *_args):
        raise self.error

    def fetchall(self):  # pragma: no cover - execute always raises here
        return []


check("R7 missing table (1146) = nothing stored yet",
      Q.load_stored_rows(FakeCursor(RuntimeError(1146, "Table doesn't exist"))) == [], "")
try:
    Q.load_stored_rows(FakeCursor(RuntimeError(1054, "Unknown column")))
    other_raised = False
except RuntimeError:
    other_raised = True
check("R7 any other DB error raises", other_raised, "")

print()
if FAILED:
    print(f"FAILED {len(FAILED)}: {FAILED}")
    raise SystemExit(1)
print("all pc sale quarantine receipt checks passed")
