#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Sealed (原盒) stock / incremental collectors.

Adapters (checkpointed in market_ingest_checkpoint under these source codes):
  sealed_pc      PriceCharting /game page -> Ungraded chart (market series)
                 + completed-auctions-used rows (eBay sold prints)
  sealed_snk     SNKRDUNK JSON API -> ask floor + provider daily line + box trades
  sealed_yahoo   Yahoo Auctions closedsearch (落札) -> JP sold prints
  sealed_ebay    import-only: verified completed-sales export file (fails closed
                 without --input; PC pages already feed source_code='ebay')
  sealed_mercari no transport yet; fails closed with reason

Doctrine: exact-bound only (accepted source freeze; --allow-candidates for
engineering runs), store-all + mark, sold/ask/market never mixed, missing
stays missing.

Usage:
  python -X utf8 pipelines/sealed_collect.py status
  python -X utf8 pipelines/sealed_collect.py stock --adapter sealed_snk --limit 5 --allow-candidates
  python -X utf8 pipelines/sealed_collect.py incr  --adapter all --limit 40
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

from sealed_discover_lib import SNK_ITEM_RE, yahoo_closedsearch_url, yahoo_jp_query, yahoo_query_needs_rewrite  # noqa: E402
from sealed_runtime import (  # noqa: E402
    HTML_DIR,
    OUT_DIR,
    PRICE_UPSERT_HEAD,
    SEALED_ADAPTERS,
    SLA_HOURS,
    checkpoint_age_hours,
    db,
    fx_units_per_usd,
    insert_sealed_sale,
    load_env,
    load_sealed_bindings,
    load_sealed_checkpoints,
    load_sealed_hints,
    qc_box_title,
    record_sealed_run,
    sha,
    stream_key,
    to_usd,
    upsert_sealed_price,
    utc_naive,
    utc_now,
    warehouse_sealed,
)

COLLECT_OUT = OUT_DIR / "collect"

SNK_QTY_LABEL = re.compile(r"^(\d+)\s*(?:枚|箱|BOX|box)$", re.I)


def _run_key(adapter: str, mode: str) -> str:
    return f"{adapter}-{mode}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}"


# --- selection ---------------------------------------------------------------


def _sealed_ids_with_data(cur, adapter: str) -> set[int]:
    if adapter == "sealed_pc":
        cur.execute(
            "SELECT DISTINCT sealed_id AS s FROM market_sealed_price_observation WHERE source_code='pricecharting'"
        )
    elif adapter == "sealed_snk":
        cur.execute(
            "SELECT DISTINCT sealed_id AS s FROM market_sealed_price_observation WHERE source_code='snkrdunk'"
        )
    elif adapter == "sealed_yahoo":
        cur.execute("SELECT DISTINCT sealed_id AS s FROM market_sealed_sale_observation WHERE source_code='yahoo'")
    else:
        return set()
    return {int(r["s"]) for r in cur.fetchall()}


