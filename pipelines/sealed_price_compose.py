#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Sealed (原盒) price composer — the sealed analogue of Polaris.

Authority chains (display price):
  EN groups : accepted sold units 30d median (source ebay, n>=1)
              -> latest PriceCharting market (<=45d)
              -> latest SNK ask (<=7d)
              -> last accepted sold (any age)
              -> last market line (any age)
  JP groups : accepted sold units 30d median (snkrdunk+yahoo+mercari, n>=1)
              -> latest SNK market line (<=45d) -> PC market (<=45d)
              -> latest SNK ask (<=7d)
              -> last accepted sold (any age)
              -> last market line (any age)
  Outlier trim still needs n>=3. Ask-divergence guard only when n>=3.
Guards:
  - outlier trim per SKU: accepted 30d units outside [median/2.5, median*2.0]
    are re-marked metric_status='outlier_trimmed' (store-all + mark; reversible)
  - JP divergence guard: sold median vs SNK ask floor > 1.75x either way
    -> prefer ask, kind='ask', guard noted
Sold / market / ask never mixed silently: composed_kind records which won.

Outputs:
  market_sealed_daily_aggregate rows (full history line for FE historyDaily)
  data/runtime/operator/sealed/compose-receipt.json
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from statistics import median
from typing import Any
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

from sealed_discover_lib import same_item_ids  # noqa: E402
from sealed_runtime import OUT_DIR, db, load_env, load_sealed_products, utc_now  # noqa: E402

JP_SOLD_SOURCES = ("snkrdunk", "yahoo", "mercari")
EN_SOLD_SOURCES = ("ebay",)
# Sales carry no item id, so a sale counts only while its SKU holds an accepted freeze on the source it came off.
# eBay sales are read off the PriceCharting item page; Yahoo/Mercari sales come from a keyword search, not a bind.
SALE_FREEZE_SOURCE = {"snkrdunk": "snkrdunk", "ebay": "pricecharting"}
TRIM_HIGH = 2.0
TRIM_LOW = 2.5
GUARD_RATIO = 1.75
SOLD_MIN_N = 3
MARKET_MAX_AGE_D = 45
ASK_MAX_AGE_D = 7


def _is_jp(group_code: str) -> bool:
    return group_code.endswith("-jp")


def item_key(source: str, external_id: Any) -> frozenset:
    """One source item, every spelling of it (SNK namespaces, PC url-quoting) folded."""
    return frozenset(same_item_ids(source, unquote(str(external_id or ""))))


def load_frozen_items(cur) -> dict[tuple[int, str], frozenset]:
    """(sealed_id, source) -> the item that SKU's accepted source freeze names. The loaders below, which compose
    and export both read, keep a price row only when it comes from that item. 2026-09-23: 1,585 rows /box read
    came from items no freeze named: S10b off a rejected SNK item, OP-01 EN off the JP box, JU EN off the
    unlimited Jungle box, S5R off S5I's box, and four PC inventory prices for unreviewed binds."""
    cur.execute(
        """
        SELECT sealed_id, source_code, external_entity_id FROM operator_sealed_binding_freeze
        WHERE freeze_kind='source' AND acceptance_status='accepted'
        """
    )
    return {(int(r["sealed_id"]), str(r["source_code"])): item_key(str(r["source_code"]), r["external_entity_id"])
            for r in cur.fetchall()}


def _frozen_item(frozen: dict, row: dict) -> bool:
    key = (int(row["sealed_id"]), str(row["source_code"]))
    return key in frozen and item_key(key[1], row["external_entity_id"]) == frozen[key]


def load_sales(cur) -> dict[int, list[dict]]:
    frozen = load_frozen_items(cur)
    cur.execute(
        """
        SELECT id, sealed_id, source_code, sold_at, unit_price_usd, quantity, metric_status
        FROM market_sealed_sale_observation
        WHERE unit_price_usd IS NOT NULL AND metric_status IN ('ok','outlier_trimmed')
        """
    )
    grouped: dict[int, list[dict]] = defaultdict(list)
    for row in cur.fetchall():
        bound = SALE_FREEZE_SOURCE.get(str(row["source_code"]))
        if bound and (int(row["sealed_id"]), bound) not in frozen:
            continue
        grouped[int(row["sealed_id"])].append(dict(row))
    return grouped


def load_market(cur) -> dict[tuple[int, str], list[tuple[date, float]]]:
    frozen = load_frozen_items(cur)
    cur.execute(
        """
        SELECT sealed_id, source_code, external_entity_id, observed_date, price_usd
        FROM market_sealed_price_observation
        WHERE price_kind='market' AND price_usd IS NOT NULL AND metric_status='ok'
        ORDER BY observed_date
        """
    )
    grouped: dict[tuple[int, str], list[tuple[date, float]]] = defaultdict(list)
    for row in cur.fetchall():
        if not _frozen_item(frozen, row):
            continue
        grouped[(int(row["sealed_id"]), str(row["source_code"]))].append(
            (row["observed_date"], float(row["price_usd"]))
        )
    return grouped


