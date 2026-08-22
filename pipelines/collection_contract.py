"""Single authority for scheduled collection adapters and their owning lane."""

from __future__ import annotations

# F-COLLECT-DISPATCH: one registry, not a lane table plus three `if adapter ==`
# chains in collect_control. `lane` picks the automatic chain, `runner` is the
# collect_control function name that actually collects it, `kind` is what the
# lane writes. Adding a source = one entry here + that runner; a source that is
# not in this dict is dispatched as `source_not_registered`, never skipped.
SOURCE_ADAPTERS: dict[str, dict[str, str]] = {
    "gemrate_pop": {"lane": "http", "runner": "run_gemrate_pop", "kind": "population"},
    "snk_trades": {"lane": "http", "runner": "run_snk_trades", "kind": "sales"},
    "snk_price": {"lane": "http", "runner": "run_snk_price", "kind": "price"},
    "snk_en_image": {"lane": "http", "runner": "run_snk_en_image", "kind": "image"},
    "pc_ebay_sales": {"lane": "browser", "runner": "run_pc_ebay_sales", "kind": "sales"},
    "en_price_ref": {"lane": "browser", "runner": "run_en_price_ref", "kind": "price"},
}

SOURCE_NOT_REGISTERED = "source_not_registered"

ADAPTER_LANE = {key: spec["lane"] for key, spec in SOURCE_ADAPTERS.items()}

CHECKPOINT_ADAPTERS = tuple(SOURCE_ADAPTERS)


def adapter_spec(source_key: str) -> dict[str, str] | None:
    """Registry row for a source key, or None when it was never registered."""

    return SOURCE_ADAPTERS.get(str(source_key))


def adapter_lane(source_key: str) -> str | None:
    spec = adapter_spec(source_key)
    return spec["lane"] if spec else None


def adapter_runner_name(source_key: str) -> str | None:
    spec = adapter_spec(source_key)
    return spec["runner"] if spec else None

# Live eBay sold comps are written by C11 via PriceCharting pages (CDP 9333).
# `source_code='ebay'` is the dead G10/altxyz archive (last write 2026-08-04).
# Ranking, collect stock clocks, and EN sales authority must read this tuple
# — never `'ebay'`. 2026-08-19: a freshness key named ebay_sales queried the
# archive and an agent reported 9333 dead.
LIVE_EBAY_SOLD_SOURCE_CODES: tuple[str, ...] = ("pricecharting",)
DEAD_G10_EBAY_SOURCE_CODE = "ebay"

