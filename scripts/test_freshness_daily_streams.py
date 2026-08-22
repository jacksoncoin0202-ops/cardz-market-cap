#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Daily freshness must not dress the dead G10 eBay archive as the 9333 lane.

2026-08-19: streams.ebay_sales queried source_code='ebay' (G10/altxyz, last
write 2026-08-04). The live eBay-sold path is C11 via PriceCharting CDP 9333
and writes source_code='pricecharting'. An agent read the lying key and told
DADDY the 9333 lane was dead.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

from collect_control import DAILY_FRESHNESS_STREAMS, PC_SALES_LISTING_SQL  # noqa: E402

FAILED: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    if ok:
        print(f"ok   {label}")
    else:
        FAILED.append(label)
        print(f"FAIL {label}{': ' + detail if detail else ''}")


def assert_daily_freshness_streams(streams: tuple[tuple[str, str], ...]) -> None:
    names = [name for name, _sql in streams]
    if "ebay_sales" in names or "ebay_price" in names:
        raise AssertionError(
            "dead G10 keys ebay_sales/ebay_price must not appear in daily freshness"
        )
    if "pc_sales" not in names:
        raise AssertionError("daily freshness must expose pc_sales")
    if "pc_price" not in names:
        raise AssertionError("daily freshness must expose pc_price")
    for name, sql in streams:
        compact = sql.replace(" ", "").replace('"', "'")
        if "source_code='ebay'" in compact:
            raise AssertionError(
                f"{name} queries source_code='ebay' — that is the dead G10 archive"
            )
    pc_sql = next(sql for name, sql in streams if name == "pc_sales")
    if "sold_at" in pc_sql:
        raise AssertionError(
            "pc_sales SLA must not use sold_at — listing midnight goes red overnight"
        )
    if "fetched_at" not in pc_sql or "pricecharting" not in pc_sql:
        raise AssertionError(
            "pc_sales SLA clock must be MAX(fetched_at) on source_code='pricecharting'"
        )


# Negative: the exact 2026-08-19 lying pair must fire.
poison = DAILY_FRESHNESS_STREAMS + (
    (
        "ebay_sales",
        "SELECT MAX(sold_at) m, COUNT(*) n FROM market_sale_observation WHERE source_code='ebay'",
    ),
)
try:
    assert_daily_freshness_streams(poison)
except AssertionError as error:
    check("dead ebay_sales key fires", "ebay_sales" in str(error), str(error))
else:
    check("dead ebay_sales key fires", False, "poison tuple was accepted")

poison_sql = (
    (
        "pc_sales",
        "SELECT MAX(sold_at) m, COUNT(*) n FROM market_sale_observation WHERE source_code='pricecharting'",
    ),
    (
        "pc_price",
        "SELECT MAX(effective_at) m, COUNT(*) n FROM market_price_observation WHERE source_code='pricecharting'",
    ),
)
try:
    assert_daily_freshness_streams(poison_sql)
except AssertionError as error:
    check("sold_at-as-SLA fires", "sold_at" in str(error), str(error))
else:
    check("sold_at-as-SLA fires", False, "sold_at SLA was accepted")

assert_daily_freshness_streams(DAILY_FRESHNESS_STREAMS)
check("live daily streams pass the gate", True)
check(
    "listing day is a sidecar, not the SLA clock",
    "sold_at" in PC_SALES_LISTING_SQL and "fetched_at" not in PC_SALES_LISTING_SQL,
)

src = (ROOT / "pipelines" / "collect_control.py").read_text(encoding="utf-8")
check(
    "freshness_summary iterates DAILY_FRESHNESS_STREAMS",
    "queries = list(DAILY_FRESHNESS_STREAMS)" in src,
)

if FAILED:
    print("FAILED: " + "; ".join(FAILED))
    raise SystemExit(1)
print("POSITIVE_OK daily freshness no longer aliases G10 ebay as the 9333 lane")