def load_asks(cur) -> dict[int, tuple[date, float, float | None]]:
    frozen = load_frozen_items(cur)
    cur.execute(
        """
        SELECT sealed_id, source_code, external_entity_id, observed_date, price_usd, native_price
        FROM market_sealed_price_observation
        WHERE price_kind='ask' AND source_code='snkrdunk' AND price_usd IS NOT NULL AND metric_status='ok'
        ORDER BY observed_date
        """
    )
    latest: dict[int, tuple[date, float, float | None]] = {}
    for row in cur.fetchall():
        if not _frozen_item(frozen, row):
            continue
        latest[int(row["sealed_id"])] = (
            row["observed_date"], float(row["price_usd"]),
            float(row["native_price"]) if row["native_price"] is not None else None,
        )
    return latest


def trim_outliers(cur, sealed_id: int, sales: list[dict], today: date) -> tuple[list[dict], int]:
    """Re-mark 30d outliers; return (accepted-in-window, marked_count)."""
    window_start = today - timedelta(days=30)
    in_window = [s for s in sales if s["sold_at"].date() >= window_start]
    if len(in_window) < SOLD_MIN_N:
        return [s for s in in_window if s["metric_status"] == "ok"], 0
    units = [float(s["unit_price_usd"]) for s in in_window]
    med = median(units)
    marked = 0
    keep: list[dict] = []
    for sale in in_window:
        unit = float(sale["unit_price_usd"])
        is_outlier = unit > med * TRIM_HIGH or unit < med / TRIM_LOW
        want_status = "outlier_trimmed" if is_outlier else "ok"
        if sale["metric_status"] != want_status:
            cur.execute(
                "UPDATE market_sealed_sale_observation SET metric_status=%s WHERE id=%s",
                (want_status, int(sale["id"])),
            )
            marked += 1
        if not is_outlier:
            keep.append(sale)
    return keep, marked


def compose_current(
    *,
    group_code: str,
    kept_sales: list[dict],
    market_by_source: dict[str, list[tuple[date, float]]],
    ask: tuple[date, float, float | None] | None,
    today: date,
    all_sales: list[dict] | None = None,
) -> dict[str, Any] | None:
    jp = _is_jp(group_code)
    sold_sources = JP_SOLD_SOURCES if jp else EN_SOLD_SOURCES
    kept_ok = [
        s for s in kept_sales
        if s["source_code"] in sold_sources and s.get("unit_price_usd") is not None
    ]
    units = [float(s["unit_price_usd"]) for s in kept_ok]
    if units:
        sold_median = round(median(units), 2)
        if (
            jp and len(units) >= SOLD_MIN_N and ask is not None
            and (today - ask[0]).days <= ASK_MAX_AGE_D and ask[1] > 0
        ):
            ratio = sold_median / ask[1] if ask[1] else 1.0
            if ratio > GUARD_RATIO or ratio < 1.0 / GUARD_RATIO:
                return {"usd": ask[1], "kind": "ask", "source": "snkrdunk", "guard": f"sold_vs_ask_{ratio:.2f}x"}
        src_counts: dict[str, int] = defaultdict(int)
        for s in kept_ok:
            src_counts[s["source_code"]] += 1
        top_source = max(src_counts, key=src_counts.get)
        return {"usd": sold_median, "kind": "sold", "source": top_source, "n": len(units)}
    market_order = ("snkrdunk", "pricecharting") if jp else ("pricecharting",)
    for source in market_order:
        series = market_by_source.get(source) or []
        if series:
            d, v = series[-1]
            if (today - d).days <= MARKET_MAX_AGE_D and v > 0:
                return {"usd": round(v, 2), "kind": "market", "source": source}
    if ask is not None and (today - ask[0]).days <= ASK_MAX_AGE_D and ask[1] > 0:
        return {"usd": ask[1], "kind": "ask", "source": "snkrdunk"}
    pool = all_sales if all_sales is not None else kept_sales
    older = [
        s for s in pool
        if s.get("metric_status") == "ok"
        and s["source_code"] in sold_sources
        and s.get("unit_price_usd") is not None
    ]
    if older:
        latest = max(older, key=lambda s: s["sold_at"])
        return {
            "usd": round(float(latest["unit_price_usd"]), 2),
            "kind": "sold",
            "source": latest["source_code"],
            "n": 1,
            "note": "last_sold",
        }
    last_mkt = _last_market(market_by_source, jp=jp)
    if last_mkt:
        return last_mkt
    return None


def _last_market(
    market_by_source: dict[str, list[tuple[date, float]]],
    *,
    jp: bool,
) -> dict[str, Any] | None:
    """Stale SNK/PC market as last-known display when no sold/fresh market/ask."""
    order = ("snkrdunk", "pricecharting") if jp else ("pricecharting", "snkrdunk")
    rows: list[tuple[date, float, str]] = []
    for source in order:
        series = market_by_source.get(source) or []
        if not series:
            continue
        observed, value = series[-1]
        if value > 0:
            rows.append((observed, value, source))
    if not rows:
        return None
    observed, value, source = max(rows, key=lambda item: item[0])
    return {
        "usd": round(value, 2),
        "kind": "market",
        "source": source,
        "n": 1,
        "note": "last_market",
    }