def _load_adapter_items(cur, adapter: str, *, allow_candidates: bool) -> list[dict]:
    if adapter == "sealed_pc":
        rows = load_sealed_bindings(cur, source_code="pricecharting", require_accepted=not allow_candidates)
        return [
            {
                "sealedId": int(r["sealed_id"]),
                "externalId": r["external_entity_id"],
                "url": r["canonical_url"] or f"https://www.pricecharting.com/game/{r['external_entity_id']}",
                "sku": r["sku_id"],
                "lang": r["lang"],
                "group": r["group_code"],
                "nameEn": r["name_en"],
            }
            for r in rows
        ]
    if adapter == "sealed_snk":
        rows = load_sealed_bindings(cur, source_code="snkrdunk", require_accepted=not allow_candidates)
        items = []
        for r in rows:
            m = SNK_ITEM_RE.match(r["external_entity_id"])
            if not m:
                continue
            items.append(
                {
                    "sealedId": int(r["sealed_id"]),
                    "externalId": r["external_entity_id"],
                    "itemId": int(m.group(1)),
                    "sku": r["sku_id"],
                    "lang": r["lang"],
                    "group": r["group_code"],
                    "nameEn": r["name_en"],
                }
            )
        return items
    if adapter == "sealed_yahoo":
        rows = load_sealed_hints(cur, source_code="yahoo", hint_kinds=("search",))
        seen: set[int] = set()
        items = []
        for r in rows:
            sid = int(r["sealed_id"])
            if sid in seen:
                continue
            seen.add(sid)
            url = r["url"]
            lang = str(r["lang"] or "").lower()
            if yahoo_query_needs_rewrite(url, lang):
                url = yahoo_closedsearch_url(
                    yahoo_jp_query(r.get("name_jp"), r.get("name_en"), str(r.get("print_wave") or "std"), str(r.get("group_code") or ""))
                )
            items.append(
                {
                    "sealedId": sid,
                    "externalId": "closedsearch",
                    "url": url,
                    "sku": r["sku_id"],
                    "lang": r["lang"],
                    "group": r["group_code"],
                    "nameEn": r["name_en"],
                }
            )
        cur.execute(
            """
            SELECT id, sku_id, group_code, lang, name_en, name_jp, print_wave
            FROM catalog_sealed_product
            WHERE status='active' AND LOWER(lang)='jp'
            """
        )
        for r in cur.fetchall():
            sid = int(r["id"])
            if sid in seen:
                continue
            seen.add(sid)
            items.append(
                {
                    "sealedId": sid,
                    "externalId": "closedsearch",
                    "url": yahoo_closedsearch_url(
                        yahoo_jp_query(r.get("name_jp"), r.get("name_en"), str(r.get("print_wave") or "std"), str(r.get("group_code") or ""))
                    ),
                    "sku": r["sku_id"],
                    "lang": r["lang"],
                    "group": r["group_code"],
                    "nameEn": r["name_en"],
                }
            )
        return items
    return []


def _fetch_key(adapter: str, item: dict) -> str:
    """What the adapter really fetches. SNK drops trading-cards:/apparel-groups:/apparels: and GETs
    /v1/apparels/{itemId}, so 'trading-cards:767625' and 'apparels:767625' are one box."""
    if adapter == "sealed_snk":
        return f"snkrdunk:{item['itemId']}"
    return str(item.get("url") or item["externalId"]).lower()


def _shared_keys(adapter: str, items: list[dict]) -> dict[str, list[str]]:
    owners: dict[str, dict[int, str]] = {}
    for item in items:
        owners.setdefault(_fetch_key(adapter, item), {})[item["sealedId"]] = item["sku"]
    return {key: sorted(skus.values()) for key, skus in owners.items() if len(skus) > 1}


def _select(cur, adapter: str, mode: str, *, limit: int | None, allow_candidates: bool,
            force: bool) -> tuple[list[dict], list[dict], dict[str, list[str]]]:
    """(selected, blocked, shared). shared = fetch keys bound to 2+ SKUs. stock skips a due item whose key is
    shared: the first pull would copy another SKU's box into it (2026-09-23: EB-05 EN sat on the EB-03 EN SNK
    item). Which SKU owns the item is a human ruling. incr keeps refreshing SKUs that already have data and
    only lists the sharing."""
    items = _load_adapter_items(cur, adapter, allow_candidates=allow_candidates)
    shared = _shared_keys(adapter, items)
    have = _sealed_ids_with_data(cur, adapter)
    checkpoints = load_sealed_checkpoints(cur, adapter)
    selected, blocked = [], []
    for item in items:
        has_data = item["sealedId"] in have
        age = checkpoint_age_hours(checkpoints, stream_key(item["sealedId"], item["externalId"]))
        if mode == "stock":
            due = not has_data
        elif adapter == "sealed_pc":
            # Same rule as PSA10 PC: sold table is 30 rows. No cooldown.
            due = has_data
        else:
            due = has_data and (force or age is None or age > SLA_HOURS)
        key = _fetch_key(adapter, item)
        if due and mode == "stock" and key in shared:
            blocked.append({"sku": item["sku"], "key": key, "sharedWith": [s for s in shared[key] if s != item["sku"]]})
        elif due:
            selected.append(item)
    if limit:
        selected = selected[:limit]
    return selected, blocked, shared


