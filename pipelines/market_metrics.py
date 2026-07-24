"""Derive CARDZ-owned daily market metrics from canonical observations.

This module deliberately has no collector, database, or provider dependency.
It consumes already-validated daily reference closes and exact-date sales only.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Iterable, Mapping


WINDOWS: Mapping[str, tuple[int, int]] = {
    "1d": (1, 1),
    "7d": (7, 2),
    "30d": (30, 3),
}
EXACT_TIMESTAMP_QUALITIES = {"date", "timestamp"}
SALE_COVERAGE = {"partial", "stale", "unavailable"}


@dataclass(frozen=True)
class DailyReferenceClose:
    observed_date: date
    price_usd: float
    source: str
    method: str


@dataclass(frozen=True)
class ExactSale:
    sold_date: date
    transaction_value_usd: float
    quantity: int
    timestamp_quality: str


def _valid_closes(closes: Iterable[DailyReferenceClose], as_of: date) -> list[DailyReferenceClose]:
    grouped: dict[date, DailyReferenceClose] = {}
    for close in closes:
        if close.observed_date > as_of or close.price_usd <= 0 or not close.source or not close.method:
            continue
        existing = grouped.get(close.observed_date)
        if existing is not None and existing != close:
            raise ValueError(f"ambiguous canonical close for {close.observed_date.isoformat()}")
        grouped[close.observed_date] = close
    return [grouped[key] for key in sorted(grouped)]


def _unavailable() -> dict[str, object]:
    return {"valuePct": None, "status": "unavailable", "asOf": None, "anchorAt": None}


def derive_change_windows(closes: Iterable[DailyReferenceClose], as_of: date) -> dict[str, dict[str, object]]:
    """Calculate close-to-close changes without crossing source/method boundaries."""

    normalized = _valid_closes(closes, as_of)
    if not normalized:
        return {window: _unavailable() for window in WINDOWS}
    current = normalized[-1]
    same_origin = [
        close
        for close in normalized
        if (close.source, close.method) == (current.source, current.method) and close.observed_date <= current.observed_date
    ]
    result: dict[str, dict[str, object]] = {}
    for window, (days, tolerance_days) in WINDOWS.items():
        target = current.observed_date - timedelta(days=days)
        anchors = [
            close
            for close in same_origin
            if close.observed_date < current.observed_date
            and abs((close.observed_date - target).days) <= tolerance_days
        ]
        if anchors:
            anchor = min(
                anchors,
                key=lambda close: (abs((close.observed_date - target).days), close.observed_date > target, close.observed_date),
            )
            result[window] = {
                "valuePct": round(((current.price_usd / anchor.price_usd) - 1) * 100, 6),
                "status": "ready",
                "asOf": current.observed_date.isoformat(),
                "anchorAt": anchor.observed_date.isoformat(),
                "source": current.source,
                "method": current.method,
            }
            continue
        oldest = same_origin[0].observed_date
        # A fresh source/method has no eligible anchor yet.  It must start a
        # new accumulation period even when an older, incompatible source has
        # data for the target date.  Once this origin has multiple points but
        # still misses a target it should be reported as unavailable instead.
        status = "accumulating" if len(same_origin) == 1 or oldest > target + timedelta(days=tolerance_days) else "unavailable"
        result[window] = {
            "valuePct": None,
            "status": status,
            "asOf": current.observed_date.isoformat(),
            "anchorAt": None,
            "source": current.source,
            "method": current.method,
        }
    return result


def _valid_exact_sales(sales: Iterable[ExactSale], as_of: date) -> list[ExactSale]:
    return [
        sale
        for sale in sales
        if sale.sold_date <= as_of
        and sale.transaction_value_usd > 0
        and sale.quantity > 0
        and sale.timestamp_quality in EXACT_TIMESTAMP_QUALITIES
    ]


def aggregate_tracked_sales(
    sales: Iterable[ExactSale],
    as_of: date,
    window: str,
    *,
    coverage: str,
) -> dict[str, object]:
    """Aggregate only exact sales and preserve honest partial coverage."""

    if window not in WINDOWS:
        raise ValueError(f"unsupported window: {window}")
    if coverage not in SALE_COVERAGE:
        raise ValueError(f"unsupported coverage: {coverage}")
    if coverage == "unavailable":
        return {"salesCount": None, "salesValueUsd": None, "coverage": "unavailable", "asOf": as_of.isoformat()}
    days, _ = WINDOWS[window]
    start = as_of - timedelta(days=days)
    selected = [sale for sale in _valid_exact_sales(sales, as_of) if start <= sale.sold_date <= as_of]
    if not selected:
        return {"salesCount": None, "salesValueUsd": None, "coverage": coverage, "asOf": as_of.isoformat()}
    return {
        "salesCount": len(selected),
        "salesValueUsd": round(sum(sale.transaction_value_usd for sale in selected), 6),
        "coverage": coverage,
        "asOf": as_of.isoformat(),
    }


def detail_trend(
    closes: Iterable[DailyReferenceClose],
    sales: Iterable[ExactSale],
    as_of: date,
    *,
    days: int,
) -> dict[str, object]:
    """Return a chart-safe daily line plus exact-sale bars, never synthetic OHLC."""

    normalized = _valid_closes(closes, as_of)
    if not normalized:
        return {"chartType": "daily_line_and_sales_bars", "priceLine": [], "salesBars": []}
    current = normalized[-1]
    start = as_of - timedelta(days=days)
    origin = (current.source, current.method)
    price_line = [
        {"date": close.observed_date.isoformat(), "priceUsd": float(close.price_usd)}
        for close in normalized
        if close.observed_date >= start and (close.source, close.method) == origin
    ]
    daily_sales: dict[date, list[ExactSale]] = {}
    for sale in _valid_exact_sales(sales, as_of):
        if sale.sold_date >= start:
            daily_sales.setdefault(sale.sold_date, []).append(sale)
    sales_bars = [
        {
            "date": sold_date.isoformat(),
            "salesCount": len(rows),
            "salesValueUsd": round(sum(row.transaction_value_usd for row in rows), 6),
        }
        for sold_date, rows in sorted(daily_sales.items())
    ]
    return {
        "chartType": "daily_line_and_sales_bars",
        "priceLine": price_line,
        "salesBars": sales_bars,
    }
