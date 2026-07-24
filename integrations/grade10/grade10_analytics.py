"""
Grade10 analytics — computes price change % (1d/7d/30d) and transaction volume
from scraped sale history data.

Usage:
    python grade10_analytics.py                # full analysis → JSON + CSV + summary
    python grade10_analytics.py --top 20       # top N by volume

Output:
    data/analytics/card_metrics.json           # full per-card metrics
    data/analytics/card_metrics.csv            # flat CSV for Excel/Sheets
    data/analytics/summary.json                # aggregate stats + top movers
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("analytics")

DATA_DIR = Path(__file__).parent / "data"
ANALYTICS_DIR = DATA_DIR / "analytics"

GRADE_MAP = {
    -1: "All", 20: "C", 21: "D", 22: "PSA 10", 23: "PSA 9",
    24: "PSA 8 or Below", 25: "BGS 10 BL", 26: "BGS 10 GL",
    27: "BGS 9.5", 28: "BGS 9 or Below", 29: "ARS 10+",
    30: "ARS 10", 31: "ARS 9", 32: "ARS 8 or Below",
}

# Canonical grade labels used to group sales into per-grade price series.
# Grade names across files vary in formatting — normalize everything to these.
GRADE_ALIASES = {
    "psa 10": "PSA 10", "psa10": "PSA 10",
    "psa 9": "PSA 9", "psa9": "PSA 9",
    "psa 8": "PSA 8 or Below", "psa 8 or below": "PSA 8 or Below",
    "psa8": "PSA 8 or Below",
    "bgs 10": "BGS 10", "bgs bl": "BGS 10", "bgs 10 bl": "BGS 10",
    "bgs black label": "BGS 10", "bgs 10 black label": "BGS 10",
    "bgs 10 gl": "BGS 10", "bgs gold label": "BGS 10",
    "bgs 9.5": "BGS 9.5", "bgs9.5": "BGS 9.5",
    "bgs 9": "BGS 9 or Below", "bgs 9 or below": "BGS 9 or Below",
    "cgc 10": "CGC 10", "cgc bl": "CGC 10", "cgc 9.5": "CGC 9.5",
    "cgc 9": "CGC 9", "cgc": "CGC",
    "ars 10+": "ARS 10+", "ars10+": "ARS 10+", "ars 10plus": "ARS 10+",
    "ars 10": "ARS 10", "ars10": "ARS 10",
    "ars 9": "ARS 9", "ars9": "ARS 9",
    "ars 8": "ARS 8 or Below", "ars 8 or below": "ARS 8 or Below",
    "sgc 10": "SGC 10", "sgc 9.5": "SGC 9.5", "sgc 9": "SGC 9",
    "sgc": "SGC", "c": "C", "d": "D",
    "ungraded": "Ungraded", "raw": "Ungraded", "": "Unknown",
}
DEFAULT_SERIES_GRADE = "PSA 10"  # matches site UI default
EBAY_FILE_GRADE = {
    "PSA_10": "PSA 10", "PSA_9": "PSA 9", "BGS_10": "BGS 10",
    "BGS_BL": "BGS 10", "CGC_10": "CGC 10", "CGC_BL": "CGC 10",
    "ARS_10plus": "ARS 10+", "ARS_10": "ARS 10", "Ungraded": "Ungraded",
}
GRADE_TO_NUM = {v: k for k, v in GRADE_MAP.items()}

NOW = datetime.now(timezone.utc)

# ---------------------------------------------------------------------------
# Date parsing
# ---------------------------------------------------------------------------

_RELATIVE_RE = re.compile(
    r"^(?:(\d+)\s+(second|minute|hour|day|week|month|year)s?\s+ago)|just now$",
    re.IGNORECASE,
)
_UNIT_SECONDS = {
    "second": 1, "minute": 60, "hour": 3600,
    "day": 86400, "week": 604800,
    "month": 2592000, "year": 31536000,  # approximate
}


def parse_sale_date(raw: str) -> datetime | None:
    """Parse '19 hours ago' / '2 days ago' / '2026-07-14' → datetime."""
    raw = raw.strip()
    if not raw:
        return None

    # ISO date
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S.%f%z"):
        try:
            dt = datetime.strptime(raw, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            continue

    # Relative "N units ago"
    m = _RELATIVE_RE.match(raw)
    if m:
        if "just now" in raw.lower():
            return NOW
        n, unit = int(m.group(1)), m.group(2).lower()
        return NOW - timedelta(seconds=_UNIT_SECONDS.get(unit, 86400) * n)

    return None


# ---------------------------------------------------------------------------
# Per-card analysis
# ---------------------------------------------------------------------------

def normalize_grade(raw: str) -> str:
    """Map any grade label variant to a canonical grade series name."""
    key = re.sub(r"\s+", " ", (raw or "").strip().lower())
    return GRADE_ALIASES.get(key, raw.strip().title() if raw else "Unknown")


# Relative dates ("19 hours ago") are rolling — a sale stamped "2 days ago"
# today will still say "2 days ago" next month, while the same sale in an
# older fetch carries its real ISO date. Keeping both calendars would shift
# that sale between days as time passes, so anything inside the window where
# relative stamps appear (RELATIVE_DATE_WINDOW_DAYS) is bucketed by fetch
# day instead. Everything beyond the window is ISO-dated and safe.
RELATIVE_DATE_WINDOW_DAYS = 42

# "K 線用"銷售紀錄快取：每次爬蟲後 load_sales 嘅結果寫入 sales_cache/，
# 歷史成交得以跨爬蟲累積（平台 saleHistory 係 ~20 筆滾動窗口，唔快取就會丟失）。
_SALES_CACHE_VERSION = 1


def sale_day(s: dict) -> str | None:
    """Bucket a sale into a calendar day, or None if it can't be placed.

    A sale already has a `day` when it was seen in a previous scrape (set by
    load_sales to that scrape's date) — keep it. Otherwise: ISO dates inside
    the relative window are new to this scrape, so they belong to today
    (their raw date is just the platform's label for a rolling bucket);
    anything older is safely ISO and uses its own date.
    """
    if s.get("day"):
        return s["day"]
    if s["dt"] >= NOW - timedelta(days=RELATIVE_DATE_WINDOW_DAYS):
        return NOW.strftime("%Y-%m-%d")
    return s["dt"].strftime("%Y-%m-%d")


def _sales_cache_path(card_dir: Path) -> Path:
    rel = card_dir.relative_to(DATA_DIR / "cards")
    return DATA_DIR / "sales_cache" / rel.parent / f"{rel.name}.json"


def load_sales(card_dir: Path) -> list[dict]:
    """Load all sales for a card: current scrape files merged with the
    rolling cache of previous scrapes (deduped, newest first).

    Apparel files with grade=-1 (All) are skipped — their saleHistory is the
    same transactions already present in the per-grade files (counting them
    would double-count volume).

    Platform quirk: eBay grade queries are keyword filters, not exact matches
    — ebay_PSA_10.json can contain raw/ungraded sales that merely mention
    "PSA 10" (e.g. a $190 raw Chopper listing inside a $2,500 PSA-10 series).
    The per-sale `grade` field is NOT authoritative there. eBay sales are
    therefore assigned the queried file's grade ONLY if the price is
    consistent with the file's own averagePrice (>= 25% of it); cheaper
    sales are misclassified listings and go to the "Ungraded" bucket so
    they still count toward volume but never pollute the price series.
    """
    today = NOW.strftime("%Y-%m-%d")
    sales = []

    # Snkrdunk apparel files (skip grade -1 "All" — duplicate stream)
    apparel_files = sorted(card_dir.glob("apparel_grade_*.json"))
    for f in apparel_files:
        grade_num = int(f.stem.rsplit("_", 1)[-1])
        if grade_num == -1:
            continue
        file_grade = GRADE_MAP.get(grade_num, "Unknown")
        data = json.loads(f.read_text(encoding="utf-8"))
        for s in data.get("saleHistory", []):
            dt = parse_sale_date(s.get("date", ""))
            if dt:
                sales.append({
                    "dt": dt,
                    "price": s.get("txAmount") or s.get("price", 0),
                    "grade": normalize_grade(s.get("grade") or file_grade),
                    "platform": "snkrdunk",
                    "day": today,
                })

    # eBay files — file name carries the queried grade
    ebay_files = sorted(card_dir.glob("ebay_*.json"))
    for f in ebay_files:
        file_grade = EBAY_FILE_GRADE.get(f.stem.replace("ebay_", ""), "Unknown")
        data = json.loads(f.read_text(encoding="utf-8"))
        avg_price = (data.get("averagePrice") or {}).get("price")
        for s in data.get("saleHistory", []):
            dt = parse_sale_date(s.get("date", ""))
            if dt:
                price = s.get("price", 0)
                grade = file_grade
                if avg_price and price < avg_price * 0.25 and file_grade != "Ungraded":
                    grade = "Ungraded"
                sales.append({
                    "dt": dt,
                    "price": price,
                    "grade": grade,
                    "platform": "ebay",
                    "day": today,
                })

    # Merge rolling cache (previous scrape days)
    cache_path = _sales_cache_path(card_dir)
    if cache_path.exists():
        try:
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            for s in cached.get("sales", []):
                dt = datetime.fromisoformat(s["dt"])
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                sales.append({
                    "dt": dt,
                    "price": s["price"],
                    "grade": s["grade"],
                    "platform": s["platform"],
                    "day": s["day"],
                })
        except (json.JSONDecodeError, KeyError, ValueError):
            log.warning("corrupt sales cache ignored: %s", cache_path)

    # Deduplicate: same (day, price, grade, platform) = same transaction
    # NOTE: two genuinely different sales on the same day at the same price
    # and grade are indistinguishable here — acceptable at daily granularity.
    seen = set()
    unique = []
    for s in sales:
        day = sale_day(s)
        if day is None:
            continue
        s["day"] = day
        key = (day, s["price"], s["grade"], s["platform"])
        if key not in seen:
            seen.add(key)
            unique.append(s)

    # Relative-window sale dates are platform labels, not transaction
    # timestamps (a "2026-07-10" stamp just identifies the ~10-days-ago
    # bucket this scrape). Sorting by them would fabricate an order, so
    # sort by our own discovery calendar: newest scrape day first; for the
    # ISO region beyond the window, the real date breaks ties.
    unique.sort(key=lambda x: (x["day"], x["dt"]), reverse=True)

    # Persist merged cache for future runs
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps({
        "version": _SALES_CACHE_VERSION,
        "updatedAt": NOW.isoformat(),
        "sales": [
            {
                "dt": s["dt"].isoformat(),
                "day": s["day"],
                "price": s["price"],
                "grade": s["grade"],
                "platform": s["platform"],
            }
            for s in unique
        ],
    }, ensure_ascii=False), encoding="utf-8")

    return unique

    # Deduplicate: same (hour, price, grade, platform) = same transaction
    seen = set()
    unique = []
    for s in sales:
        key = (s["dt"].strftime("%Y-%m-%d %H"), s["price"], s["grade"], s["platform"])
        if key not in seen:
            seen.add(key)
            unique.append(s)

    unique.sort(key=lambda x: x["dt"], reverse=True)
    return unique


def daily_avg_prices(card_dir: Path, grade: str) -> list[float]:
    """Return the platform's per-day average prices for a grade, newest first.

    Snkrdunk apparel files carry averagePrice = the current day's mean for
    that grade; the rolling cache doesn't store these, so only the latest
    day is available until tomorrow's scrape adds another. eBay files'
    averagePrice is a window average (not per-day) and is excluded.
    """
    avgs = []
    for f in sorted(card_dir.glob("apparel_grade_*.json"),
                    key=lambda p: p.stat().st_mtime, reverse=True):
        grade_num = int(f.stem.rsplit("_", 1)[-1])
        if GRADE_MAP.get(grade_num) != grade:
            continue
        data = json.loads(f.read_text(encoding="utf-8"))
        price = (data.get("averagePrice") or {}).get("price")
        if price:
            avgs.append(price)
    return avgs


def compute_metrics(sales: list[dict], daily_avgs: list[float] | None = None) -> dict:
    """Compute 1d/7d/30d change% and volumes from parsed sales.

    Change% comes from a single-grade price series (PSA 10 by default, else
    the grade with the most sales) — mixing grades in one window compares
    different products (a PSA 10 Charizard sells for ~3x a PSA 9), which
    manufactures fake moves. Volumes aggregate across all grades/platforms.

    daily_avgs: platform-provided per-day average prices (apparel
    averagePrice), used for the 1d change when available — more stable than
    comparing scrape-day buckets.
    """
    empty = {
        "latestPrice": None,
        "change1dPct": None, "change7dPct": None, "change30dPct": None,
        "volume1d": 0, "volume7d": 0, "volume30d": 0,
        "volume1dUsd": 0, "volume7dUsd": 0, "volume30dUsd": 0,
        "avgPrice7d": None, "avgPrice30d": None,
        "saleCount": 0,
        "seriesGrade": None, "seriesSaleCount": 0, "seriesPlatform": None,
    }
    if not sales:
        return empty

    now = NOW
    cut_1d = now - timedelta(days=1)
    cut_7d = now - timedelta(days=7)
    cut_30d = now - timedelta(days=30)

    # Volumes: all grades, all platforms
    within_1d = [s for s in sales if s["dt"] >= cut_1d]
    within_7d = [s for s in sales if s["dt"] >= cut_7d]
    within_30d = [s for s in sales if s["dt"] >= cut_30d]

    # Price series: single grade. If a card's per-platform price levels
    # diverge (>2x between series medians), the platform's population is
    # mixed (e.g. snkrdunk "PSA 10" file containing ungraded sales) — use
    # the dominant platform alone rather than blending two price levels.
    by_grade: dict[str, list[dict]] = {}
    for s in sales:
        by_grade.setdefault(s["grade"], []).append(s)
    if DEFAULT_SERIES_GRADE in by_grade:
        series_grade = DEFAULT_SERIES_GRADE
    else:
        series_grade = max(by_grade, key=lambda g: len(by_grade[g]))
    series = by_grade[series_grade]

    def median(vals: list[float]) -> float:
        v = sorted(vals)
        n = len(v)
        return v[n // 2] if n % 2 else (v[n // 2 - 1] + v[n // 2]) / 2

    by_platform: dict[str, list[float]] = {}
    for s in series:
        by_platform.setdefault(s["platform"], []).append(s["price"])
    series_platforms = ""
    if len(by_platform) > 1:
        meds = {p: median(v) for p, v in by_platform.items() if len(v) >= 3}
        if len(meds) > 1 and max(meds.values()) > 2 * min(meds.values()):
            dominant = max(by_platform, key=lambda p: len(by_platform[p]))
            series = [s for s in series if s["platform"] == dominant]
            series_platforms = dominant

    # 1d change uses the platform's own per-day aggregation (averagePrice)
    # rather than comparing scrape-day buckets — relative-window dates make
    # bucket boundaries unstable, while averagePrice is per-grade/day.
    def median(vals: list[float]) -> float:
        v = sorted(vals)
        n = len(v)
        return v[n // 2] if n % 2 else (v[n // 2 - 1] + v[n // 2]) / 2

    latest_price = median([s["price"] for s in series if s["day"] == series[0]["day"]])

    # Change%: avg of recent window vs avg of comparison window, same grade
    def avg_in_window(start_days: float, end_days: float) -> float | None:
        cutoff_start = now - timedelta(days=start_days)
        cutoff_end = now - timedelta(days=end_days)
        prices = [s["price"] for s in series if cutoff_end <= s["dt"] <= cutoff_start]
        return sum(prices) / len(prices) if prices else None

    recent_avg = avg_in_window(0, 1)  # last 24h
    if daily_avgs:
        recent_avg = daily_avgs[0]
    if recent_avg is None:
        recent_avg = latest_price

    recent_day = series[0]["day"]
    base_1d = None
    if daily_avgs and len(daily_avgs) >= 2:
        recent_avg = daily_avgs[0]
        base_1d = daily_avgs[1]
    else:
        prev_days = sorted({s["day"] for s in series if s["day"] < recent_day}, reverse=True)
        if prev_days:
            prev_prices = [s["price"] for s in series if s["day"] == prev_days[0]]
            base_1d = sum(prev_prices) / len(prev_prices)
    base_7d = avg_in_window(6, 8)     # 6-8 days ago
    base_30d = avg_in_window(28, 32)  # 28-32 days ago

    def pct_change(old: float | None) -> float | None:
        if old is None or old == 0:
            return None
        return round((recent_avg - old) / old * 100, 2)

    def avg(sales_list):
        return round(sum(s["price"] for s in sales_list) / len(sales_list), 2) if sales_list else None

    return {
        "latestPrice": round(latest_price, 2),
        "recentAvgPrice": round(recent_avg, 2),
        "change1dPct": pct_change(base_1d),
        "change7dPct": pct_change(base_7d),
        "change30dPct": pct_change(base_30d),
        "volume1d": len(within_1d),
        "volume7d": len(within_7d),
        "volume30d": len(within_30d),
        "volume1dUsd": round(sum(s["price"] for s in within_1d), 2),
        "volume7dUsd": round(sum(s["price"] for s in within_7d), 2),
        "volume30dUsd": round(sum(s["price"] for s in within_30d), 2),
        "avgPrice7d": avg(within_7d),
        "avgPrice30d": avg(within_30d),
        "saleCount": len(sales),
        "seriesGrade": series_grade,
        "seriesSaleCount": len(series),
        "seriesPlatform": series_platforms or "all",
    }


# ---------------------------------------------------------------------------
# Main analysis
# ---------------------------------------------------------------------------

def analyze_all() -> list[dict]:
    """Walk all card directories, compute metrics for each."""
    cards_dir = DATA_DIR / "cards"
    if not cards_dir.exists():
        log.error("No cards directory — run grade10_scraper.py first")
        return []

    # Load constituent metadata (name, set, index membership)
    card_meta = {}
    for it in ("ptcg", "ptcg100", "opcg"):
        cons_file = DATA_DIR / "index" / it / "constituents.json"
        if cons_file.exists():
            cons = json.loads(cons_file.read_text(encoding="utf-8"))
            for row in cons.get("rows", []):
                url = row.get("url", "")
                parts = url.rstrip("/").split("/")
                if len(parts) >= 2:
                    src, cid = parts[-2], parts[-1]
                    api_src = {"ebay": "altxyz"}.get(src, src)
                    key = f"{api_src}/{cid}"
                    if key not in card_meta:
                        card_meta[key] = {
                            "name": row.get("name", ""),
                            "setName": row.get("setName", ""),
                            "lang": row.get("lang", ""),
                            "indexPrice": row.get("priceUsd"),
                            "indexChange30d": row.get("change30dPct"),
                            "weightPct": row.get("weightPct"),
                            "rank": row.get("rank"),
                        }

    results = []
    for source_dir in sorted(cards_dir.iterdir()):
        if not source_dir.is_dir():
            continue
        source = source_dir.name
        for card_dir in sorted(source_dir.iterdir()):
            if not card_dir.is_dir():
                continue
            cid = card_dir.name
            key = f"{source}/{cid}"
            meta = card_meta.get(key, {})

            sales = load_sales(card_dir)
            grade_num = GRADE_TO_NUM.get(DEFAULT_SERIES_GRADE)
            has_default = (card_dir / f"apparel_grade_{grade_num}.json").exists()
            metrics = compute_metrics(
                sales,
                daily_avgs=daily_avg_prices(card_dir, DEFAULT_SERIES_GRADE) if has_default else None,
            )

            row = {
                "id": cid,
                "source": source,
                "name": meta.get("name", ""),
                "setName": meta.get("setName", ""),
                "lang": meta.get("lang", ""),
                "rank": meta.get("rank"),
                "weightPct": meta.get("weightPct"),
                "indexPriceUsd": meta.get("indexPrice"),
                "indexChange30dPct": meta.get("indexChange30d"),
                **metrics,
            }
            results.append(row)

    return results


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def save_outputs(results: list[dict], top_n: int = 0):
    ANALYTICS_DIR.mkdir(parents=True, exist_ok=True)

    # Full JSON
    json_path = ANALYTICS_DIR / "card_metrics.json"
    json_path.write_text(
        json.dumps(results, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    log.info("JSON → %s (%d cards)", json_path, len(results))

    # CSV
    if results:
        csv_path = ANALYTICS_DIR / "card_metrics.csv"
        keys = list(results[0].keys())
        with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=keys)
            writer.writeheader()
            writer.writerows(results)
        log.info("CSV  → %s", csv_path)

    # Summary
    with_price = [r for r in results if r["latestPrice"] is not None]
    with_1d = [r for r in results if r["change1dPct"] is not None]
    with_7d = [r for r in results if r["change7dPct"] is not None]
    with_30d = [r for r in results if r["change30dPct"] is not None]

    def top_by(key, n=10, reverse=True):
        valid = [r for r in results if r.get(key) is not None]
        return sorted(valid, key=lambda x: x[key], reverse=reverse)[:n]

    summary = {
        "generatedAt": NOW.isoformat(),
        "totalCards": len(results),
        "cardsWithPriceData": len(with_price),
        "cardsWith1dChange": len(with_1d),
        "cardsWith7dChange": len(with_7d),
        "cardsWith30dChange": len(with_30d),
        "totalVolume1dUsd": round(sum(r["volume1dUsd"] for r in results), 2),
        "totalVolume7dUsd": round(sum(r["volume7dUsd"] for r in results), 2),
        "totalVolume30dUsd": round(sum(r["volume30dUsd"] for r in results), 2),
        "totalTx1d": sum(r["volume1d"] for r in results),
        "totalTx7d": sum(r["volume7d"] for r in results),
        "totalTx30d": sum(r["volume30d"] for r in results),
        "topGainers1d": top_by("change1dPct", top_n or 10),
        "topLosers1d": top_by("change1dPct", top_n or 10, reverse=False),
        "topGainers7d": top_by("change7dPct", top_n or 10),
        "topLosers7d": top_by("change7dPct", top_n or 10, reverse=False),
        "topByVolume7d": top_by("volume7dUsd", top_n or 10),
        "topByVolume30d": top_by("volume30dUsd", top_n or 10),
    }

    summary_path = ANALYTICS_DIR / "summary.json"
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    log.info("Summary → %s", summary_path)

    # Print top movers to console
    n = top_n or 10
    print("\n" + "=" * 70)
    print(f"{'TOP GAINERS (7d)':<35} {'Change%':>8} {'Price':>10} {'Vol7d$':>10}")
    print("-" * 70)
    for r in summary["topGainers7d"][:n]:
        print(f"  {r['name'][:33]:<35} {r['change7dPct']:>+7.1f}% ${r['latestPrice']:>8,.0f} ${r['volume7dUsd']:>8,.0f}")

    print(f"\n{'TOP LOSERS (7d)':<35} {'Change%':>8} {'Price':>10} {'Vol7d$':>10}")
    print("-" * 70)
    for r in summary["topLosers7d"][:n]:
        print(f"  {r['name'][:33]:<35} {r['change7dPct']:>+7.1f}% ${r['latestPrice']:>8,.0f} ${r['volume7dUsd']:>8,.0f}")

    print(f"\n{'TOP VOLUME (7d)':<35} {'Tx':>4} {'Vol$':>10} {'Avg$':>10}")
    print("-" * 70)
    for r in summary["topByVolume7d"][:n]:
        print(f"  {r['name'][:33]:<35} {r['volume7d']:>4} ${r['volume7dUsd']:>8,.0f} ${r['avgPrice7d'] or 0:>8,.0f}")

    print(f"\n{'TOP VOLUME (30d)':<35} {'Tx':>4} {'Vol$':>10} {'Avg$':>10}")
    print("-" * 70)
    for r in summary["topByVolume30d"][:n]:
        print(f"  {r['name'][:33]:<35} {r['volume30d']:>4} ${r['volume30dUsd']:>8,.0f} ${r['avgPrice30d'] or 0:>8,.0f}")
    print()


def main():
    parser = argparse.ArgumentParser(description="Grade10 analytics")
    parser.add_argument("--top", type=int, default=10, help="Top N movers to show")
    args = parser.parse_args()

    log.info("Analyzing %s", DATA_DIR / "cards")
    results = analyze_all()
    if not results:
        log.error("No results")
        return

    save_outputs(results, top_n=args.top)


if __name__ == "__main__":
    main()
