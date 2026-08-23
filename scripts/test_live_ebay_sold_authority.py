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

# 2026-08-23：operator_control 嗰個 chart 價 composer（latest_prices 一家）已經
# 刪咗 —— run_daily 封存之後佢一直零 call site，而佢係由 market_price_observation
# 嘅 chart 點砌價，唔經 current_quote_revision.assert_quote_mint_allowed（F-MINT）。
# 排名價而家係 psa10_latest_sale_quote 出嘅最新真成交。所以呢度由「檢查佢讀邊個
# source」變成「檢查佢冇返生」：一返生就即係多咗一條唔經 F-MINT 嘅 chart 價路。
# 用 hasattr 唔用 source grep：留喺原位嘅 tombstone 註釋提到啲名都唔會假綠。
RETIRED_CHART_COMPOSER_ATTRS = (
    "latest_prices",
    "_trim_mean_prices",
    "_pick_prefer_sources",
    "_sales_unit_authority",
    "_latest_price_rows",
    "_variant_languages",
)
for attr in RETIRED_CHART_COMPOSER_ATTRS:
    check(f"chart 價 composer operator_control.{attr} 冇返生", not hasattr(OC, attr))
# 反假綠：module 真係載入到先算。import 炸咗嘅話上面每一個 hasattr 都會「啱」。
check(
    "operator_control 真係載入到（唔係全部 hasattr 假綠）",
    hasattr(OC, "image_rows") and hasattr(OC, "_is_canonical_price_route"),
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
