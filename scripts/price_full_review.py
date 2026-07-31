#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Comprehensive READ-ONLY price review for CARDZ Market Cap.

Phases A–D against MySQL + public snapshot. Writes evidence under
docs/evidence/<date>-price-review/ and prints an executive summary.

Usage:
  python -X utf8 scripts/price_full_review.py
  python -X utf8 scripts/price_full_review.py --snapshot data/public/publish-staging/generations/plan_a_qc3_20260729_154227/snapshot.json
  python -X utf8 scripts/price_full_review.py --out-dir docs/evidence/2026-07-29-price-review

Never writes to the database.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from verify_claims import db_config, scrub  # noqa: E402

TRUSTED = frozenset({"snk_psa10", "snk", "snkrdunk", "tcgpricelookup"})
RISKY = frozenset({"ebay", "g10_kline", "g10"})
KNOWN_SOURCES = TRUSTED | RISKY | frozenset({"tcgfish", "snkrdunk"})
OUTLIER_RATIO = 5.0
THRESH_TOP100 = {"1d": 50.0, "7d": 100.0, "30d": 200.0}
THRESH_POOL_30D = 500.0
WINDOWS = ("1d", "7d", "30d")
WINDOW_DAYS = {"1d": 1, "7d": 7, "30d": 30}
WINDOW_TOL = {"1d": 2, "7d": 3, "30d": 3}
LIVE = frozenset({"ready", "stale"})


def history_price_map(hist: list[dict[str, Any]] | None) -> dict[str, float]:
    """Normalize snapshot historyDaily points → {YYYY-MM-DD: priceUsd}."""
    prices: dict[str, float] = {}
    for pt in hist or []:
        if not isinstance(pt, dict):
            continue
        d = pt.get("date") or pt.get("day") or pt.get("at") or pt.get("observed_date")
        pv = pt.get("priceUsd") or pt.get("price") or pt.get("close") or pt.get("price_usd")
        if d is None or pv is None:
            continue
        day = str(d)[:10]
        try:
            prices[day] = float(pv)
        except (TypeError, ValueError):
            continue
    return prices


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def connect():
    import pymysql

    cfg = db_config()
    if not cfg.get("password"):
        raise SystemExit("CARDZ_DB_PASSWORD missing (backend.env)")
    return pymysql.connect(
        host=cfg["host"],
        port=cfg["port"],
        user=cfg["user"],
        password=cfg["password"],
        database=cfg["database"],
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=True,
        read_timeout=120,
        write_timeout=30,
    )


def q(cur, sql: str, args: tuple | list | None = None) -> list[dict[str, Any]]:
    cur.execute(sql, args or ())
    return list(cur.fetchall() or [])


def q1(cur, sql: str, args: tuple | list | None = None) -> dict[str, Any]:
    rows = q(cur, sql, args)
    return rows[0] if rows else {}


