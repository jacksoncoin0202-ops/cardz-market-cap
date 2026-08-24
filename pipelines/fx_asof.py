#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Deterministic USD/JPY lookup for the market date being converted.

The table stores JPY per USD. A conversion uses the newest observation whose
effective date is not later than the market date. Dates older than the first
observation use that oldest known point and report its date explicitly; an
empty table is an error, never an invitation to invent a rate.
"""
from __future__ import annotations

import argparse
import math
from bisect import bisect_right
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Iterable, Mapping


def _date_value(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


@dataclass(frozen=True)
class FxPoint:
    effective_date: date
    rate: float


class JpyPerUsdHistory:
    """One run's immutable, date-indexed JPY-per-USD observations."""

    def __init__(self, rows: Iterable[Mapping[str, Any]]) -> None:
        # Query order is date/id ascending; assigning by date makes the largest
        # id on a duplicate date authoritative without a second DB round trip.
        by_date: dict[date, float] = {}
        for row in rows:
            observed = _date_value(row["effective_date"])
            rate = float(row["rate"])
            if not math.isfinite(rate) or rate <= 0:
                raise RuntimeError(f"invalid USD/JPY FX rate for {observed}: {rate!r}")
            by_date[observed] = rate
        if not by_date:
            raise RuntimeError("USD/JPY FX history missing; refuse to invent conversion")
        self._points = tuple(FxPoint(day, by_date[day]) for day in sorted(by_date))
        self._dates = tuple(point.effective_date for point in self._points)

    @classmethod
    def load(cls, cursor: Any) -> "JpyPerUsdHistory":
        cursor.execute(
            """
            SELECT id, effective_date, rate
            FROM market_fx_rate_observation
            WHERE base_currency='USD' AND quote_currency='JPY'
            ORDER BY effective_date ASC, id ASC
            """
        )
        return cls(cursor.fetchall())

    @property
    def point_count(self) -> int:
        return len(self._points)

    @property
    def latest(self) -> FxPoint:
        return self._points[-1]

    def for_date(self, market_date: date | datetime | str) -> tuple[float, date]:
        target = _date_value(market_date)
        index = bisect_right(self._dates, target) - 1
        if index < 0:
            index = 0
        point = self._points[index]
        return point.rate, point.effective_date


def _self_test() -> None:
    rows = [
        {"id": 1, "effective_date": "2026-07-25", "rate": "163.67"},
        {"id": 2, "effective_date": date(2026, 8, 17), "rate": 160.92},
        {"id": 3, "effective_date": date(2026, 8, 17), "rate": 160.50},
        {"id": 4, "effective_date": datetime(2026, 8, 24, 0, 0), "rate": 159.0},
    ]
    history = JpyPerUsdHistory(rows)
    assert history.for_date("2023-06-19") == (163.67, date(2026, 7, 25))
    assert history.for_date(date(2026, 8, 16)) == (163.67, date(2026, 7, 25))
    assert history.for_date(date(2026, 8, 17)) == (160.50, date(2026, 8, 17))
    assert history.for_date(date(2026, 9, 1)) == (159.0, date(2026, 8, 24))
    try:
        JpyPerUsdHistory([])
    except RuntimeError as exc:
        assert "refuse to invent" in str(exc)
    else:
        raise AssertionError("empty FX history must fail closed")
    print("FX_ASOF_SELF_TEST_OK")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if not args.self_test:
        parser.error("only --self-test is supported; import JpyPerUsdHistory in writers")
    _self_test()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