# --- sealed_pc ---------------------------------------------------------------


def run_pc(conn, items: list[dict], *, mode: str, html_max_age_h: float, timeout_s: int) -> dict:
    import pricecharting_cf_session as cf
    from pricecharting_page_parse import parse_product_html

    cur = conn.cursor()
    run_key = _run_key("sealed_pc", mode)
    ok_items: list[dict] = []
    results: list[dict] = []
    for item in items:
        url = item["url"]
        digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:10]
        out = HTML_DIR / f"{item['sealedId']}_{digest}.html"
        res: dict[str, Any] = {"sku": item["sku"], "url": url}
        try:
            fresh = out.exists() and (utc_naive().timestamp() - out.stat().st_mtime) / 3600.0 < html_max_age_h
            if fresh and "VGPC" not in out.read_text(encoding="utf-8", errors="replace")[:200000]:
                fresh = False  # cached file is a CF challenge or junk page
            if not fresh:
                code = cf.cmd_fetch(url, out, headless=True, timeout_s=timeout_s)
                if code != 0:
                    res["status"] = "fetch_failed" if code != 4 else "terminal_404"
                    results.append(res)
                    continue
            parsed = parse_product_html(out.read_text(encoding="utf-8", errors="replace"), source_url=url)
            if not parsed.get("ok"):
                res["status"] = "parse_failed"
                results.append(res)
                continue
            used = (parsed.get("chart") or {}).get("used") or {}
            series = used.get("series") or []
            price_rows = []
            for point in series:
                if not (isinstance(point, list) and len(point) >= 2):
                    continue
                ts, cents = point[0], point[1]
                if not cents:
                    continue
                day = datetime.fromtimestamp(ts / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
                usd = round(float(cents) / 100.0, 2)
                price_rows.append(
                    (item["sealedId"], "pricecharting", "market", day, usd, "USD", usd,
                     item["externalId"], url[:700], "ok", run_key)
                )
            if price_rows:
                cur.executemany(
                    """
                    INSERT INTO market_sealed_price_observation
                      (sealed_id, source_code, price_kind, observed_date, native_price, native_currency,
                       price_usd, external_entity_id, source_url, metric_status, ingest_run_key)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON DUPLICATE KEY UPDATE
                      """ + PRICE_UPSERT_HEAD + """, native_price=VALUES(native_price), price_usd=VALUES(price_usd),
                      ingest_run_key=VALUES(ingest_run_key)
                    """,
                    price_rows,
                )
            sold_rows = ((parsed.get("sales") or {}).get("completed-auctions-used") or {}).get("rows") or []
            inserted_sales = 0
            for row in sold_rows:
                price = row.get("price_usd")
                day = row.get("date")
                if price is None or not day:
                    continue
                qc = qc_box_title(row.get("title") or "")
                qty = qc["quantity"]
                unit = round(float(price) / qty, 2) if qty >= 1 else float(price)
                lot = f"itm:{row['ebay_itm']}" if row.get("ebay_itm") else "sha:" + sha(row)[:32]
                inserted_sales += insert_sealed_sale(
                    cur,
                    sealed_id=item["sealedId"],
                    source_code="ebay",
                    lot_id=lot,
                    sold_at=f"{day} 00:00:00",
                    unit_price_usd=unit if qc["accepted"] else None,
                    native_price=float(price),
                    native_currency="USD",
                    quantity=qty,
                    total_native_price=float(price),
                    box_condition=qc["condition"],
                    title=row.get("title"),
                    raw_url=row.get("ebay_url"),
                    metric_status="ok" if qc["accepted"] else f"rejected_{qc['reason']}",
                    parser="pc_page_v1",
                    ingest_run_key=run_key,
                )
            product = parsed.get("product") or {}
            warehouse_sealed(
                cur,
                sealed_id=item["sealedId"],
                source_code="pricecharting",
                external_entity_id=item["externalId"],
                observation_kind="sealed_pc_page",
                payload={
                    "product": product,
                    "usedPoints": used.get("points"),
                    "usedLastUsd": used.get("last_usd"),
                    "soldRows": len(sold_rows),
                    "url": url,
                },
                ingest_run_key=run_key,
            )
            conn.commit()
            res.update({"status": "ok", "pricePoints": len(price_rows), "soldRows": len(sold_rows), "salesInserted": inserted_sales})
            ok_items.append(item)
        except Exception as exc:  # noqa: BLE001
            conn.rollback()
            res.update({"status": "error", "error": f"{type(exc).__name__}:{exc}"})
        results.append(res)
    receipt = record_sealed_run(conn, adapter="sealed_pc", mode=mode, items=ok_items, payload=results, started_at=utc_naive())
    conn.commit()
    return {"adapter": "sealed_pc", "mode": mode, "attempted": len(items), "ok": len(ok_items), "run": receipt, "items": results}


# --- sealed_snk ---------------------------------------------------------------


def _snk_trade_quantity(trade: dict) -> int | None:
    quantity = trade.get("quantity")
    if isinstance(quantity, int) and not isinstance(quantity, bool) and quantity > 0:
        return quantity
    match = SNK_QTY_LABEL.fullmatch(str(trade.get("label") or "").strip())
    if match:
        return int(match.group(1))
    return None


def run_snk(conn, items: list[dict], *, mode: str, delay: float) -> dict:
    from snkrdunk_bulk import SnkrdunkApi

    api = SnkrdunkApi(delay=delay)
    cur = conn.cursor()
    jpy_per_usd = fx_units_per_usd(cur, "JPY")
    run_key = _run_key("sealed_snk", mode)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    ok_items: list[dict] = []
    results: list[dict] = []
    for item in items:
        res: dict[str, Any] = {"sku": item["sku"], "itemId": item["itemId"]}
        try:
            master = api.get_master(item["itemId"])
            pcid = master.get("productCatalogId")
            ask_jpy = master.get("usedMinPrice")
            if isinstance(ask_jpy, (int, float)) and ask_jpy > 0:
                upsert_sealed_price(
                    cur,
                    sealed_id=item["sealedId"],
                    source_code="snkrdunk",
                    price_kind="ask",
                    observed_date=today,
                    native_price=float(ask_jpy),
                    native_currency="JPY",
                    price_usd=to_usd(float(ask_jpy), "JPY", jpy_per_usd),
                    external_entity_id=item["externalId"],
                    source_url=f"https://snkrdunk.com/en/trading-cards/{item['itemId']}",
                    ingest_run_key=run_key,
                )
            history: dict = {}
            trades: list[dict] = []
            points: list[dict] = []
            if pcid:
                history = api.get_trading_history(int(pcid), range_="all", condition_code=None)
                trades = history.get("trades") or []
                lines = ((history.get("chart") or {}).get("lines") or [])
                points = (lines[0].get("points") or []) if lines else []
            price_rows = []
            for point in points:
                ts = point.get("timestamp") if isinstance(point, dict) else None
                price = point.get("price") if isinstance(point, dict) else None
                if not isinstance(ts, (int, float)) or not isinstance(price, (int, float)) or price <= 0:
                    continue
                day = datetime.fromtimestamp(ts / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
                price_rows.append(
                    (item["sealedId"], "snkrdunk", "market", day, float(price), "JPY",
                     to_usd(float(price), "JPY", jpy_per_usd), item["externalId"], None, "ok", run_key)
                )
            if price_rows:
                cur.executemany(
                    """
                    INSERT INTO market_sealed_price_observation
                      (sealed_id, source_code, price_kind, observed_date, native_price, native_currency,
                       price_usd, external_entity_id, source_url, metric_status, ingest_run_key)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON DUPLICATE KEY UPDATE
                      """ + PRICE_UPSERT_HEAD + """, native_price=VALUES(native_price), price_usd=VALUES(price_usd),
                      ingest_run_key=VALUES(ingest_run_key)
                    """,
                    price_rows,
                )
            inserted_sales = 0
            for trade in trades:
                if not isinstance(trade, dict):
                    continue
                sold_at = trade.get("soldAt")
                total = trade.get("price")
                if not isinstance(sold_at, str) or "T" not in sold_at or not isinstance(total, (int, float)) or total <= 0:
                    continue
                qty = _snk_trade_quantity(trade)
                tx = trade.get("transactionId")
                lot = f"tx:{tx}" if tx else "sha:" + sha({"soldAt": sold_at, "price": total, "label": trade.get("label")})[:32]
                sold_dt = sold_at.replace("T", " ").replace("Z", "")[:26]
                unit_jpy = (float(total) / qty) if qty else None
                inserted_sales += insert_sealed_sale(
                    cur,
                    sealed_id=item["sealedId"],
                    source_code="snkrdunk",
                    lot_id=lot,
                    sold_at=sold_dt,
                    unit_price_usd=to_usd(unit_jpy, "JPY", jpy_per_usd) if unit_jpy else None,
                    native_price=unit_jpy,
                    native_currency="JPY",
                    quantity=qty or 1,
                    total_native_price=float(total),
                    box_condition="unknown",
                    title=str(trade.get("label") or "") or None,
                    raw_url=None,
                    metric_status="ok" if qty else "unlabeled_qty",
                    parser="snk_history_v1",
                    ingest_run_key=run_key,
                )
            warehouse_sealed(
                cur,
                sealed_id=item["sealedId"],
                source_code="snkrdunk",
                external_entity_id=item["externalId"],
                observation_kind="sealed_snk_history",
                payload={"master": master, "history": history},
                ingest_run_key=run_key,
            )
            conn.commit()
            res.update({"status": "ok", "points": len(price_rows), "trades": len(trades), "salesInserted": inserted_sales, "askJpy": ask_jpy})
            ok_items.append(item)
        except Exception as exc:  # noqa: BLE001
            conn.rollback()
            res.update({"status": "error", "error": f"{type(exc).__name__}:{exc}"})
        results.append(res)
    receipt = record_sealed_run(conn, adapter="sealed_snk", mode=mode, items=ok_items, payload=results, started_at=utc_naive())
    conn.commit()
    return {"adapter": "sealed_snk", "mode": mode, "attempted": len(items), "ok": len(ok_items), "run": receipt, "items": results}


# --- sealed_yahoo ---------------------------------------------------------------


YAHOO_NEXT_DATA_RE = re.compile(
    r'<script id="__NEXT_DATA__" type="application/json"[^>]*>(.*?)</script>', re.S
)


def _yahoo_rows(html: str) -> list[dict]:
    """Parse closedsearch __NEXT_DATA__ items. bidCount>=1 == actually sold."""
    match = YAHOO_NEXT_DATA_RE.search(html)
    if not match:
        return []
    try:
        data = json.loads(match.group(1))
        items = data["props"]["pageProps"]["initialState"]["search"]["items"]["listing"]["items"]
    except (KeyError, TypeError, json.JSONDecodeError):
        return []
    rows = []
    for item in items:
        if not isinstance(item, dict):
            continue
        auction_id = item.get("auctionId")
        price = item.get("price")
        end_time = str(item.get("endTime") or "")
        if not auction_id or not isinstance(price, (int, float)) or price <= 0 or not end_time:
            continue
        if int(item.get("bidCount") or 0) < 1:
            continue
        try:
            sold_dt = datetime.fromisoformat(end_time).astimezone(timezone.utc).replace(tzinfo=None)
        except ValueError:
            continue
        rows.append(
            {
                "auctionId": str(auction_id),
                "title": str(item.get("title") or ""),
                "priceJpy": float(price),
                "soldAt": sold_dt.strftime("%Y-%m-%d %H:%M:%S"),
                "bidCount": int(item.get("bidCount") or 0),
                "isFixedPrice": bool(item.get("isFixedPrice")),
            }
        )
    return rows


def _group_name_map(cur) -> dict[str, list[tuple[int, str | None, str | None]]]:
    # a no-box set (S8a 25th ANNIVERSARY COLLECTION) sells no box to price, but its name in a title still names
    # another product: '25th ANNIVERSARY COLLECTION & ロストアビス 2BOXセット' is no S11 box.
    cur.execute("SELECT id, group_code, name_en, name_jp FROM catalog_sealed_product")
    grouped: dict[str, list[tuple[int, str | None, str | None]]] = {}
    for r in cur.fetchall():
        grouped.setdefault(str(r["group_code"]), []).append((int(r["id"]), r["name_en"], r["name_jp"]))
    return grouped


def set_names(group_names: dict[str, list[tuple[int, str | None, str | None]]], group: str,
              sealed_id: int) -> tuple[list[str], list[str]]:
    """A Yahoo title's set-name judge: own = the SKU's EN and JP names, foreign = every other box's JP name in its
    group. run_yahoo and sealed_operator.cmd_sealed_rejudge_sales both read it, so old and new rows see one rule."""
    peers = group_names.get(group, [])
    return ([n for sid, en, jp in peers if sid == sealed_id for n in (en, jp) if n],
            [jp for sid, en, jp in peers if sid != sealed_id and jp])


def run_yahoo(conn, items: list[dict], *, mode: str, delay: float) -> dict:
    import time

    import requests

    session = requests.Session()
    session.headers["User-Agent"] = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/126.0.0.0 Safari/537.36"
    )
    from sealed_runtime import title_set_contamination

    cur = conn.cursor()
    jpy_per_usd = fx_units_per_usd(cur, "JPY")
    group_names = _group_name_map(cur)
    run_key = _run_key("sealed_yahoo", mode)
    ok_items: list[dict] = []
    results: list[dict] = []
    for item in items:
        res: dict[str, Any] = {"sku": item["sku"], "url": item["url"]}
        own_names, foreign_names = set_names(group_names, item["group"], item["sealedId"])
        try:
            response = session.get(item["url"], timeout=30)
            response.raise_for_status()
            rows = _yahoo_rows(response.text)
            if not rows:
                res["status"] = "no_rows_parsed"
                results.append(res)
                time.sleep(delay)
                continue
            inserted = 0
            accepted = 0
            for row in rows:
                qc = qc_box_title(row["title"])
                if qc["accepted"]:
                    contamination = title_set_contamination(row["title"], own_names, foreign_names)
                    if not contamination["accepted"]:
                        qc = {**qc, "accepted": False, "reason": contamination["reason"]}
                qty = qc["quantity"]
                unit_jpy = row["priceJpy"] / qty if qty >= 1 else row["priceJpy"]
                if qc["accepted"]:
                    accepted += 1
                inserted += insert_sealed_sale(
                    cur,
                    sealed_id=item["sealedId"],
                    source_code="yahoo",
                    lot_id=f"auction:{row['auctionId']}",
                    sold_at=row["soldAt"],
                    unit_price_usd=to_usd(unit_jpy, "JPY", jpy_per_usd) if qc["accepted"] else None,
                    native_price=unit_jpy,
                    native_currency="JPY",
                    quantity=qty,
                    total_native_price=row["priceJpy"],
                    box_condition=qc["condition"],
                    title=row["title"],
                    raw_url=f"https://auctions.yahoo.co.jp/jp/auction/{row['auctionId']}",
                    metric_status="ok" if qc["accepted"] else f"rejected_{qc['reason']}",
                    parser="yahoo_closedsearch_v1",
                    ingest_run_key=run_key,
                )
            warehouse_sealed(
                cur,
                sealed_id=item["sealedId"],
                source_code="yahoo",
                external_entity_id="closedsearch",
                observation_kind="sealed_yahoo_closedsearch",
                payload={"url": item["url"], "rows": rows[:50]},
                ingest_run_key=run_key,
            )
            conn.commit()
            res.update({"status": "ok", "rows": len(rows), "accepted": accepted, "inserted": inserted})
            ok_items.append(item)
        except Exception as exc:  # noqa: BLE001
            conn.rollback()
            res.update({"status": "error", "error": f"{type(exc).__name__}:{exc}"})
        results.append(res)
        time.sleep(delay)
    receipt = record_sealed_run(conn, adapter="sealed_yahoo", mode=mode, items=ok_items, payload=results, started_at=utc_naive())
    conn.commit()
    return {"adapter": "sealed_yahoo", "mode": mode, "attempted": len(items), "ok": len(ok_items), "run": receipt, "items": results}


# --- sealed_ebay (import-only) / sealed_mercari (no transport) -----------------


def run_ebay_import(conn, input_path: Path) -> dict:
    """Verified completed-sales export: JSONL rows
    {slug|skuId, lotId, soldAt, priceUsd|priceNative+currency, title, url, quantity?}
    """
    cur = conn.cursor()
    cur.execute("SELECT id, sku_id, slug FROM catalog_sealed_product")
    by_slug = {r["slug"]: int(r["id"]) for r in cur.fetchall()}
    cur.execute("SELECT sku_id, id FROM catalog_sealed_product")
    by_sku = {r["sku_id"]: int(r["id"]) for r in cur.fetchall()}
    run_key = _run_key("sealed_ebay", "import")
    inserted = 0
    skipped = 0
    for line in input_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        row = json.loads(line)
        sealed_id = by_slug.get(row.get("slug") or "") or by_sku.get(row.get("skuId") or "")
        if not sealed_id:
            skipped += 1
            continue
        qc = qc_box_title(row.get("title") or "")
        qty = int(row.get("quantity") or qc["quantity"] or 1)
        price = row.get("priceUsd")
        unit = (float(price) / qty) if price is not None and qty >= 1 else None
        inserted += insert_sealed_sale(
            cur,
            sealed_id=sealed_id,
            source_code="ebay",
            lot_id=str(row.get("lotId") or "sha:" + sha(row)[:32]),
            sold_at=str(row.get("soldAt") or utc_naive().strftime("%Y-%m-%d %H:%M:%S")),
            unit_price_usd=unit if qc["accepted"] else None,
            native_price=float(price) if price is not None else None,
            native_currency="USD",
            quantity=qty,
            total_native_price=float(price) if price is not None else None,
            box_condition=qc["condition"],
            title=row.get("title"),
            raw_url=row.get("url"),
            metric_status="ok" if qc["accepted"] else f"rejected_{qc['reason']}",
            parser="ebay_import_v1",
            ingest_run_key=run_key,
        )
    conn.commit()
    return {"adapter": "sealed_ebay", "mode": "import", "inserted": inserted, "skipped": skipped}


# --- status --------------------------------------------------------------------


def cmd_status() -> dict:
    load_env()
    conn = db()
    try:
        cur = conn.cursor()
        out: dict[str, Any] = {"asOf": utc_now(), "action": "sealed-collect-status", "adapters": {}}
        for adapter in ("sealed_pc", "sealed_snk", "sealed_yahoo"):
            items = _load_adapter_items(cur, adapter, allow_candidates=True)
            accepted_items = _load_adapter_items(cur, adapter, allow_candidates=False)
            have = _sealed_ids_with_data(cur, adapter)
            checkpoints = load_sealed_checkpoints(cur, adapter)
            fresh = 0
            for item in items:
                age = checkpoint_age_hours(checkpoints, stream_key(item["sealedId"], item["externalId"]))
                if age is not None and age <= SLA_HOURS:
                    fresh += 1
            out["adapters"][adapter] = {
                "bound": len(items),
                "boundAccepted": len(accepted_items),
                "withData": len([i for i in items if i["sealedId"] in have]),
                "freshWithinSla": fresh,
                "slaHours": SLA_HOURS,
            }
        out["adapters"]["sealed_ebay"] = {"transport": "import-only; PC pages feed source_code=ebay"}
        out["adapters"]["sealed_mercari"] = {"transport": "none; fails closed"}
        return out
    finally:
        conn.close()


# --- main ------------------------------------------------------------------------


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status")

    for name in ("stock", "incr"):
        p = sub.add_parser(name)
        p.add_argument("--adapter", default="all", choices=[*SEALED_ADAPTERS, "all"])
        p.add_argument("--limit", type=int, default=None)
        p.add_argument("--allow-candidates", action="store_true",
                       help="engineering mode: pull for unfrozen candidate binds")
        p.add_argument("--force", action="store_true", help="ignore SLA freshness (incr)")
        p.add_argument("--delay", type=float, default=1.5)
        p.add_argument("--timeout", type=int, default=90)
        p.add_argument("--html-max-age-hours", type=float, default=20.0)
        p.add_argument("--input", type=Path, default=None, help="sealed_ebay import file")

    args = ap.parse_args()
    if args.cmd == "status":
        report = cmd_status()
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
        return 0

    load_env()
    adapters = list(SEALED_ADAPTERS) if args.adapter == "all" else [args.adapter]
    reports = []
    conn = db()
    try:
        cur = conn.cursor()
        for adapter in adapters:
            if adapter == "sealed_ebay":
                if args.input and args.input.is_file():
                    reports.append(run_ebay_import(conn, args.input))
                else:
                    reports.append({"adapter": adapter, "status": "unavailable", "reason": "no --input export; fails closed"})
                continue
            if adapter == "sealed_mercari":
                reports.append({"adapter": adapter, "status": "unavailable", "reason": "no transport (needs authed browser); fails closed"})
                continue
            items, blocked, shared = _select(cur, adapter, args.cmd, limit=args.limit, allow_candidates=args.allow_candidates,
                                             force=args.force)
            flags = {name: value for name, value in (("blocked", blocked), ("shared", shared)) if value}
            if not items:
                reports.append({"adapter": adapter, "mode": args.cmd, "attempted": 0, "ok": 0, "note": "nothing due", **flags})
                continue
            if adapter == "sealed_pc":
                report = run_pc(conn, items, mode=args.cmd, html_max_age_h=args.html_max_age_hours, timeout_s=args.timeout)
            elif adapter == "sealed_snk":
                report = run_snk(conn, items, mode=args.cmd, delay=args.delay)
            elif adapter == "sealed_yahoo":
                report = run_yahoo(conn, items, mode=args.cmd, delay=args.delay)
            reports.append({**report, **flags})
    finally:
        conn.close()

    COLLECT_OUT.mkdir(parents=True, exist_ok=True)
    out_path = COLLECT_OUT / f"last_{args.cmd}.json"
    doc = {"asOf": utc_now(), "cmd": args.cmd, "reports": reports}
    out_path.write_text(json.dumps(doc, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps(doc, ensure_ascii=False, indent=2, default=str))
    print(f"wrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
