#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The one place a PriceCharting PSA10 completed sale gets its identity.

Two writers land the same physical sales into market_sale_observation:
c11_pc_sold_ingest.py (the daily browser lane) and rebuild_036.py (the replay
leg). Each built its own fingerprint preimage, and the two disagreed on one
byte-level detail -- the price:

    c11_pc_sold_ingest.money()   -> Decimal("0.000001") quantum -> "65.000000"
    rebuild_036 (inline)         -> Decimal("0.01")     quantum -> "65.00"

Same eBay item id, same card, same day, same dollars, two fingerprints:

    pc|10026801|psa|10|2026-07-12|65.00|800321739580     -> dc0c17ba...  (run 7868)
    pc|10026801|psa|10|2026-07-12|65.000000|800321739580 -> 0334af5d...  (run 8146)

UNIQUE KEY uq_market_sale_observation is
(source_code, external_entity_id, transaction_fingerprint), so it cannot see
the collision: the fingerprints differ, so both rows are "new". Measured
2026-08-11 over the whole table: 65,845 pricecharting rows against 36,101
distinct sales in the source HTML = 1.82x, and the board's trackedSales.count
carried that inflation straight to the public page.

The canonical price text is the SIX-place form, not two. Three reasons, in
order of weight:
  1. It is what the live daily lane already emits, so every fingerprint
     already sitting in the acceptance tables and in
     data/runtime/rebuild-036/daily-sales-manifests/pc-*.jsonl stays valid.
     Canonicalising on two places would orphan them all.
  2. market_sale_observation.unit_price_usd is decimal(18,6); the fingerprint
     now says the same thing the column says.
  3. PriceCharting quotes whole cents, so the extra places are always zeros --
     nothing about the sale is lost either way.

Both call sites must go through pc_sale_fingerprint(). scripts/test_pc_sale_identity.py
fails if either one grows its own preimage again.
"""
from __future__ import annotations

import hashlib
from decimal import ROUND_HALF_UP, Decimal

# Matches market_sale_observation.unit_price_usd decimal(18,6).
SALE_PRICE_QUANTUM = Decimal("0.000001")

GRADER_CODE = "psa"
GRADE_LABEL = "10"


def pc_sale_price_text(unit_price: Decimal | float | int | str) -> str:
    """Canonical price text for both the fingerprint and the stored column."""
    return str(
        Decimal(str(unit_price)).quantize(SALE_PRICE_QUANTUM, rounding=ROUND_HALF_UP)
    )


def pc_sale_fingerprint(
    product_id: int | str,
    date_text: str,
    unit_price: Decimal | float | int | str,
    ebay_itm: str,
) -> str:
    """sha256 of the canonical preimage. No caller-supplied formatting."""
    preimage = "|".join(
        (
            "pc",
            str(int(product_id)),
            GRADER_CODE,
            GRADE_LABEL,
            str(date_text).strip(),
            pc_sale_price_text(unit_price),
            str(ebay_itm).strip(),
        )
    )
    return hashlib.sha256(preimage.encode("utf-8")).hexdigest()
