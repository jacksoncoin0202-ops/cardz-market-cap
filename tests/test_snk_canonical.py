from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

from market_source_sync import build_source_observations, load_snk_run  # noqa: E402
from snk_market_data import aggregate_daily_trades  # noqa: E402


class SnkCanonicalTests(unittest.TestCase):
    def test_trade_aggregate_uses_exact_sale_identity_and_never_uses_ask(self) -> None:
        daily = aggregate_daily_trades(
            [
                {"transactionId": "sale-1", "soldAt": "2026-07-21T09:00:00Z", "price": 12_000},
                {"transactionId": "sale-1", "soldAt": "2026-07-21T09:00:00Z", "price": 12_000},
                {"transactionId": "sale-2", "soldAt": "2026-07-21T10:00:00Z", "price": 13_000},
                {"transactionId": "invalid", "soldAt": "2026-07-21", "price": 0},
            ]
        )

        self.assertEqual(daily, {"2026-07-21": {"count": 2, "value_jpy": 25_000}})

    def test_snk_price_and_sales_keep_native_currency_fx_and_fetch_provenance(self) -> None:
        crosswalk = {
            "cards": [
                {
                    "canonicalSourceCode": "snkrdunk",
                    "canonicalExternalId": "101",
                    "snkItemId": 101,
                    "tcg": "pokemon",
                    "collectorNumber": "085/SV-P",
                    "language": "ja",
                    "parallel": "holo",
                    "identityStatus": "confirmed",
                }
            ]
        }
        snk_rows = {
            101: {
                "item_id": 101,
                "condition_filter": "trading_card_single_psa10",
                "fetched_at": "2026-07-23T01:02:03Z",
                "used_min_price": 99_999,
                "identity": {
                    "collectorNumber": "085/SV-P",
                    "language": "ja",
                    "parallel": "holo",
                    "matchStatus": "exact",
                },
                "kline": [
                    {"date": "2026-07-21", "price_jpy": 16_000},
                    {"date": "2026-07-22", "price_jpy": 20_000},
                ],
                "daily_activity": {"2026-07-22": {"count": 2, "value_jpy": 34_000}},
            }
        }

        observations, counts = build_source_observations(
            crosswalk,
            ROOT / "data" / "private" / "missing-gemrate",
            snk_rows,
            {},
            {},
            160,
            datetime(2026, 7, 23, tzinfo=timezone.utc),
            None,
        )

        prices = [row for row in observations if row["observationKind"] == "index_constituent"]
        latest = prices[-1]["payload"]
        sales = next(row["payload"] for row in observations if row["observationKind"] == "tracked_sales_daily")
        self.assertEqual(counts["snkPriceObservations"], 2)
        self.assertEqual(latest["priceJpy"], 20_000)
        self.assertEqual(latest["priceUsd"], 125.0)
        self.assertEqual(latest["nativeCurrency"], "JPY")
        self.assertEqual(latest["fetchedAt"], "2026-07-23T01:02:03Z")
        self.assertNotIn("usedMinPrice", latest)
        self.assertEqual(sales["salesValueJpy"], 34_000.0)
        self.assertEqual(sales["nativeCurrency"], "JPY")
        self.assertEqual(sales["fetchedAt"], "2026-07-23T01:02:03Z")

    def test_snk_identity_mismatch_and_partial_run_fail_closed(self) -> None:
        crosswalk = {
            "cards": [
                {
                    "canonicalSourceCode": "snkrdunk",
                    "canonicalExternalId": "101",
                    "snkItemId": 101,
                    "tcg": "pokemon",
                    "collectorNumber": "085/SV-P",
                    "language": "ja",
                    "parallel": "holo",
                    "identityStatus": "confirmed",
                }
            ]
        }
        mismatched = {
            101: {
                "item_id": 101,
                "condition_filter": "trading_card_single_psa10",
                "fetched_at": "2026-07-23T01:02:03Z",
                "identity": {
                    "collectorNumber": "086/SV-P",
                    "language": "ja",
                    "parallel": "holo",
                    "matchStatus": "exact",
                },
                "kline": [{"date": "2026-07-22", "price_jpy": 20_000}],
            }
        }
        with self.assertRaisesRegex(RuntimeError, "SNK identity mismatch"):
            build_source_observations(
                crosswalk,
                ROOT / "data" / "private" / "missing-gemrate",
                mismatched,
                {},
                {},
                160,
                datetime(2026, 7, 23, tzinfo=timezone.utc),
                None,
            )

        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "partial.jsonl"
            path.write_text(
                "\n".join(
                    [
                        '{"item_id":101,"condition_filter":"trading_card_single_psa10","fetched_at":"2026-07-23T01:02:03Z"}',
                        '{"item_id":101,"condition_filter":"trading_card_single_psa10","fetched_at":"2026-07-23T01:02:03Z"}',
                    ]
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(RuntimeError, "duplicate SNK item"):
                load_snk_run(path)


if __name__ == "__main__":
    unittest.main()
