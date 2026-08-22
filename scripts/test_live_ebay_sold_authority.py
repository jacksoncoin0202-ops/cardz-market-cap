#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Live eBay solds are C11 pricecharting rows. G10 source_code='ebay' is dead.

2026-08-19: freshness named ebay_sales queried the archive; then latest_prices
and collect stock clocks still read source_code='ebay'. DADDY: PC 腳本就係
eBay 來源. One tuple, every daily reader.
"""
from __future__ import annotations

import inspect
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

from collection_contract import (  # noqa: E402
    DEAD_G10_EBAY_SOURCE_CODE,
    LIVE_EBAY_SOLD_SOURCE_CODES,
)
from current_quote_revision import eligible_current_quote_revision_ddl  # noqa: E402
import operator_control as OC  # noqa: E402
from pc_psa10_price_derivation import load_pc_sales, select_ebay_median  # noqa: E402

FAILED: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    if ok:
        print(f"ok   {label}")
    else:
        FAILED.append(label)
        print(f"FAIL {label}{': ' + detail if detail else ''}")


def assert_no_dead_g10_ebay_sql(sql: str, *, label: str) -> None:
    compact = sql.replace(" ", "").replace('"', "'")
    if "source_code='ebay'" in compact or 'source_codes=("ebay"' in compact.replace("'", '"'):
        raise AssertionError(f"{label} still queries dead G10 source_code='ebay'")


# Negative: the exact 2026-08-19 lying call.
try:
    assert_no_dead_g10_ebay_sql(
        'source_codes=("ebay",)',
        label="poison latest_prices",
    )
except AssertionError as error:
    check("dead source_codes=('ebay',) fires", "ebay" in str(error), str(error))
else:
    check("dead source_codes=('ebay',) fires", False, "poison was accepted")

try:
    assert_no_dead_g10_ebay_sql(
        "AND source_code='ebay'",
        label="poison load_pc_sales",
    )
except AssertionError as error:
    check("dead load_pc_sales SQL fires", "ebay" in str(error), str(error))
else:
    check("dead load_pc_sales SQL fires", False, "poison SQL was accepted")

check(
    "live sold tuple is C11 pricecharting only",
    LIVE_EBAY_SOLD_SOURCE_CODES == ("pricecharting",),
    str(LIVE_EBAY_SOLD_SOURCE_CODES),
)
check(
    "dead archive code is named, not used as live",
    DEAD_G10_EBAY_SOURCE_CODE == "ebay"
    and DEAD_G10_EBAY_SOURCE_CODE not in LIVE_EBAY_SOLD_SOURCE_CODES,
)

prices_src = inspect.getsource(OC.latest_prices)
check(
    "latest_prices uses LIVE_EBAY_SOLD_SOURCE_CODES",
    "LIVE_EBAY_SOLD_SOURCE_CODES" in prices_src,
)
check(
    "latest_prices EN sales does not hard-code source_codes=('ebay',)",
    'source_codes=("ebay"' not in prices_src.replace(" ", ""),
)
check(
    "latest_prices EN fallback is pricecharting, not ebay-then-PC",
    '("ebay", "pricecharting")' not in prices_src
    and "('ebay', 'pricecharting')" not in prices_src,
)

sales_src = inspect.getsource(OC._sales_unit_authority)
check(
    "sales composer documents C11 not G10 ebay",
    "pricecharting" in sales_src and "dead G10" in sales_src,
)

collect_src = (ROOT / "pipelines" / "collect_control.py").read_text(encoding="utf-8")
check(
    "collect sold clock iterates LIVE_EBAY_SOLD_SOURCE_CODES",
    "for sc in LIVE_EBAY_SOLD_SOURCE_CODES:" in collect_src,
)
check(
    "collect sold clock no longer reads s.get('ebay')",
    'ebay_sale_max = (s.get("ebay")' not in collect_src,
)

derive_sql = inspect.getsource(load_pc_sales)
assert_no_dead_g10_ebay_sql(derive_sql, label="load_pc_sales")
check("load_pc_sales SQL uses live sold tuple", "LIVE_EBAY_SOLD_SOURCE_CODES" in derive_sql)

median_src = inspect.getsource(select_ebay_median)
check(
    "ebay median selector accepts live C11 solds",
    "LIVE_EBAY_SOLD_SOURCE_CODES" in median_src,
)
check(
    "ebay median selector no longer requires source_code==ebay",
    "!= SOURCE_EBAY" not in median_src,
)

ddl = eligible_current_quote_revision_ddl()
check(
    "daily ranking view still excludes G10 ebay quotes",
    "q.source_code IN ('snkrdunk','snk_psa10','snk','pricecharting')" in ddl.replace(" ", "")
    or "q.source_code IN ('snkrdunk','snk_psa10','snk','pricecharting')" in ddl,
)
check("'ebay' is not an eligible quote source", "'ebay'" not in ddl)

if FAILED:
    print("FAILED: " + "; ".join(FAILED))
    raise SystemExit(1)
print("POSITIVE_OK live eBay sold authority is C11 pricecharting")
