from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

from market_metrics import (  # noqa: E402
    DailyReferenceClose,
    ExactSale,
    derive_change_windows,
    detail_trend,
    aggregate_tracked_sales,
)


class MarketMetricTests(unittest.TestCase):
    def test_daily_windows_use_closest_same_method_anchor_and_never_self_anchor(self) -> None:
        closes = [
            DailyReferenceClose(date(2026, 6, 22), 100, "snk", "psa10_history"),
            DailyReferenceClose(date(2026, 7, 15), 110, "snk", "psa10_history"),
            DailyReferenceClose(date(2026, 7, 21), 120, "snk", "psa10_history"),
            DailyReferenceClose(date(2026, 7, 22), 132, "snk", "psa10_history"),
        ]

        result = derive_change_windows(closes, date(2026, 7, 22))

        self.assertEqual(result["1d"]["status"], "ready")
        self.assertEqual(result["1d"]["valuePct"], 10.0)
        self.assertEqual(result["7d"]["valuePct"], 20.0)
        self.assertEqual(result["30d"]["valuePct"], 32.0)
        self.assertEqual(result["30d"]["anchorAt"], "2026-06-22")
        single_day = derive_change_windows([closes[-1]], date(2026, 7, 22))["1d"]
        self.assertEqual(single_day["status"], "accumulating")
        self.assertIsNone(single_day["valuePct"])
        self.assertEqual(single_day["asOf"], "2026-07-22")
        self.assertIsNone(single_day["anchorAt"])

    def test_source_or_method_transition_resets_windows_to_accumulating(self) -> None:
        closes = [
            DailyReferenceClose(date(2026, 6, 22), 100, "snk", "psa10_history"),
            DailyReferenceClose(date(2026, 7, 21), 110, "snk", "psa10_history"),
            DailyReferenceClose(date(2026, 7, 22), 120, "ebay", "exact_sold_median"),
        ]

        result = derive_change_windows(closes, date(2026, 7, 22))

        self.assertTrue(all(metric["status"] == "accumulating" for metric in result.values()))
        self.assertTrue(all(metric["valuePct"] is None for metric in result.values()))
        self.assertEqual(result["1d"]["source"], "ebay")
        self.assertEqual(result["1d"]["method"], "exact_sold_median")

    def test_unavailable_current_and_missing_old_anchor_do_not_become_zero(self) -> None:
        self.assertTrue(
            all(metric["status"] == "unavailable" for metric in derive_change_windows([], date(2026, 7, 22)).values())
        )
        closes = [
            DailyReferenceClose(date(2026, 6, 1), 100, "snk", "psa10_history"),
            DailyReferenceClose(date(2026, 7, 22), 110, "snk", "psa10_history"),
        ]
        seven_days = derive_change_windows(closes, date(2026, 7, 22))["7d"]
        self.assertEqual(seven_days["status"], "unavailable")
        self.assertIsNone(seven_days["valuePct"])

    def test_tracked_sales_uses_exact_dates_and_never_returns_fake_zero(self) -> None:
        sales = [
            ExactSale(date(2026, 7, 22), 100, 1, "date"),
            ExactSale(date(2026, 7, 16), 200, 2, "date"),
            ExactSale(date(2026, 7, 1), 999, 1, "relative_date_bucket"),
        ]

        seven_days = aggregate_tracked_sales(sales, date(2026, 7, 22), "7d", coverage="partial")
        self.assertEqual(seven_days["salesCount"], 2)
        self.assertEqual(seven_days["salesValueUsd"], 300.0)
        self.assertEqual(seven_days["coverage"], "partial")
        no_sales = aggregate_tracked_sales([], date(2026, 7, 22), "1d", coverage="partial")
        self.assertEqual(no_sales["coverage"], "partial")
        self.assertIsNone(no_sales["salesCount"])
        self.assertIsNone(no_sales["salesValueUsd"])
        stale = aggregate_tracked_sales(sales, date(2026, 7, 22), "30d", coverage="stale")
        self.assertEqual(stale["coverage"], "stale")

    def test_detail_trend_is_daily_line_and_sales_bars_without_ohlc(self) -> None:
        closes = [
            DailyReferenceClose(date(2026, 7, 20), 100, "snk", "psa10_history"),
            DailyReferenceClose(date(2026, 7, 21), 110, "snk", "psa10_history"),
            DailyReferenceClose(date(2026, 7, 22), 120, "snk", "psa10_history"),
        ]
        sales = [ExactSale(date(2026, 7, 21), 115, 1, "date")]

        trend = detail_trend(closes, sales, date(2026, 7, 22), days=7)

        self.assertEqual(trend["chartType"], "daily_line_and_sales_bars")
        self.assertEqual([point["priceUsd"] for point in trend["priceLine"]], [100.0, 110.0, 120.0])
        self.assertEqual(trend["salesBars"], [{"date": "2026-07-21", "salesCount": 1, "salesValueUsd": 115.0}])
        self.assertFalse({"open", "high", "low", "close"} & set(trend))


if __name__ == "__main__":
    unittest.main()
