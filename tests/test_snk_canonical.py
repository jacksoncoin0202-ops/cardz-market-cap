from __future__ import annotations

import ast
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from pymysql.cursors import RE_INSERT_VALUES


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

from market_source_sync import build_source_observations, load_snk_run  # noqa: E402
from snk_market_data import (  # noqa: E402
    aggregate_daily_trades,
    normalized_trade_unit_prices,
    one_card_variant_id,
    pull_market_data,
)


class SnkCanonicalTests(unittest.TestCase):
    def test_price_upsert_uses_pymysql_true_batch_shape(self) -> None:
        tree = ast.parse(
            (ROOT / "pipelines" / "snk_market_data.py").read_text(encoding="utf-8")
        )
        query = None
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Assign)
                and any(
                    isinstance(target, ast.Name) and target.id == "price_upsert"
                    for target in node.targets
                )
            ):
                query = ast.literal_eval(node.value)
                break
        self.assertIsNotNone(query)
        self.assertIsNotNone(RE_INSERT_VALUES.match(query))

    def test_sales_upsert_uses_pymysql_true_batch_shape(self) -> None:
        tree = ast.parse(
            (ROOT / "pipelines" / "ingest_snk_trades_sales.py").read_text(
                encoding="utf-8"
            )
        )
        query = None
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Assign)
                and any(
                    isinstance(target, ast.Name) and target.id == "sales_upsert"
                    for target in node.targets
                )
            ):
                query = ast.literal_eval(node.value)
                break
        self.assertIsNotNone(query)
        self.assertIsNotNone(RE_INSERT_VALUES.match(query))

    def test_collection_keeps_complete_source_payload_for_local_reuse(self) -> None:
        master = {
            "productCatalogId": 77,
            "productNumber": "OP01-001",
            "name": "Test Card",
            "providerExtra": {"futureField": True},
        }
        condition_history = {
            "filters": {
                "variants": {
                    "options": [
                        {"id": 88, "name": "1枚"},
                        {"id": 89, "name": "2枚"},
                    ]
                }
            },
            "trades": [],
            "providerMeta": {"cursor": "kept"},
        }
        single_history = {
            "chart": {"lines": [{"points": []}]},
            "providerMeta": {"range": "all"},
        }

        class FakeApi:
            def get_master(self, item_id):
                self.assert_item = item_id
                return master

            def get_trading_history(
                self,
                product_catalog_id,
                range_="all",
                condition_code=None,
                variant_id=None,
            ):
                return condition_history if variant_id is None else single_history

        row = pull_market_data(
            FakeApi(),
            123,
            condition_code="trading_card_single_psa10",
        )

        self.assertEqual(
            row["source_payload"],
            {
                "master": master,
                "condition_history": condition_history,
                "single_card_history": single_history,
            },
        )

    def test_trade_aggregate_uses_exact_sale_identity_and_never_uses_ask(self) -> None:
        daily = aggregate_daily_trades(
            [
                {"transactionId": "sale-1", "soldAt": "2026-07-21T09:00:00Z", "price": 12_000, "label": "1枚"},
                {"transactionId": "sale-1", "soldAt": "2026-07-21T09:00:00Z", "price": 12_000, "label": "1枚"},
                {"transactionId": "sale-2", "soldAt": "2026-07-21T10:00:00Z", "price": 13_000, "label": "1枚"},
                {"transactionId": "bundle", "soldAt": "2026-07-21T11:00:00Z", "price": 36_000, "label": "3枚"},
                {"transactionId": "invalid", "soldAt": "2026-07-21", "price": 0},
            ]
        )

        self.assertEqual(daily, {"2026-07-21": {"count": 5, "value_jpy": 61_000}})

    def test_snk_bundle_prices_are_normalized_per_card_for_any_quantity(self) -> None:
        prices = normalized_trade_unit_prices(
            [
                {"soldAt": "2026-07-21T09:00:00Z", "price": 36_000, "label": "3枚"},
                {"soldAt": "2026-07-21T10:00:00Z", "price": 120_000, "label": "12枚"},
            ]
        )
        self.assertEqual(prices, {"2026-07-21": 11_000.0})

    def test_snk_requires_the_explicit_one_card_variant(self) -> None:
        history = {
            "filters": {
                "variants": {
                    "options": [
                        {"id": 1872071, "name": "1枚"},
                        {"id": 1872072, "name": "2枚"},
                    ]
                }
            }
        }
        self.assertEqual(one_card_variant_id(history), 1872071)

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