def num(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def live_metric(m: Any) -> float | None:
    if not isinstance(m, dict):
        return None
    if m.get("status") not in LIVE or m.get("value") is None:
        return None
    return num(m.get("value"))


def metric_status(m: Any) -> str:
    if not isinstance(m, dict):
        return "missing"
    return str(m.get("status") or "missing")


def resolve_snapshot_path(explicit: Path | None) -> Path:
    if explicit is not None:
        return explicit
    latest = ROOT / "data/public/publish-staging/latest.json"
    if latest.is_file():
        meta = json.loads(latest.read_text(encoding="utf-8"))
        key = meta.get("snapshotKey") or ""
        if key:
            p = ROOT / "data/public/publish-staging" / key
            if p.is_file():
                return p
    # fallbacks
    for cand in (
        ROOT / "data/public/publish-staging/generations/canonical_live_fe/snapshot.json",
        ROOT / "data/public/seed-snapshot.json",
    ):
        if cand.is_file():
            return cand
    raise SystemExit("no snapshot found")


def atomic_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.next")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        for row in rows:
            w.writerow(row)


# ── Phase A ──────────────────────────────────────────────────────────


def phase_a(cur) -> dict[str, Any]:
    watch = q1(
        cur,
        """
        SELECT
          COUNT(*) AS watch,
          SUM(w.variant_id IS NOT NULL) AS has_variant,
          SUM(EXISTS(SELECT 1 FROM market_price_observation p
                     WHERE p.variant_id=w.variant_id)) AS any_price,
          SUM(EXISTS(SELECT 1 FROM market_price_observation p
                     WHERE p.variant_id=w.variant_id AND p.price_usd > 0)) AS strict_price,
          SUM(EXISTS(SELECT 1 FROM market_price_observation p
                     WHERE p.variant_id=w.variant_id AND p.source_code='snk_psa10'
                       AND p.price_usd > 0)) AS snk_psa10,
          SUM(EXISTS(SELECT 1 FROM market_price_observation p
                     WHERE p.variant_id=w.variant_id AND p.source_code='tcgpricelookup'
                       AND p.price_usd > 0)) AS tcgpricelookup,
          SUM(EXISTS(SELECT 1 FROM market_price_observation p
                     WHERE p.variant_id=w.variant_id AND p.source_code='ebay'
                       AND p.price_usd > 0)) AS ebay,
          SUM(EXISTS(SELECT 1 FROM market_price_observation p
                     WHERE p.variant_id=w.variant_id AND p.source_code='g10_kline'
                       AND p.price_usd > 0)) AS g10_kline,
          SUM(EXISTS(SELECT 1 FROM market_price_observation p
                     WHERE p.variant_id=w.variant_id AND p.source_code='tcgfish'
                       AND p.price_usd > 0)) AS tcgfish,
          SUM(EXISTS(SELECT 1 FROM market_price_observation p
                     WHERE p.variant_id=w.variant_id
                       AND p.source_code IN ('snk_psa10','snkrdunk','g10_kline')
                       AND p.price_usd > 0)) AS snk_family,
          SUM(EXISTS(SELECT 1 FROM catalog_source_identity s
                     WHERE s.variant_id=w.variant_id
                       AND s.source_code IN ('snkrdunk','snk'))) AS snk_id
        FROM market_gemrate_psa10_watchlist w
        """,
    )
    for k, v in list(watch.items()):
        watch[k] = int(v or 0)

    source_enum = q(
        cur,
        """
        SELECT source_code, COUNT(*) AS rows_n,
               COUNT(DISTINCT variant_id) AS variants,
               MIN(observed_date) AS min_d, MAX(observed_date) AS max_d
        FROM market_price_observation
        GROUP BY source_code
        ORDER BY rows_n DESC
        """,
    )
    for r in source_enum:
        r["rows_n"] = int(r["rows_n"] or 0)
        r["variants"] = int(r["variants"] or 0)
        r["min_d"] = str(r["min_d"] or "")
        r["max_d"] = str(r["max_d"] or "")

    recent = q(
        cur,
        """
        SELECT observed_date, source_code, COUNT(*) AS n
        FROM market_price_observation
        WHERE observed_date >= DATE_SUB(CURDATE(), INTERVAL 14 DAY)
        GROUP BY observed_date, source_code
        ORDER BY observed_date DESC, n DESC
        """,
    )
    for r in recent:
        r["n"] = int(r["n"] or 0)
        r["observed_date"] = str(r["observed_date"])

    index_rows = q(
        cur,
        """
        SELECT id, run_id, index_code, effective_date, constituent_count,
               total_market_cap_usd, created_at
        FROM market_index_snapshot
        ORDER BY id DESC
        LIMIT 12
        """,
    )
    for r in index_rows:
        r["constituent_count"] = int(r["constituent_count"] or 0)
        r["total_market_cap_usd"] = num(r.get("total_market_cap_usd"))
        r["effective_date"] = str(r.get("effective_date") or "")
        r["created_at"] = str(r.get("created_at") or "")

    catalog = q1(cur, "SELECT COUNT(*) AS n FROM catalog_variant")
    universe = q1(
        cur,
        """
        SELECT COUNT(*) AS n FROM market_universe_member
        WHERE universe_lock_id = (
          SELECT MAX(universe_lock_id) FROM market_universe_member
        )
        """,
    )

    return {
        "watchlist": watch,
        "sourceEnum": source_enum,
        "recent14d": recent,
        "indexSnapshots": index_rows,
        "catalogVariants": int(catalog.get("n") or 0),
        "universeMembers": int(universe.get("n") or 0) if universe.get("n") is not None else None,
    }


# ── Phase B ──────────────────────────────────────────────────────────


def phase_b(cur, today: date) -> dict[str, Any]:
    no_price = q(
        cur,
        """
        SELECT w.variant_id, w.gemrate_id,
               COALESCE(cv.canonical_name, w.card_name) AS name_en,
               w.set_name, w.collector_number,
               EXISTS(SELECT 1 FROM catalog_source_identity s
                      WHERE s.variant_id=w.variant_id
                        AND s.source_code='tcgpricelookup') AS has_tpl_id,
               EXISTS(SELECT 1 FROM catalog_source_identity s
                      WHERE s.variant_id=w.variant_id
                        AND s.source_code IN ('snkrdunk','snk')) AS has_snk_id
        FROM market_gemrate_psa10_watchlist w
        LEFT JOIN catalog_variant cv ON cv.id = w.variant_id
        WHERE w.variant_id IS NOT NULL
          AND NOT EXISTS (
            SELECT 1 FROM market_price_observation p
            WHERE p.variant_id = w.variant_id AND p.price_usd > 0
          )
        ORDER BY w.variant_id
        """,
    )
    for r in no_price:
        r["has_tpl_id"] = bool(r.get("has_tpl_id"))
        r["has_snk_id"] = bool(r.get("has_snk_id"))
        r["variant_id"] = int(r["variant_id"]) if r.get("variant_id") is not None else None

    # Fake-today: same source, today's price equals previous observation price
    fake_today = q(
        cur,
        """
        SELECT t.variant_id, t.source_code, t.observed_date AS today_date,
               t.price_usd AS today_price, p.observed_date AS prev_date,
               p.price_usd AS prev_price, t.effective_at AS today_effective_at
        FROM market_price_observation t
        INNER JOIN market_price_observation p
          ON p.variant_id = t.variant_id
         AND p.source_code = t.source_code
         AND p.observed_date = (
              SELECT MAX(p2.observed_date)
              FROM market_price_observation p2
              WHERE p2.variant_id = t.variant_id
                AND p2.source_code = t.source_code
                AND p2.observed_date < t.observed_date
                AND p2.price_usd > 0
         )
        WHERE t.observed_date = %s
          AND t.price_usd > 0
          AND ABS(t.price_usd - p.price_usd) < 0.005
          AND t.variant_id IN (SELECT variant_id FROM market_gemrate_psa10_watchlist)
        ORDER BY t.source_code, t.variant_id
        """,
        (today.isoformat(),),
    )
    for r in fake_today:
        r["variant_id"] = int(r["variant_id"])
        r["today_price"] = num(r["today_price"])
        r["prev_price"] = num(r["prev_price"])
        r["today_date"] = str(r["today_date"])
        r["prev_date"] = str(r["prev_date"])
        r["today_effective_at"] = str(r.get("today_effective_at") or "")

    fake_by_source = Counter(r["source_code"] for r in fake_today)

    # bulk same effective_at cluster (suggests stamp job)
    bulk_stamp = q(
        cur,
        """
        SELECT source_code, DATE(effective_at) AS eff_day,
               COUNT(*) AS n, COUNT(DISTINCT variant_id) AS variants
        FROM market_price_observation
        WHERE observed_date = %s
          AND variant_id IN (SELECT variant_id FROM market_gemrate_psa10_watchlist)
        GROUP BY source_code, DATE(effective_at)
        HAVING COUNT(*) >= 20
        ORDER BY n DESC
        """,
        (today.isoformat(),),
    )
    for r in bulk_stamp:
        r["n"] = int(r["n"] or 0)
        r["variants"] = int(r["variants"] or 0)
        r["eff_day"] = str(r.get("eff_day") or "")

    unknown_sources = [
        r for r in q(
            cur,
            """
            SELECT source_code, COUNT(*) AS n
            FROM market_price_observation
            GROUP BY source_code
            """,
        )
        if r["source_code"] not in KNOWN_SOURCES
        and r["source_code"] not in ("snkrdunk",)
    ]
    # re-list all for forbidden-name check
    forbidden_hits = q(
        cur,
        """
        SELECT source_code, COUNT(*) AS n
        FROM market_price_observation
        WHERE source_code LIKE %s
           OR source_code LIKE %s
           OR source_code LIKE %s
           OR source_code LIKE %s
        GROUP BY source_code
        """,
        ("%limitless%", "%tcgplayer%", "%raw%", "%cardmarket%"),
    )

    return {
        "noPriceCount": len(no_price),
        "noPrice": no_price,
        "fakeTodayCount": len(fake_today),
        "fakeTodayBySource": dict(fake_by_source),
        "fakeTodaySample": fake_today[:200],
        "bulkTodayClusters": bulk_stamp,
        "unknownSources": [
            {"source_code": r["source_code"], "n": int(r["n"] or 0)} for r in unknown_sources
        ],
        "forbiddenSourceHits": [
            {"source_code": r["source_code"], "n": int(r["n"] or 0)} for r in forbidden_hits
        ],
    }


# ── Phase C depth ────────────────────────────────────────────────────


def can_window(dates: set[date], latest: date, window: str) -> bool:
    days = WINDOW_DAYS[window]
    tol = WINDOW_TOL[window]
    target = latest - timedelta(days=days)
    earlier = {d for d in dates if d < latest}
    if not earlier:
        return False
    return any(abs((d - target).days) <= tol for d in earlier)


def phase_c_pool_depth(cur) -> dict[str, Any]:
    rows = q(
        cur,
        """
        SELECT p.variant_id, p.observed_date
        FROM market_price_observation p
        WHERE p.price_usd > 0
          AND p.variant_id IN (SELECT variant_id FROM market_gemrate_psa10_watchlist)
        """,
    )
    by_var: dict[int, set[date]] = defaultdict(set)
    for r in rows:
        vid = int(r["variant_id"])
        d = r["observed_date"]
        if isinstance(d, datetime):
            d = d.date()
        elif isinstance(d, str):
            d = date.fromisoformat(d[:10])
        by_var[vid].add(d)

    today = date.today()
    horizon = today - timedelta(days=33)
    stats = {
        "variantsWithAnyBar": len(by_var),
        "can_1d": 0,
        "can_7d": 0,
        "can_30d": 0,
        "bars30d_ge1": 0,
        "bars30d_ge5": 0,
        "bars30d_ge10": 0,
        "sparse_no_30d_anchor": 0,
    }
    depth_hist: Counter[int] = Counter()
    for vid, dates in by_var.items():
        latest = max(dates)
        bars30 = sum(1 for d in dates if d >= horizon)
        depth_hist[min(bars30, 30)] += 1
        if bars30 >= 1:
            stats["bars30d_ge1"] += 1
        if bars30 >= 5:
            stats["bars30d_ge5"] += 1
        if bars30 >= 10:
            stats["bars30d_ge10"] += 1
        for w in WINDOWS:
            if can_window(dates, latest, w):
                stats[f"can_{w}"] += 1
        if not can_window(dates, latest, "30d"):
            stats["sparse_no_30d_anchor"] += 1

    # candidate daily change null rates (latest evaluation_id)
    cand = q1(
        cur,
        """
        SELECT
          COUNT(*) AS n,
          SUM(change_1d_pct IS NOT NULL) AS c1,
          SUM(change_7d_pct IS NOT NULL) AS c7,
          SUM(change_30d_pct IS NOT NULL) AS c30
        FROM market_candidate_daily_snapshot
        WHERE evaluation_id = (
          SELECT MAX(evaluation_id) FROM market_candidate_daily_snapshot
        )
        """,
    )
    if not cand or int(cand.get("n") or 0) == 0:
        cand = q1(
            cur,
            """
            SELECT COUNT(*) AS n,
                   SUM(change_1d_pct IS NOT NULL) AS c1,
                   SUM(change_7d_pct IS NOT NULL) AS c7,
                   SUM(change_30d_pct IS NOT NULL) AS c30
            FROM market_candidate_daily_snapshot
            """,
        )
    candidate = {
        "rows": int(cand.get("n") or 0),
        "change_1d_nonnull": int(cand.get("c1") or 0),
        "change_7d_nonnull": int(cand.get("c7") or 0),
        "change_30d_nonnull": int(cand.get("c30") or 0),
    }

    return {
        "poolDepth": stats,
        "depthHist30d": {str(k): v for k, v in sorted(depth_hist.items())},
        "candidateDaily": candidate,
    }


def phase_c_snapshot(doc: dict[str, Any]) -> dict[str, Any]:
    def analyze(cards: list[dict[str, Any]], label: str) -> dict[str, Any]:
        out: dict[str, Any] = {
            "label": label,
            "n": len(cards),
            "priceReady": 0,
            "priceMissing": 0,
            "windows": {},
            "fakeZero": [],
            "nullChange": {w: [] for w in WINDOWS},
            "marketCapChangeReady": {w: 0 for w in WINDOWS},
            "popChangeReady": {w: 0 for w in WINDOWS},
        }
        for w in WINDOWS:
            out["windows"][w] = Counter()

        for card in cards:
            price = card.get("pricePsa10") or {}
            if price.get("status") in LIVE and price.get("value") is not None:
                out["priceReady"] += 1
            else:
                out["priceMissing"] += 1
            prices_by_day = history_price_map(card.get("historyDaily"))

            psa = (card.get("graderPopulations") or {}).get("PSA") or {}
            pop_ch = psa.get("topGradePopulationChangePct") or {}

            for w in WINDOWS:
                win = (card.get("windows") or {}).get(w) or {}
                ch = win.get("changePct") or {}
                st = metric_status(ch)
                out["windows"][w][st] += 1
                val = live_metric(ch)
                if st not in LIVE or val is None:
                    out["nullChange"][w].append(
                        {
                            "rank": card.get("rank") or card.get("marketRank"),
                            "id": card.get("id"),
                            "name": ((card.get("names") or {}).get("en") or card.get("id") or "")[:40],
                            "status": st,
                        }
                    )
                # fake zero: value ~0 but history has different prices in window
                if val is not None and abs(val) < 1e-9 and len(prices_by_day) >= 2:
                    days_sorted = sorted(prices_by_day)
                    latest = days_sorted[-1]
                    try:
                        latest_d = date.fromisoformat(latest)
                        target = latest_d - timedelta(days=WINDOW_DAYS[w])
                        near_vals = [
                            prices_by_day[d]
                            for d in days_sorted[:-1]
                            if abs((date.fromisoformat(d) - target).days) <= WINDOW_TOL[w]
                        ]
                        if near_vals and any(abs(v - prices_by_day[latest]) > 0.01 for v in near_vals):
                            out["fakeZero"].append(
                                {
                                    "rank": card.get("rank"),
                                    "id": card.get("id"),
                                    "name": ((card.get("names") or {}).get("en") or "")[:40],
                                    "window": w,
                                    "latest": prices_by_day[latest],
                                    "anchor": near_vals[0],
                                }
                            )
                    except ValueError:
                        pass

                mcc = live_metric(win.get("marketCapChangePct"))
                if mcc is not None:
                    out["marketCapChangeReady"][w] += 1
                if live_metric(pop_ch.get(w) if isinstance(pop_ch, dict) else None) is not None:
                    out["popChangeReady"][w] += 1

        # serialize counters
        out["windows"] = {w: dict(out["windows"][w]) for w in WINDOWS}
        # cap null lists
        for w in WINDOWS:
            out["nullChange"][w] = out["nullChange"][w][:50]
        out["fakeZeroCount"] = len(out["fakeZero"])
        out["fakeZero"] = out["fakeZero"][:30]
        return out

    top100 = list(doc.get("top100") or [])
    watch = list(doc.get("watchlist") or [])
    return {
        "top100": analyze(top100, "top100"),
        "watchlist": analyze(watch, "watchlist"),
        "feSetN": len(top100) + len(watch),
        "meta": {
            "generatedAt": doc.get("generatedAt") or doc.get("meta", {}).get("generatedAt"),
            "effectiveAt": doc.get("effectiveAt") or doc.get("meta", {}).get("effectiveAt"),
            "generationId": doc.get("generationId") or doc.get("meta", {}).get("generationId"),
        },
    }


# ── Phase D outliers ─────────────────────────────────────────────────


def phase_d_snapshot(doc: dict[str, Any]) -> dict[str, Any]:
    flags: list[dict[str, Any]] = []

    def scan(cards: list[dict[str, Any]], bucket: str) -> None:
        for card in cards:
            name = ((card.get("names") or {}).get("en") or card.get("id") or "")[:50]
            rank = card.get("rank") or card.get("marketRank")
            price = live_metric(card.get("pricePsa10"))
            cap = live_metric(card.get("marketCap"))
            row = {
                "bucket": bucket,
                "rank": rank,
                "id": card.get("id"),
                "name": name,
                "pricePsa10": price,
                "marketCap": cap,
            }
            reasons: list[str] = []
            for w in WINDOWS:
                ch = live_metric(((card.get("windows") or {}).get(w) or {}).get("changePct"))
                row[f"pct_{w}"] = ch
                if ch is None:
                    continue
                thr = THRESH_TOP100[w] if bucket == "top100" else (
                    THRESH_POOL_30D if w == "30d" else THRESH_TOP100[w] * 2
                )
                if abs(ch) > thr:
                    reasons.append(f"|{w}|={ch:.1f}%>{thr}")
            if price is not None and price > 50000:
                reasons.append(f"price>${price:,.0f}")
            if reasons:
                row["reasons"] = ";".join(reasons)
                flags.append(row)

    scan(list(doc.get("top100") or []), "top100")
    scan(list(doc.get("watchlist") or []), "watchlist")

    # also ranked beyond FE if present
    for key in ("ranked", "candidates", "board"):
        extra = doc.get(key)
        if isinstance(extra, list):
            scan(extra, key)

    flags.sort(
        key=lambda r: max(abs(r.get(f"pct_{w}") or 0) for w in WINDOWS),
        reverse=True,
    )
    top100_extreme_30 = sum(
        1
        for r in flags
        if r["bucket"] == "top100"
        and r.get("pct_30d") is not None
        and abs(r["pct_30d"]) > THRESH_TOP100["30d"]
    )
    return {
        "flagCount": len(flags),
        "top100_abs30d_gt200": top100_extreme_30,
        "flags": flags[:200],
    }


def phase_d_db_outliers(cur) -> dict[str, Any]:
    """Cross-source spread + risky vs trusted median on latest prices."""
    rows = q(
        cur,
        """
        SELECT p.variant_id, p.source_code, p.price_usd, p.observed_date
        FROM market_price_observation p
        INNER JOIN (
          SELECT variant_id, source_code, MAX(observed_date) AS max_d
          FROM market_price_observation
          WHERE price_usd > 0
            AND variant_id IN (SELECT variant_id FROM market_gemrate_psa10_watchlist)
            AND observed_date >= DATE_SUB(CURDATE(), INTERVAL 90 DAY)
          GROUP BY variant_id, source_code
        ) m ON m.variant_id = p.variant_id
           AND m.source_code = p.source_code
           AND m.max_d = p.observed_date
        WHERE p.price_usd > 0
        """,
    )
    by_var: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        by_var[int(r["variant_id"])].append(
            {
                "source": r["source_code"],
                "price": float(r["price_usd"]),
                "date": str(r["observed_date"]),
            }
        )

    cross: list[dict[str, Any]] = []
    risky_flags: list[dict[str, Any]] = []
    for vid, pts in by_var.items():
        prices = [p["price"] for p in pts]
        if len(prices) >= 2:
            lo, hi = min(prices), max(prices)
            if lo > 0 and hi / lo > OUTLIER_RATIO:
                cross.append(
                    {
                        "variant_id": vid,
                        "min": lo,
                        "max": hi,
                        "ratio": round(hi / lo, 2),
                        "sources": pts,
                    }
                )
        trusted = [p["price"] for p in pts if p["source"] in TRUSTED]
        risky = [p for p in pts if p["source"] in RISKY]
        if trusted and risky:
            med = sorted(trusted)[len(trusted) // 2]
            for rp in risky:
                if med > 0 and rp["price"] / med > OUTLIER_RATIO:
                    risky_flags.append(
                        {
                            "variant_id": vid,
                            "risky_source": rp["source"],
                            "risky_price": rp["price"],
                            "trusted_median": med,
                            "ratio": round(rp["price"] / med, 2),
                        }
                    )

    cross.sort(key=lambda x: x["ratio"], reverse=True)
    risky_flags.sort(key=lambda x: x["ratio"], reverse=True)
    return {
        "crossSourceSpreadGt5x": len(cross),
        "crossSourceSample": cross[:50],
        "riskyVsTrustedGt5x": len(risky_flags),
        "riskySample": risky_flags[:50],
    }


def phase_e_sample(doc: dict[str, Any], cur) -> list[dict[str, Any]]:
    """10 top + 10 mid + up to 10 extreme — recompute cap and window from history."""
    top = list(doc.get("top100") or [])
    picks: list[dict[str, Any]] = []
    if top:
        for card in top[:10]:
            picks.append(("top", card))
        mid_start = max(0, len(top) // 2 - 5)
        for card in top[mid_start : mid_start + 10]:
            picks.append(("mid", card))
        # extremes by |30d|
        scored = []
        for card in top:
            ch = live_metric(((card.get("windows") or {}).get("30d") or {}).get("changePct"))
            scored.append((abs(ch or 0), card))
        scored.sort(key=lambda item: item[0], reverse=True)
        for _, card in scored[:10]:
            picks.append(("extreme", card))

    seen: set[str] = set()
    results: list[dict[str, Any]] = []
    for bucket, card in picks:
        cid = str(card.get("id") or "")
        key = f"{bucket}:{cid}"
        if key in seen:
            continue
        seen.add(key)
        price = live_metric(card.get("pricePsa10"))
        pop = live_metric(card.get("populationPsa10"))
        cap = live_metric(card.get("marketCap"))
        cap_ok = None
        if price is not None and pop is not None and cap is not None:
            expected = price * pop
            cap_ok = abs(expected - cap) / max(expected, 1) < 0.01

        prices = history_price_map(card.get("historyDaily"))

        recomputed: dict[str, Any] = {}
        for w in WINDOWS:
            published = live_metric(((card.get("windows") or {}).get(w) or {}).get("changePct"))
            if len(prices) < 2:
                recomputed[w] = {"published": published, "derived": None, "match": None}
                continue
            days_sorted = sorted(prices)
            latest = days_sorted[-1]
            latest_d = date.fromisoformat(latest)
            target = latest_d - timedelta(days=WINDOW_DAYS[w])
            near = [
                (abs((date.fromisoformat(d) - target).days), d, prices[d])
                for d in days_sorted[:-1]
                if abs((date.fromisoformat(d) - target).days) <= WINDOW_TOL[w]
            ]
            derived = None
            if near:
                _, _, anchor = min(near, key=lambda x: (x[0], date.fromisoformat(x[1]) > target))
                if anchor > 0:
                    derived = round((prices[latest] / anchor - 1) * 100, 4)
            match = None
            if published is not None and derived is not None:
                match = abs(published - derived) < 1.0 or (
                    abs(published) < 1e-6 and abs(derived) < 1e-6
                )
            recomputed[w] = {"published": published, "derived": derived, "match": match}

        results.append(
            {
                "bucket": bucket,
                "rank": card.get("rank"),
                "id": cid,
                "name": ((card.get("names") or {}).get("en") or "")[:40],
                "price": price,
                "pop": pop,
                "cap": cap,
                "capEqualsPriceTimesPop": cap_ok,
                "windows": recomputed,
                "histPoints": len(prices),
            }
        )
    return results


def verdicts(report: dict[str, Any]) -> dict[str, str]:
    a = report["A"]["watchlist"]
    b = report["B"]
    c_fe = report["C"]["snapshot"]["top100"]
    d = report["D"]["snapshot"]

    # Q1
    watch = a["watch"]
    strict = a["strict_price"]
    rate = strict / watch if watch else 0
    fake = b["fakeTodayCount"]
    forbidden = b["forbiddenSourceHits"]
    if rate >= 0.97 and fake < 50 and not forbidden:
        q1 = "PASS"
    elif rate >= 0.90:
        q1 = "PARTIAL"
    else:
        q1 = "FAIL"

    # Q2
    ready_rates = []
    for w in WINDOWS:
        n = c_fe["n"] or 1
        ready = c_fe["windows"].get(w, {}).get("ready", 0) + c_fe["windows"].get(w, {}).get("stale", 0)
        ready_rates.append(ready / n)
    fake0 = c_fe.get("fakeZeroCount", 0)
    if min(ready_rates) >= 0.95 and fake0 == 0:
        q2 = "PASS"
    elif min(ready_rates) >= 0.70:
        q2 = "PARTIAL"
    else:
        q2 = "FAIL"

    # Q3
    extreme = d.get("top100_abs30d_gt200", 0)
    if extreme == 0 and d.get("flagCount", 0) < 15:
        q3 = "PASS"
    elif extreme <= 3:
        q3 = "PARTIAL"
    else:
        q3 = "FAIL"

    return {"Q1_coverage": q1, "Q2_windows": q2, "Q3_outliers": q3}


def main() -> int:
    parser = argparse.ArgumentParser(description="READ-ONLY full price review")
    parser.add_argument("--snapshot", type=Path, default=None)
    parser.add_argument("--out-dir", type=Path, default=None)
    parser.add_argument("--today", type=str, default=None, help="YYYY-MM-DD override for fake-today")
    args = parser.parse_args()

    as_of = utc_now()
    day = date.fromisoformat(args.today) if args.today else date.today()
    out_dir = args.out_dir or (ROOT / "docs" / "evidence" / f"{day.isoformat()}-price-review")
    out_dir = out_dir if out_dir.is_absolute() else ROOT / out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    snap_path = resolve_snapshot_path(args.snapshot)
    doc = json.loads(snap_path.read_text(encoding="utf-8"))

    print(f"[price-review] asOf={as_of} today={day} snapshot={snap_path.relative_to(ROOT)}")
    print(f"[price-review] evidence → {out_dir.relative_to(ROOT)}")

    conn = connect()
    try:
        cur = conn.cursor()
        print("[A] baseline…")
        A = phase_a(cur)
        atomic_json(out_dir / "A_baseline.json", A)

        print("[B] coverage / fake-today…")
        B = phase_b(cur, day)
        atomic_json(out_dir / "B_no_price.json", {"count": B["noPriceCount"], "rows": B["noPrice"]})
        write_jsonl(out_dir / "B_fake_today.jsonl", B["fakeTodaySample"])
        atomic_json(
            out_dir / "B_coverage.json",
            {
                "noPriceCount": B["noPriceCount"],
                "fakeTodayCount": B["fakeTodayCount"],
                "fakeTodayBySource": B["fakeTodayBySource"],
                "bulkTodayClusters": B["bulkTodayClusters"],
                "unknownSources": B["unknownSources"],
                "forbiddenSourceHits": B["forbiddenSourceHits"],
            },
        )

        print("[C] pool depth + snapshot windows…")
        C_pool = phase_c_pool_depth(cur)
        C_snap = phase_c_snapshot(doc)
        C = {"pool": C_pool, "snapshot": C_snap}
        atomic_json(out_dir / "C_window_coverage.json", C)

        print("[D] outliers…")
        D_snap = phase_d_snapshot(doc)
        D_db = phase_d_db_outliers(cur)
        D = {"snapshot": D_snap, "db": D_db}
        write_csv(
            out_dir / "D_outliers.csv",
            D_snap["flags"],
            [
                "bucket",
                "rank",
                "id",
                "name",
                "pricePsa10",
                "marketCap",
                "pct_1d",
                "pct_7d",
                "pct_30d",
                "reasons",
            ],
        )
        atomic_json(out_dir / "D_db_outliers.json", D_db)

        print("[E] sample…")
        E = phase_e_sample(doc, cur)
        atomic_json(out_dir / "E_sample_audit.json", E)
    finally:
        conn.close()

    report = {
        "asOf": as_of,
        "today": day.isoformat(),
        "snapshotPath": str(snap_path.relative_to(ROOT)).replace("\\", "/"),
        "A": A,
        "B": {
            "noPriceCount": B["noPriceCount"],
            "fakeTodayCount": B["fakeTodayCount"],
            "fakeTodayBySource": B["fakeTodayBySource"],
            "bulkTodayClusters": B["bulkTodayClusters"],
            "unknownSources": B["unknownSources"],
            "forbiddenSourceHits": B["forbiddenSourceHits"],
            "noPriceClassified": {
                "noTplNoSnk": sum(1 for r in B["noPrice"] if not r["has_tpl_id"] and not r["has_snk_id"]),
                "tplOnly": sum(1 for r in B["noPrice"] if r["has_tpl_id"] and not r["has_snk_id"]),
                "snkOnly": sum(1 for r in B["noPrice"] if r["has_snk_id"] and not r["has_tpl_id"]),
                "bothIds": sum(1 for r in B["noPrice"] if r["has_tpl_id"] and r["has_snk_id"]),
            },
        },
        "C": C,
        "D": {
            "snapshot": {
                "flagCount": D_snap["flagCount"],
                "top100_abs30d_gt200": D_snap["top100_abs30d_gt200"],
                "topFlags": D_snap["flags"][:25],
            },
            "db": {
                "crossSourceSpreadGt5x": D_db["crossSourceSpreadGt5x"],
                "riskyVsTrustedGt5x": D_db["riskyVsTrustedGt5x"],
                "crossSourceSample": D_db["crossSourceSample"][:15],
                "riskySample": D_db["riskySample"][:15],
            },
        },
        "E": E,
        "layers": {
            "note": "parallel counts — not the same pool",
            "watchlist": A["watchlist"]["watch"],
            "strictPrice": A["watchlist"]["strict_price"],
            "anyPrice": A["watchlist"]["any_price"],
            "universeMembers": A.get("universeMembers"),
            "catalogVariants": A.get("catalogVariants"),
            "feSet": C_snap["feSetN"],
        },
    }
    report["verdicts"] = verdicts(report)
    atomic_json(out_dir / "report.json", report)

    # stdout summary
    v = report["verdicts"]
    w = A["watchlist"]
    print()
    print("=== PRICE FULL REVIEW ===")
    print(f"asOf: {as_of}")
    print(f"layers: watch={w['watch']} strict_price={w['strict_price']} any_price={w['any_price']} "
          f"universe={A.get('universeMembers')} catalog={A.get('catalogVariants')} feSet={C_snap['feSetN']}")
    print(f"sources: TPL={w['tcgpricelookup']} SNK={w['snk_psa10']} ebay={w['ebay']} "
          f"g10={w['g10_kline']} tcgfish={w['tcgfish']}")
    print(f"no_price: {B['noPriceCount']}  fake_today_same_price: {B['fakeTodayCount']} {B['fakeTodayBySource']}")
    print(f"forbidden sources: {B['forbiddenSourceHits'] or 'none'}")
    pd = C_pool["poolDepth"]
    print(f"pool depth can 1d/7d/30d: {pd['can_1d']}/{pd['can_7d']}/{pd['can_30d']} "
          f"(bars30d>=1: {pd['bars30d_ge1']})")
    for label, block in (("top100", C_snap["top100"]), ("watchlist", C_snap["watchlist"])):
        wr = {win: block["windows"].get(win, {}) for win in WINDOWS}
        print(f"FE {label} n={block['n']} priceReady={block['priceReady']} windows={wr} "
              f"fakeZero={block.get('fakeZeroCount')}")
    print(f"outliers: flags={D_snap['flagCount']} top100_|30d|>200%={D_snap['top100_abs30d_gt200']} "
          f"db_cross_5x={D_db['crossSourceSpreadGt5x']} risky_5x={D_db['riskyVsTrustedGt5x']}")
    print(f"VERDICTS: Q1={v['Q1_coverage']} Q2={v['Q2_windows']} Q3={v['Q3_outliers']}")
    print(f"report: {(out_dir / 'report.json').relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        cfg = db_config()
        print(scrub(f"FATAL: {exc}", cfg.get("password") or ""), file=sys.stderr)
        raise
