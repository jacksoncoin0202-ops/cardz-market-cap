"""
Grade10 daily K-line (candlestick) generator.

Builds one OHLCV row per card per day from the accumulated sale history
(data/sales_cache/, maintained by grade10_analytics.load_sales).

Usage:
    python grade10_kline.py             # all cards, PSA 10 series
    python grade10_kline.py --grade "PSA 9"

Output:
    data/analytics/klines/{source}_{id}_{grade_slug}.csv   one row per day
    data/analytics/klines/index.json                       card → file + meta

Row format (TradingView / any charting lib compatible):
    date, open, high, low, close, tx, volumeUsd, carried
    carried=1 means no sale that day — close was carried forward
    (open=high=low=close) so every day has a line, as intended.
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from grade10_analytics import DATA_DIR, NOW, load_sales, normalize_grade

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("kline")

KLINE_DIR = DATA_DIR / "analytics" / "klines"


def build_klines(sales: list[dict], grade: str) -> list[dict]:
    """Aggregate same-grade sales into daily OHLCV, carrying the close
    forward across days with no transactions."""
    series = [s for s in sales if normalize_grade(s["grade"]) == grade]
    if not series:
        return []

    by_day: dict[str, list[float]] = defaultdict(list)
    day_volume: dict[str, float] = defaultdict(float)
    day_tx: dict[str, int] = defaultdict(int)
    # Keep real prices but sort each day's prices — within one scrape day
    # the raw order is meaningless (see grade10_analytics note on
    # relative-window dates), and a random first/last element would
    # otherwise become open/close. Sorting makes OHLC deterministic.
    for s in series:
        by_day[s["day"]].append(s["price"])
    for prices in by_day.values():
        prices.sort()
    # volume counts every sale that day across all grades/platforms
    for s in sales:
        day_volume[s["day"]] += s["price"]
        day_tx[s["day"]] += 1

    first = min(by_day)
    last = max(by_day)
    start = datetime.strptime(first, "%Y-%m-%d").date()
    end = datetime.strptime(last, "%Y-%m-%d").date()

    rows = []
    prev_close = None
    day = start
    while day <= end:
        key = day.strftime("%Y-%m-%d")
        prices = by_day.get(key)
        if prices:
            o, h, l, c = prices[0], max(prices), min(prices), prices[-1]
            carried = 0
        elif prev_close is None:
            day += timedelta(days=1)
            continue  # before first sale — series starts at first real day
        else:
            o = h = l = c = prev_close
            carried = 1
        rows.append({
            "date": key,
            "open": round(o, 2), "high": round(h, 2),
            "low": round(l, 2), "close": round(c, 2),
            "tx": day_tx.get(key, 0),
            "volumeUsd": round(day_volume.get(key, 0), 2),
            "carried": carried,
        })
        if prices:
            prev_close = c
        day += timedelta(days=1)
    return rows


def main():
    parser = argparse.ArgumentParser(description="Grade10 daily K-line generator")
    parser.add_argument("--grade", default="PSA 10", help="Grade series to chart")
    args = parser.parse_args()
    grade = normalize_grade(args.grade)

    cards_dir = DATA_DIR / "cards"
    if not cards_dir.exists():
        log.error("No cards directory — run grade10_scraper.py first")
        return

    KLINE_DIR.mkdir(parents=True, exist_ok=True)
    grade_slug = grade.replace(" ", "_").replace("+", "plus")
    index = []
    written = 0

    for source_dir in sorted(cards_dir.iterdir()):
        if not source_dir.is_dir():
            continue
        for card_dir in sorted(source_dir.iterdir()):
            if not card_dir.is_dir():
                continue
            sales = load_sales(card_dir)
            rows = build_klines(sales, grade)
            if not rows:
                continue
            fname = f"{source_dir.name}_{card_dir.name}_{grade_slug}.csv"
            with open(KLINE_DIR / fname, "w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
                w.writeheader()
                w.writerows(rows)
            index.append({
                "source": source_dir.name,
                "id": card_dir.name,
                "grade": grade,
                "file": fname,
                "days": len(rows),
                "from": rows[0]["date"],
                "to": rows[-1]["date"],
                "lastClose": rows[-1]["close"],
                "realDays": sum(1 for r in rows if not r["carried"]),
            })
            written += 1

    (KLINE_DIR / "index.json").write_text(
        json.dumps({"generatedAt": NOW.isoformat(), "grade": grade, "cards": index},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    log.info("K-lines → %s (%d cards, grade=%s)", KLINE_DIR, written, grade)


if __name__ == "__main__":
    main()
