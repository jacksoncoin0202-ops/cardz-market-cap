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