def upsert_daily(cur, sealed_id: int, rows: list[tuple]) -> None:
    keep_dates = [row[1] for row in rows]
    if keep_dates:
        cur.execute(
            f"""
            DELETE FROM market_sealed_daily_aggregate
            WHERE sealed_id=%s AND observed_date NOT IN ({",".join(["%s"] * len(keep_dates))})
            """,
            (sealed_id, *keep_dates),
        )
    else:
        cur.execute("DELETE FROM market_sealed_daily_aggregate WHERE sealed_id=%s", (sealed_id,))
    if not rows:
        return
    cur.executemany(
        """
        INSERT INTO market_sealed_daily_aggregate
          (sealed_id, observed_date, sold_count, sold_value_usd, vwap_usd,
           composed_price_usd, composed_kind, composed_source)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
        ON DUPLICATE KEY UPDATE
          sold_count=VALUES(sold_count), sold_value_usd=VALUES(sold_value_usd),
          vwap_usd=VALUES(vwap_usd), composed_price_usd=VALUES(composed_price_usd),
          composed_kind=VALUES(composed_kind), composed_source=VALUES(composed_source)
        """,
        rows,
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true", help="persist aggregates + outlier marks")
    args = ap.parse_args()

    load_env()
    conn = db()
    today = datetime.now(timezone.utc).date()
    receipt: dict[str, Any] = {"asOf": utc_now(), "action": "sealed-price-compose", "counts": {}}
    composed_summary: list[dict] = []
    try:
        cur = conn.cursor()
        products = load_sealed_products(cur)
        sales_by_sealed = load_sales(cur)
        market_all = load_market(cur)
        asks = load_asks(cur)

        stats = {"products": len(products), "withSold30d": 0, "composed": 0, "bySource": {}, "byKind": {}, "outliersMarked": 0}
        for product in products:
            sealed_id = int(product["id"])
            group = str(product["group_code"])
            jp = _is_jp(group)
            sold_sources = JP_SOLD_SOURCES if jp else EN_SOLD_SOURCES
            sales = sales_by_sealed.get(sealed_id, [])
            kept, marked = trim_outliers(cur, sealed_id, sales, today)
            stats["outliersMarked"] += marked
            market_by_source = {
                source: market_all.get((sealed_id, source), [])
                for source in ("pricecharting", "snkrdunk")
            }

            # full daily line
            daily_sales: dict[date, list[dict]] = defaultdict(list)
            for sale in sales:
                if sale["metric_status"] == "ok" and sale["source_code"] in sold_sources:
                    daily_sales[sale["sold_at"].date()].append(sale)
            market_line: dict[date, tuple[float, str]] = {}
            order = ("pricecharting", "snkrdunk") if not jp else ("snkrdunk", "pricecharting")
            for source in reversed(order):
                for d, v in market_by_source.get(source) or []:
                    market_line[d] = (v, source)
            all_dates = sorted(set(daily_sales) | set(market_line))
            rows = []
            for d in all_dates:
                day_sales = daily_sales.get(d, [])
                count = sum(int(s["quantity"] or 1) for s in day_sales)
                value = round(sum(float(s["unit_price_usd"]) * int(s["quantity"] or 1) for s in day_sales), 2)
                vwap = round(value / count, 2) if count else None
                day_units = [float(s["unit_price_usd"]) for s in day_sales]
                if day_units:
                    composed = (round(median(day_units), 2), "sold", day_sales[0]["source_code"])
                elif d in market_line:
                    composed = (round(market_line[d][0], 2), "market", market_line[d][1])
                else:
                    composed = (None, None, None)
                rows.append((sealed_id, d.isoformat(), count, value if count else None, vwap, composed[0], composed[1], composed[2]))
            if args.write:
                upsert_daily(cur, sealed_id, rows)

            current = compose_current(
                group_code=group,
                kept_sales=kept,
                market_by_source=market_by_source,
                ask=asks.get(sealed_id),
                today=today,
                all_sales=sales,
            )
            if kept:
                stats["withSold30d"] += 1
            if current:
                stats["composed"] += 1
                stats["bySource"][current["source"]] = stats["bySource"].get(current["source"], 0) + 1
                stats["byKind"][current["kind"]] = stats["byKind"].get(current["kind"], 0) + 1
                composed_summary.append({"sku": product["sku_id"], "slug": product["slug"], **current})
        if args.write:
            conn.commit()
        else:
            conn.rollback()
        receipt["counts"] = stats
        receipt["mode"] = "write" if args.write else "dry-run"
        receipt["composedTop"] = sorted(composed_summary, key=lambda x: -x["usd"])[:25]
    finally:
        conn.close()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / "compose-receipt.json"
    out.write_text(json.dumps(receipt, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps(receipt, ensure_ascii=False, indent=2, default=str))
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
