#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Ingest SNK recent_trades (+ optional re-pull) into market_sale_observation.

Full-volume path for liquidity:
  1) Read harvest JSONL (default snk-psa10-940.jsonl)
  2) Map item_id → variant_id via catalog_source_identity
  3) PSA10 only (title contains PSA10 / grade 10)
  4) Bundle label 2枚… → unit price = total / n
  5) Write sale rows + liquidity registry hint

Usage:
  export CARDZ_DB_HOST=127.0.0.1
  python3 -X utf8 pipelines/ingest_snk_trades_sales.py
  python3 -X utf8 pipelines/ingest_snk_trades_sales.py --repull  # re-fetch all bound SNK ids
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

from failure_ledger import record_failure, record_resolution  # noqa: E402

MAP = ROOT / "data/runtime/private-source-map"
DEFAULT_HARVEST = MAP / "snk-psa10-940.jsonl"
REGISTRY = MAP / "liquidity-source-registry.jsonl"
REPORT_DIR = MAP / "qualified-pool-reports"


def load_env() -> None:
    env = ROOT / "data/runtime/config/backend.env"
    if not env.is_file():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())
    os.environ.setdefault("CARDZ_DB_HOST", "127.0.0.1")


def db():
    import pymysql

    load_env()
    return pymysql.connect(
        host=os.environ.get("CARDZ_DB_HOST", "127.0.0.1"),
        port=int(os.environ.get("CARDZ_DB_PORT", "3308")),
        user=os.environ["CARDZ_DB_USER"],
        password=os.environ["CARDZ_DB_PASSWORD"],
        database=os.environ["CARDZ_DB_NAME"],
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
    )


def jpy_per_usd(cur) -> float:
    cur.execute(
        """
        SELECT rate FROM market_fx_rate_observation
        WHERE base_currency='USD' AND quote_currency='JPY'
        ORDER BY effective_date DESC, id DESC LIMIT 1
        """
    )
    row = cur.fetchone()
    if not row or row.get("rate") is None or float(row["rate"]) <= 0:
        raise RuntimeError("USD/JPY FX rate missing; refuse to invent conversion")
    return float(row["rate"])


def parse_qty(label: str) -> int:
    m = re.search(r"(\d+)\s*枚", label or "")
    if m:
        n = int(m.group(1))
        return n if n > 0 else 1
    return 1


def is_psa10(title: str) -> bool:
    t = (title or "").upper().replace(" ", "")
    if "PSA10" in t or "PSA-10" in t:
        return True
    if re.search(r"\b10\b", title or "") and "PSA" in t:
        return True
    return "PSA10" in (title or "").upper().replace(" ", "")


def fingerprint(item_id: int, sold_at: str, price_jpy: float, qty: int, title: str) -> str:
    raw = f"snk|{item_id}|{sold_at}|{price_jpy}|{qty}|{title}"
    return hashlib.sha256(raw.encode()).hexdigest()


def item_to_variant(cur) -> dict[int, int]:
    cur.execute(
        """
        SELECT external_entity_id, variant_id
        FROM (
            SELECT identity.external_entity_id,
                   COALESCE(alias.canonical_variant_id, identity.variant_id) AS variant_id
            FROM catalog_source_identity AS identity
            LEFT JOIN catalog_variant_alias AS alias
              ON alias.duplicate_variant_id = identity.variant_id
            WHERE identity.source_code IN ('snkrdunk', 'snk', 'snk_psa10')
              AND LOWER(identity.match_status) = 'exact'
        ) AS exact_identity
        """
    )
    out: dict[int, int] = {}
    for r in cur.fetchall():
        try:
            out.setdefault(int(r["external_entity_id"]), int(r["variant_id"]))
        except (TypeError, ValueError):
            continue
    return out


