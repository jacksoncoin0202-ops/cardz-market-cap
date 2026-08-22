"""Single authority for scheduled collection adapters and their owning lane."""

from __future__ import annotations

ADAPTER_LANE = {
    "gemrate_pop": "http",
    "snk_trades": "http",
    "snk_price": "http",
    "snk_en_image": "http",
    "pc_ebay_sales": "browser",
    "en_price_ref": "browser",
}

CHECKPOINT_ADAPTERS = tuple(ADAPTER_LANE)

# Live eBay sold comps are written by C11 via PriceCharting pages (CDP 9333).
# `source_code='ebay'` is the dead G10/altxyz archive (last write 2026-08-04).
# Ranking, collect stock clocks, and EN sales authority must read this tuple
# — never `'ebay'`. 2026-08-19: a freshness key named ebay_sales queried the
# archive and an agent reported 9333 dead.
LIVE_EBAY_SOLD_SOURCE_CODES: tuple[str, ...] = ("pricecharting",)
DEAD_G10_EBAY_SOURCE_CODE = "ebay"

