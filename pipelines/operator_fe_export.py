#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""FE-facing projection helpers for operator_control export."""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any

EXACT_PRICE_SOURCES = ("snk_psa10", "snk", "snkrdunk", "ebay", "pricecharting", "tcgpricelookup")
BANNED_PRICE_SOURCES = ("g10_kline",)
WINDOW_DEFS = {"1d": (1, 2), "7d": (7, 3), "30d": (30, 5)}
GRADERS = ("PSA", "BGS", "CGC", "SGC", "TAG")


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def metric(value, status, as_of, **extra):
    out = {"value": value, "status": status, "asOf": as_of}
    out.update(extra)
    return out


def iso_day(d) -> str | None:
    if d is None:
        return None
    if isinstance(d, datetime):
        return d.date().isoformat()
    if isinstance(d, date):
        return d.isoformat()
    s = str(d)
    return s[:10] if s else None


def asof_iso(v) -> str | None:
    if v is None:
        return None
    if isinstance(v, datetime):
        if v.tzinfo is None:
            return v.replace(tzinfo=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        return v.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if isinstance(v, date):
        return f"{v.isoformat()}T00:00:00Z"
    s = str(v).replace(" ", "T")
    if s.endswith("+00:00"):
        s = s[:-6] + "Z"
    if len(s) == 10:
        s += "T00:00:00Z"
    return s


def pick_price_by_day(rows: list[dict]) -> dict[str, dict]:
    order = {s: i for i, s in enumerate(EXACT_PRICE_SOURCES)}
    by_day: dict[str, dict] = {}
    for r in rows:
        day = iso_day(r.get("observed_date"))
        if not day:
            continue
        try:
            px = float(r["price_usd"]) if r.get("price_usd") is not None else None
        except Exception:
            px = None
        if px is None or px <= 0:
            continue
        src = str(r.get("source_code") or "")
        if src in BANNED_PRICE_SOURCES or str(r.get("metric_status") or "") == "banned_g10_kline":
            continue
        prev = by_day.get(day)
        if prev is None or order.get(src, 99) < order.get(str(prev.get("source_code")), 99):
            by_day[day] = {"source_code": src, "price_usd": px, "effective_at": r.get("effective_at")}
    return by_day


def nearest_day_price(by_day: dict[str, dict], target: date, tol_days: int):
    best = None
    best_delta = None
    for day_s, row in by_day.items():
        try:
            d = date.fromisoformat(day_s)
        except Exception:
            continue
        delta = abs((d - target).days)
        if delta > tol_days:
            continue
        if best_delta is None or delta < best_delta or (delta == best_delta and d > best[0]):
            best = (d, row)
            best_delta = delta
    if not best:
        return None, None, None
    d, row = best
    return float(row["price_usd"]), asof_iso(row.get("effective_at") or d), d


def pct_change(new, old):
    if new is None or old is None or old == 0:
        return None
    return (new / old - 1.0) * 100.0


def compose_cap_change(price_pct, pop_pct):
    if price_pct is None or pop_pct is None:
        return None
    return ((1.0 + price_pct / 100.0) * (1.0 + pop_pct / 100.0) - 1.0) * 100.0


def sales_window(sales_by_day: dict[str, dict], end: date, days: int):
    start = end - timedelta(days=days - 1)
    total_val = 0.0
    total_cnt = 0
    has = False
    last = None
    for day_s, row in sales_by_day.items():
        try:
            d = date.fromisoformat(day_s)
        except Exception:
            continue
        if start <= d <= end:
            has = True
            total_val += float(row.get("value") or 0)
            total_cnt += int(row.get("count") or 0)
            last = max(last or d, d)
    if not has:
        return None, None, None
    return total_val, total_cnt, asof_iso(last)


def load_bulk(cur, vids: list[int]) -> dict[str, Any]:
    empty = {"price_rows": {}, "sales_days": {}, "pop_rows": {}, "printing": {}, "locales": {}, "ungraded": {}, "fx": {}}
    if not vids:
        return empty
    ph = ",".join(["%s"] * len(vids))
    banned = ",".join(f"'{s}'" for s in BANNED_PRICE_SOURCES)

    cur.execute(
        f"""
        SELECT variant_id, source_code, observed_date, price_usd, effective_at, metric_status
        FROM market_price_observation
        WHERE variant_id IN ({ph})
          AND price_usd IS NOT NULL AND price_usd > 0
          AND source_code NOT IN ({banned})
          AND metric_status <> 'banned_g10_kline'
        ORDER BY observed_date ASC
        """,
        tuple(vids),
    )
    price_rows: dict[int, list] = defaultdict(list)
    for r in cur.fetchall():
        price_rows[int(r["variant_id"])].append(r)

    cur.execute(
        f"""
        SELECT variant_id, observed_date, SUM(sales_count) AS sales_count, SUM(sales_value_usd) AS sales_value_usd
        FROM market_daily_sales_aggregate
        WHERE variant_id IN ({ph})
        GROUP BY variant_id, observed_date
        """,
        tuple(vids),
    )
    sales_days: dict[int, dict[str, dict]] = defaultdict(dict)
    for r in cur.fetchall():
        day = iso_day(r.get("observed_date"))
        if day:
            sales_days[int(r["variant_id"])][day] = {
                "count": int(r["sales_count"] or 0),
                "value": float(r["sales_value_usd"] or 0),
            }

    cur.execute(
        f"""
        SELECT variant_id, grader_code, observed_date, top_grade_population, total_population, top_grade_label, effective_at
        FROM market_grader_population_observation
        WHERE variant_id IN ({ph})
        ORDER BY observed_date ASC
        """,
        tuple(vids),
    )
    pop_rows: dict[int, dict[str, list]] = defaultdict(lambda: defaultdict(list))
    for r in cur.fetchall():
        pop_rows[int(r["variant_id"])][str(r["grader_code"]).upper()].append(r)

    cur.execute(
        f"""
        SELECT variant_id, tcg_code, card_language, set_name, collector_number, edition_code, parallel_code, finish_code,
               canonical_printing_sha256, evidence_sha256, identity_status
        FROM catalog_printing_identity
        WHERE variant_id IN ({ph})
        """,
        tuple(vids),
    )
    printing = {int(r["variant_id"]): r for r in cur.fetchall()}

    cur.execute(
        f"""
        SELECT variant_id, locale_code, localized_name, localized_set_name, market_story
        FROM catalog_variant_locale
        WHERE variant_id IN ({ph})
        """,
        tuple(vids),
    )
    locales: dict[int, dict[str, dict]] = defaultdict(dict)
    for r in cur.fetchall():
        locales[int(r["variant_id"])][str(r["locale_code"])] = r

    cur.execute(
        f"""
        SELECT u.variant_id, u.price_usd, u.observed_at, u.source_code
        FROM market_ungraded_reference_price u
        INNER JOIN (
            SELECT variant_id, MAX(observed_at) mx
            FROM market_ungraded_reference_price
            WHERE variant_id IN ({ph})
            GROUP BY variant_id
        ) t ON t.variant_id=u.variant_id AND t.mx=u.observed_at
        """,
        tuple(vids),
    )
    ungraded = {int(r["variant_id"]): r for r in cur.fetchall()}

    cur.execute(
        """
        SELECT f.quote_currency, f.rate, f.effective_at
        FROM market_fx_rate_observation f
        INNER JOIN (
            SELECT quote_currency, MAX(effective_at) mx
            FROM market_fx_rate_observation
            WHERE base_currency='USD'
            GROUP BY quote_currency
        ) t ON t.quote_currency=f.quote_currency AND t.mx=f.effective_at
        WHERE f.base_currency='USD'
        """
    )
    fx = {str(r["quote_currency"]).upper(): r for r in cur.fetchall()}
    return {
        "price_rows": price_rows,
        "sales_days": sales_days,
        "pop_rows": pop_rows,
        "printing": printing,
        "locales": locales,
        "ungraded": ungraded,
        "fx": fx,
    }


def build_history_windows_graders(vid: int, bulk: dict, latest_price: dict | None, latest_pop: dict | None):
    by_day = pick_price_by_day(bulk["price_rows"].get(vid) or [])
    sales_by_day = bulk["sales_days"].get(vid) or {}
    days = sorted(by_day.keys())
    history = []
    for day in days[-120:]:
        sale = sales_by_day.get(day) or {}
        scount = sale.get("count")
        sval = sale.get("value")
        history.append(
            {
                "at": f"{day}T00:00:00Z",
                "priceUsd": by_day[day]["price_usd"],
                "priceStatus": "ready",
                "trackedSalesValueUsd": float(sval) if sval is not None else None,
                "trackedSalesCount": int(scount) if scount is not None else None,
                "salesCoverage": "partial" if (scount is not None or sval is not None) else "unavailable",
            }
        )

    if latest_price and latest_price.get("price_usd") is not None:
        cur_px = float(latest_price["price_usd"])
        cur_asof = asof_iso(latest_price.get("effective_at"))
        try:
            cur_day = date.fromisoformat(str(latest_price.get("observed_date") or cur_asof or "")[:10])
        except Exception:
            cur_day = date.fromisoformat(days[-1]) if days else date.today()
    elif days:
        cur_day = date.fromisoformat(days[-1])
        cur_px = float(by_day[days[-1]]["price_usd"])
        cur_asof = asof_iso(by_day[days[-1]].get("effective_at") or cur_day)
    else:
        cur_day = date.today()
        cur_px = None
        cur_asof = None

    pop_psa = bulk["pop_rows"].get(vid, {}).get("PSA") or []
    pop_by_day = {}
    for r in pop_psa:
        day = iso_day(r.get("observed_date"))
        if day and r.get("top_grade_population") is not None:
            pop_by_day[day] = int(r["top_grade_population"])
    if latest_pop and latest_pop.get("top_grade_population") is not None:
        cur_pop = int(latest_pop["top_grade_population"])
        cur_pop_asof = asof_iso(latest_pop.get("effective_at") or latest_pop.get("observed_date"))
    elif pop_by_day:
        last = sorted(pop_by_day.keys())[-1]
        cur_pop = pop_by_day[last]
        cur_pop_asof = f"{last}T00:00:00Z"
    else:
        cur_pop = None
        cur_pop_asof = None

    windows = {}
    for code, (days_n, tol) in WINDOW_DEFS.items():
        old_px, old_asof, _ = nearest_day_price(by_day, cur_day - timedelta(days=days_n), tol)
        price_pct = pct_change(cur_px, old_px)
        price_status = "ready" if price_pct is not None else ("accumulating" if cur_px is not None else "unavailable")
        # Daily chase = current POP level. Single-card growth is derived from daily PSA points.
        target_pop_day = cur_day - timedelta(days=days_n)
        old_pop = None
        if pop_by_day:
            best = None
            for day_s, val in pop_by_day.items():
                d = date.fromisoformat(day_s)
                delta = abs((d - target_pop_day).days)
                if delta <= tol and (best is None or delta < best[0]):
                    best = (delta, val, day_s)
            if best:
                old_pop = best[1]
        pop_pct = pct_change(float(cur_pop) if cur_pop is not None else None, float(old_pop) if old_pop is not None else None)
        cap_pct = compose_cap_change(price_pct, pop_pct)
        if cap_pct is not None:
            cap_status, cap_val = "ready", cap_pct
            cap_asof = cur_asof
        elif price_pct is not None:
            cap_status, cap_val, cap_asof = price_status, price_pct, cur_asof
        else:
            cap_status = "accumulating" if cur_px is not None or cur_pop is not None else "unavailable"
            cap_val, cap_asof = None, None
        sval, scount, sasof = sales_window(sales_by_day, cur_day, days_n)
        pval, _, _ = sales_window(sales_by_day, cur_day - timedelta(days=days_n), days_n)
        sales_pct = pct_change(sval, pval)
        sales_status = "ready" if sval is not None else "unavailable"
        windows[code] = {
            "changePct": metric(price_pct, price_status, cur_asof if price_pct is not None else None, **({"anchorAt": old_asof} if old_asof else {})),
            "marketCapChangePct": metric(
                cap_val,
                cap_status,
                cap_asof if cap_val is not None else None,
                **({"anchorAt": old_asof} if old_asof and cap_val is not None and price_pct is not None else {}),
            ),
            "trackedSalesChangePct": metric(
                sales_pct,
                "ready" if sales_pct is not None else ("accumulating" if sval is not None else "unavailable"),
                sasof if sales_pct is not None else None,
            ),
            "trackedSales": {
                "valueUsd": metric(sval, sales_status, sasof),
                "count": metric(scount, sales_status, sasof),
                "coverage": "partial" if sval is not None else "unavailable",
                "asOf": sasof,
            },
        }

    grader_out = {}
    for g in GRADERS:
        series = bulk["pop_rows"].get(vid, {}).get(g) or []
        latest = series[-1] if series else None
        if g == "PSA":
            top_val = cur_pop if cur_pop is not None else (int(latest["top_grade_population"]) if latest and latest.get("top_grade_population") is not None else None)
            total_val = int(latest["total_population"]) if latest and latest.get("total_population") is not None else None
            asof = cur_pop_asof or (asof_iso(latest.get("effective_at") or latest.get("observed_date")) if latest else None)
            top_grade = str((latest or {}).get("top_grade_label") or "10")
        elif latest:
            top_val = int(latest["top_grade_population"]) if latest.get("top_grade_population") is not None else None
            total_val = int(latest["total_population"]) if latest.get("total_population") is not None else None
            asof = asof_iso(latest.get("effective_at") or latest.get("observed_date"))
            top_grade = str(latest.get("top_grade_label") or "10")
        else:
            top_val = total_val = asof = None
            top_grade = "10"
        # PSA growth derives from daily POP points. Other graders: level only, no growth series work.
        changes = {}
        for code, (days_n, tol) in WINDOW_DEFS.items():
            if g != "PSA" or top_val is None:
                changes[code] = metric(None, "unavailable", None)
                continue
            target = cur_day - timedelta(days=days_n)
            best = None
            for day_s, val in pop_by_day.items():
                d = date.fromisoformat(day_s)
                delta = abs((d - target).days)
                if delta <= tol and (best is None or delta < best[0]):
                    best = (delta, val, day_s)
            if not best:
                changes[code] = metric(None, "accumulating", asof)
                continue
            pct = pct_change(float(top_val), float(best[1]))
            changes[code] = metric(
                pct,
                "ready" if pct is not None else "unavailable",
                asof,
                **({"anchorAt": f"{best[2]}T00:00:00Z"} if pct is not None else {}),
            )
        grader_out[g] = {
            "topGrade": top_grade or "10",
            "total": {**metric(total_val, "ready" if total_val is not None else "unavailable", asof), "estimated": False},
            "topGradePopulation": {**metric(top_val, "ready" if top_val is not None else "unavailable", asof), "estimated": False},
            "topGradePopulationChangePct": changes,
        }
    return history, windows, grader_out


def coverage_from_cards(cards, top, watch):
    def ready_change(code):
        return sum(1 for c in cards if ((c.get("windows") or {}).get(code) or {}).get("changePct", {}).get("value") is not None)

    def ready_sales(code):
        return sum(
            1
            for c in cards
            if ((((c.get("windows") or {}).get(code) or {}).get("trackedSales") or {}).get("valueUsd") or {}).get("value") is not None
        )

    def grader_ready(g):
        return sum(1 for c in cards if ((((c.get("graderPopulations") or {}).get(g) or {}).get("topGradePopulation") or {}).get("value") is not None))

    def grader_change_ready(g, code):
        return sum(
            1
            for c in cards
            if ((((c.get("graderPopulations") or {}).get(g) or {}).get("topGradePopulationChangePct") or {}).get(code) or {}).get("value") is not None
        )

    return {
        "claim": "verified-top-n",
        "requestedCount": 100,
        "verifiedCount": len(top),
        "top100Count": len(top),
        "watchlistCount": len(watch),
        "changeReady": {"1d": ready_change("1d"), "7d": ready_change("7d"), "30d": ready_change("30d")},
        "salesReady": {"1d": ready_sales("1d"), "7d": ready_sales("7d"), "30d": ready_sales("30d")},
        "graderPopulationReady": {g: grader_ready(g) for g in GRADERS},
        "graderPopulationChangeReady": {g: {"1d": grader_change_ready(g, "1d"), "7d": grader_change_ready(g, "7d"), "30d": grader_change_ready(g, "30d")} for g in GRADERS},
        "completeIdentityCount": sum(1 for c in cards if c.get("identityStatus") == "confirmed"),
        "localizedStoryCount": {
            "en": sum(1 for c in cards if (c.get("stories") or {}).get("en")),
            "ja": sum(1 for c in cards if (c.get("stories") or {}).get("ja")),
            "zhCN": sum(1 for c in cards if (c.get("stories") or {}).get("zhCN")),
            "zhTW": sum(1 for c in cards if (c.get("stories") or {}).get("zhTW")),
            "ko": sum(1 for c in cards if (c.get("stories") or {}).get("ko")),
        },
    }


def currencies_block(now: str, fx: dict | None = None):
    fx = fx or {}
    def rate(code, fallback):
        row = fx.get(code)
        if row and row.get("rate") is not None:
            return metric(float(row["rate"]), "ready", asof_iso(row.get("effective_at")) or now)
        return metric(fallback, "ready", now)
    return {
        "base": "USD",
        "supported": ["USD", "HKD", "CNY", "GBP", "TWD", "JPY", "KRW"],
        "rates": {
            "USD": metric(1, "ready", now),
            "HKD": rate("HKD", 7.8),
            "CNY": rate("CNY", 7.2),
            "GBP": rate("GBP", 0.78),
            "TWD": rate("TWD", 32.0),
            "JPY": rate("JPY", 150.0),
            "KRW": rate("KRW", 1350.0),
        },
        "asOf": now,
    }