def load_harvest(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def repull_bound(ids: list[int], out_path: Path, delay: float) -> Path:
    from snk_market_data import run as snk_run

    run_id = f"snk_liq_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    # snk_run expects fresh out path or complete existing — use new path
    snk_run(ids, out_path, delay, "trading_card_single_psa10", run_id)
    return out_path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--harvest", type=Path, default=DEFAULT_HARVEST)
    parser.add_argument("--repull", action="store_true", help="re-fetch all bound SNK item ids")
    parser.add_argument("--delay", type=float, default=0.35)
    args = parser.parse_args()
    load_env()

    conn = db()
    cur = conn.cursor()
    fx = jpy_per_usd(cur)
    id_map = item_to_variant(cur)
    print(json.dumps({"boundSnkIds": len(id_map), "jpyPerUsd": fx}, sort_keys=True), flush=True)

    harvest_path = args.harvest
    if args.repull and id_map:
        harvest_path = MAP / f"snk-psa10-liquidity-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.jsonl"
        print(f"[repull] {len(id_map)} items → {harvest_path}", flush=True)
        try:
            repull_bound(sorted(id_map.keys()), harvest_path, args.delay)
        except Exception as exc:  # noqa: BLE001
            print(f"[repull] failed: {exc}; falling back to {args.harvest}", flush=True)
            harvest_path = args.harvest

    rows = load_harvest(harvest_path)
    print(json.dumps({"harvestRows": len(rows), "path": str(harvest_path)}, sort_keys=True), flush=True)

    effective = datetime.now(timezone.utc).replace(tzinfo=None)
    run_key = f"snk_trades_sales_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    seed = hashlib.sha256(run_key.encode()).hexdigest()
    cur.execute(
        """
        INSERT INTO market_ingest_run
            (run_key, source_code, ingest_mode, effective_at, payload_sha256, manifest_sha256,
             status, observed_count, accepted_count, quarantined_count, rejected_count, started_at)
        VALUES (%s, 'snkrdunk', 'full', %s, %s, %s, 'running', 0, 0, 0, 0, %s)
        """,
        (run_key, effective, seed, seed, effective),
    )
    run_id = cur.lastrowid

    written = 0
    skipped_no_vid = 0
    skipped_not_psa10 = 0
    skipped_bad = 0
    cards_with_sales = set()
    registry_updates: list[dict[str, Any]] = []
    sales_to_write: list[tuple[Any, ...]] = []
    resolved_cards: list[tuple[int, int, int]] = []

    for row in rows:
        item = row.get("item_id")
        if not isinstance(item, int):
            continue
        vid = id_map.get(item)
        if not vid:
            skipped_no_vid += 1
            record_failure(
                source="snkrdunk",
                stage="normalize_psa10_sales",
                script=__file__,
                item_key=item,
                reason_code="no_exact_identity",
                message="harvest item has no exact canonical identity",
                retryable=False,
                run_id=run_key,
                evidence_paths=[harvest_path],
                next_action="agent_identity_review",
            )
            continue
        trades = row.get("recent_trades") or []
        if not isinstance(trades, list) or not trades:
            # daily_activity proxy still mark
            da = row.get("daily_activity") or {}
            if da:
                registry_updates.append(
                    {
                        "variantId": vid,
                        "preferredLiquiditySource": "snk_daily_activity",
                        "snkItemId": item,
                        "script": "pipelines/snk_market_data.py",
                        "note": "activity only, no recent_trades list",
                    }
                )
            record_failure(
                source="snkrdunk",
                stage="normalize_psa10_sales",
                script=__file__,
                item_key=item,
                reason_code="no_psa10_trades",
                message="source snapshot has no recent PSA 10 trades",
                retryable=True,
                run_id=run_key,
                context={"variantId": vid},
                evidence_paths=[harvest_path],
                next_action="retry_later_or_cross_source",
            )
            continue

        card_sales = 0
        for t in trades:
            if not isinstance(t, dict):
                skipped_bad += 1
                continue
            title = str(t.get("title") or "")
            if not is_psa10(title):
                skipped_not_psa10 += 1
                continue
            price_jpy = t.get("price")
            if not isinstance(price_jpy, (int, float)) or price_jpy <= 0:
                skipped_bad += 1
                continue
            qty = parse_qty(str(t.get("label") or "1枚"))
            unit_jpy = float(price_jpy) / qty
            unit_usd = round(unit_jpy / fx, 6)
            sold_raw = str(t.get("soldAt") or t.get("sold_at") or "")
            try:
                sold_at = datetime.fromisoformat(sold_raw.replace("Z", "+00:00")).replace(tzinfo=None)
            except ValueError:
                skipped_bad += 1
                continue
            fp = fingerprint(item, sold_raw, float(price_jpy), qty, title)
            payload = {"itemId": item, "priceJpy": price_jpy, "qty": qty, "title": title, "soldAt": sold_raw}
            ph = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
            sales_to_write.append(
                (
                    run_id,
                    vid,
                    "snkrdunk",
                    str(item),
                    fp,
                    "psa",
                    "10",
                    sold_at,
                    sold_raw[:100],
                    effective,
                    "exact_date",
                    unit_usd,
                    qty,
                    round(unit_usd * qty, 6),
                    ph,
                    "partial",
                )
            )
            written += 1
            card_sales += 1

        if card_sales > 0:
            cards_with_sales.add(vid)
            registry_updates.append(
                {
                    "variantId": vid,
                    "preferredLiquiditySource": "snkrdunk",
                    "snkItemId": item,
                    "script": "pipelines/ingest_snk_trades_sales.py",
                    "tradesIngested": card_sales,
                    "updatedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                }
            )
            resolved_cards.append((item, vid, card_sales))
        else:
            record_failure(
                source="snkrdunk",
                stage="normalize_psa10_sales",
                script=__file__,
                item_key=item,
                reason_code="zero_valid_psa10_trades",
                message="recent trades were present but none passed PSA 10 normalization",
                retryable=True,
                run_id=run_key,
                context={"variantId": vid, "rawTrades": len(trades)},
                evidence_paths=[harvest_path],
                next_action="agent_review_or_cross_source",
            )

    sales_upsert = """
        INSERT INTO market_sale_observation
            (run_id, variant_id, source_code, external_entity_id, transaction_fingerprint,
             grader_code, grade_label, sold_at, source_date_text, fetched_at,
             timestamp_quality, unit_price_usd, quantity, transaction_value_usd,
             source_payload_sha256, coverage_status)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            run_id=VALUES(run_id),
            variant_id=VALUES(variant_id),
            unit_price_usd=VALUES(unit_price_usd),
            quantity=VALUES(quantity),
            transaction_value_usd=VALUES(transaction_value_usd),
            sold_at=VALUES(sold_at),
            fetched_at=VALUES(fetched_at),
            source_payload_sha256=VALUES(source_payload_sha256),
            coverage_status=VALUES(coverage_status)
    """
    try:
        for offset in range(0, len(sales_to_write), 1000):
            cur.executemany(
                sales_upsert,
                sales_to_write[offset : offset + 1000],
            )
        cur.execute(
            """
            UPDATE market_ingest_run
            SET status='complete', observed_count=%s, accepted_count=%s, completed_at=%s
            WHERE id=%s
            """,
            (written, written, datetime.now(timezone.utc).replace(tzinfo=None), run_id),
        )
        conn.commit()
    except Exception as exc:
        conn.rollback()
        record_failure(
            source="snkrdunk",
            stage="write_psa10_sales",
            script=__file__,
            item_key=run_key,
            reason_code="batch_write_failed",
            message=str(exc),
            retryable=True,
            run_id=run_key,
            evidence_paths=[harvest_path],
            next_action="retry",
            error_type=type(exc).__name__,
        )
        conn.close()
        raise

    # coverage after — windows are derived from full history (ingest keeps all sold_at)
    cur.execute(
        """
        SELECT
          COUNT(DISTINCT CASE WHEN s.sold_at >= DATE_SUB(UTC_TIMESTAMP(), INTERVAL 1 DAY) THEN w.variant_id END) AS d1,
          COUNT(DISTINCT CASE WHEN s.sold_at >= DATE_SUB(UTC_TIMESTAMP(), INTERVAL 7 DAY) THEN w.variant_id END) AS d7,
          COUNT(DISTINCT CASE WHEN s.sold_at >= DATE_SUB(UTC_TIMESTAMP(), INTERVAL 21 DAY) THEN w.variant_id END) AS d21,
          COUNT(DISTINCT CASE WHEN s.sold_at >= DATE_SUB(UTC_TIMESTAMP(), INTERVAL 30 DAY) THEN w.variant_id END) AS d30,
          COUNT(DISTINCT CASE WHEN s.sold_at >= DATE_SUB(UTC_TIMESTAMP(), INTERVAL 90 DAY) THEN w.variant_id END) AS d90,
          COUNT(DISTINCT w.variant_id) AS ever
        FROM market_gemrate_psa10_watchlist w
        JOIN market_sale_observation s ON s.variant_id = w.variant_id
        WHERE s.sold_at IS NOT NULL
        """
    )
    win = cur.fetchone() or {}
    watch_ever = int(win.get("ever") or 0)
    watch_30d = int(win.get("d30") or 0)
    conn.close()
    for item, vid, card_sales in resolved_cards:
        record_resolution(
            source="snkrdunk",
            stage="normalize_psa10_sales",
            script=__file__,
            item_key=item,
            run_id=run_key,
            resolution="accepted_psa10_sales",
            context={"variantId": vid, "tradesIngested": card_sales},
            evidence_paths=[harvest_path],
        )

    # merge registry
    by_vid: dict[int, dict] = {}
    if REGISTRY.is_file():
        for line in REGISTRY.read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                by_vid[int(r["variantId"])] = r
    for r in registry_updates:
        by_vid[int(r["variantId"])] = {**(by_vid.get(int(r["variantId"])) or {}), **r}
    REGISTRY.parent.mkdir(parents=True, exist_ok=True)
    with REGISTRY.open("w", encoding="utf-8") as fh:
        for vid in sorted(by_vid):
            fh.write(json.dumps(by_vid[vid], ensure_ascii=False, default=str) + "\n")

    summary = {
        "runId": run_id,
        "runKey": run_key,
        "harvest": str(harvest_path),
        "tradesWritten": written,
        "cardsWithSales": len(cards_with_sales),
        "skippedNoVariant": skipped_no_vid,
        "skippedNotPsa10": skipped_not_psa10,
        "skippedBad": skipped_bad,
        "note": "All trades written regardless of age; 1d/7d/21d/30d are derived windows only",
        "watchlistCardsWithAnySale": watch_ever,
        "watchlistSaleWindows": {
            "1d": int(win.get("d1") or 0),
            "7d": int(win.get("d7") or 0),
            "21d": int(win.get("d21") or 0),
            "30d": watch_30d,
            "90d": int(win.get("d90") or 0),
            "any": watch_ever,
        },
        "watchlistCardsWithPsa10Sale30d": watch_30d,
        "registry": str(REGISTRY),
        "registryRows": len(by_vid),
    }
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    rep = REPORT_DIR / f"ingest_snk_trades_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    rep.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    print("report", rep)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
