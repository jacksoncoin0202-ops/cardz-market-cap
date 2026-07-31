from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import unittest
from collections import Counter
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "pipelines" / "g10_ebay_ingest.py"
SPEC = importlib.util.spec_from_file_location("cardz_g10_ebay_ingest", MODULE_PATH)
assert SPEC and SPEC.loader
ingest = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = ingest
SPEC.loader.exec_module(ingest)


def build_rows(document: object, *, base_date: date, stats: Counter | None = None) -> list:
    return ingest.sale_rows_from_document(
        document,
        variant_id=7,
        external_entity_id="card-uuid",
        grade_key="PSA_10",
        grader_code="psa",
        grade_label="10",
        fetched_at=datetime(2026, 7, 25, 21, 37, 0),
        base_date=base_date,
        payload_sha256="0" * 64,
        stats=stats if stats is not None else Counter(),
    )


class DateResolutionTests(unittest.TestCase):
    def test_iso_date_resolves_to_midnight_and_is_marked_exact(self) -> None:
        sold_at, quality = ingest.resolve_sale_date("2026-04-25", date(2026, 7, 25))
        self.assertEqual(sold_at, datetime(2026, 4, 25, 0, 0, 0))
        self.assertEqual(quality, "exact_date")

    def test_relative_dates_subtract_from_the_supplied_base_day(self) -> None:
        base = date(2026, 7, 25)
        for text, expected in (
            ("0 day ago", date(2026, 7, 25)),
            ("1 day ago", date(2026, 7, 24)),
            ("2 days ago", date(2026, 7, 23)),
            ("3 days ago", date(2026, 7, 22)),
        ):
            with self.subTest(text=text):
                sold_at, quality = ingest.resolve_sale_date(text, base)
                self.assertEqual(sold_at, datetime.combine(expected, datetime.min.time()))
                self.assertEqual(quality, "relative_resolved")

    def test_unparseable_text_yields_null_sold_at_but_keeps_the_original_string(self) -> None:
        for text in ("", "last week", "2026/04/25", "yesterday", "2026-13-45"):
            with self.subTest(text=text):
                sold_at, quality = ingest.resolve_sale_date(text, date(2026, 7, 25))
                self.assertIsNone(sold_at)
                self.assertEqual(quality, "unparsed")

        rows = build_rows(
            {"saleHistory": [{"date": "last week", "price": 100, "currency": "usd"}]},
            base_date=date(2026, 7, 25),
        )
        self.assertEqual(len(rows), 1)
        self.assertIsNone(rows[0].sold_at)
        self.assertEqual(rows[0].source_date_text, "last week")
        self.assertEqual(rows[0].timestamp_quality, "unparsed")
        self.assertEqual(rows[0].coverage_status, "quarantined")
        self.assertFalse(rows[0].accepted)


class MtimeBaseTests(unittest.TestCase):
    def test_relative_base_comes_from_file_mtime_not_from_today(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "ebay_PSA_10.json"
            path.write_text("{}", encoding="utf-8")
            stamp = datetime(2026, 6, 10, 4, 0, 0, tzinfo=timezone.utc).timestamp()
            os.utime(path, (stamp, stamp))

            fetched_at = ingest.mtime_fetched_at(path)
            self.assertEqual(fetched_at, datetime(2026, 6, 10, 4, 0, 0))
            base = ingest.relative_base_date(fetched_at)
            self.assertEqual(base, date(2026, 6, 10))
            self.assertNotEqual(base, datetime.now(timezone.utc).date())

            sold_at, quality = ingest.resolve_sale_date("3 days ago", base)
            self.assertEqual(sold_at, datetime(2026, 6, 7, 0, 0, 0))
            self.assertEqual(quality, "relative_resolved")

    def test_base_day_is_the_utc_calendar_day_of_the_mtime(self) -> None:
        # 2026-07-25T21:37Z is 2026-07-26 06:37 in JST. The base must stay on the
        # UTC day, otherwise every evening scrape shifts a whole batch forward a day.
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "ebay_PSA_10.json"
            path.write_text("{}", encoding="utf-8")
            stamp = datetime(2026, 7, 25, 21, 37, 0, tzinfo=timezone.utc).timestamp()
            os.utime(path, (stamp, stamp))
            self.assertEqual(ingest.relative_base_date(ingest.mtime_fetched_at(path)), date(2026, 7, 25))


class FingerprintTests(unittest.TestCase):
    def test_same_input_hashes_to_the_same_fingerprint_every_time(self) -> None:
        args = ("card-uuid", "psa", "10", "2 days ago", Decimal("230.5"))
        first = ingest.transaction_fingerprint(*args)
        second = ingest.transaction_fingerprint(*args)
        self.assertEqual(first, second)
        self.assertEqual(len(first), 64)

    def test_price_representation_does_not_change_the_fingerprint(self) -> None:
        base = ingest.transaction_fingerprint("card-uuid", "psa", "10", "2026-04-25", Decimal("230.5"))
        for equivalent in (Decimal("230.50"), Decimal("230.500000"), Decimal(str(230.5))):
            with self.subTest(price=equivalent):
                self.assertEqual(
                    ingest.transaction_fingerprint("card-uuid", "psa", "10", "2026-04-25", equivalent),
                    base,
                )

    def test_fingerprint_uses_raw_date_text_so_reruns_stay_idempotent(self) -> None:
        # The same record read on two different days resolves to different sold_at
        # values, but must keep one row.
        rows_today = build_rows(
            {"saleHistory": [{"date": "2 days ago", "price": 230.5, "currency": "usd"}]},
            base_date=date(2026, 7, 25),
        )
        rows_later = build_rows(
            {"saleHistory": [{"date": "2 days ago", "price": 230.5, "currency": "usd"}]},
            base_date=date(2026, 7, 28),
        )
        self.assertNotEqual(rows_today[0].sold_at, rows_later[0].sold_at)
        self.assertEqual(rows_today[0].fingerprint, rows_later[0].fingerprint)

    def test_any_component_change_produces_a_different_fingerprint(self) -> None:
        base = ingest.transaction_fingerprint("card-uuid", "psa", "10", "2026-04-25", Decimal("230.5"))
        variants = (
            ("other-uuid", "psa", "10", "2026-04-25", Decimal("230.5")),
            ("card-uuid", "bgs", "10", "2026-04-25", Decimal("230.5")),
            ("card-uuid", "psa", "9", "2026-04-25", Decimal("230.5")),
            ("card-uuid", "psa", "10", "2026-04-26", Decimal("230.5")),
            ("card-uuid", "psa", "10", "2026-04-25", Decimal("230.6")),
        )
        for args in variants:
            with self.subTest(args=args):
                self.assertNotEqual(ingest.transaction_fingerprint(*args), base)


class MedianTests(unittest.TestCase):
    def test_median_of_odd_count_is_the_middle_value(self) -> None:
        self.assertEqual(
            ingest.median_usd([Decimal("100"), Decimal("200"), Decimal("300")]),
            Decimal("200.000000"),
        )

    def test_median_of_even_count_averages_the_two_middle_values(self) -> None:
        self.assertEqual(
            ingest.median_usd([Decimal("100"), Decimal("200"), Decimal("300"), Decimal("500")]),
            Decimal("250.000000"),
        )

    def test_median_ignores_a_single_outlier_that_would_wreck_the_mean(self) -> None:
        values = [Decimal("200"), Decimal("210"), Decimal("205"), Decimal("215"), Decimal("99999")]
        self.assertEqual(ingest.median_usd(values), Decimal("210.000000"))
        mean = sum(values) / len(values)
        self.assertLess(ingest.median_usd(values), mean / 10)

    def test_single_sale_median_is_that_sale(self) -> None:
        self.assertEqual(ingest.median_usd([Decimal("214.558")]), Decimal("214.558000"))


class DocumentParsingTests(unittest.TestCase):
    def test_missing_or_empty_sale_history_produces_no_rows(self) -> None:
        stats: Counter = Counter()
        self.assertEqual(build_rows({"averagePrice": None}, base_date=date(2026, 7, 25), stats=stats), [])
        self.assertEqual(stats["file_no_history"], 1)
        self.assertEqual(build_rows({"saleHistory": []}, base_date=date(2026, 7, 25)), [])

    def test_bad_prices_and_non_usd_rows_are_rejected_not_guessed(self) -> None:
        stats: Counter = Counter()
        rows = build_rows(
            {
                "saleHistory": [
                    {"date": "2026-04-25", "price": 0, "currency": "usd"},
                    {"date": "2026-04-25", "price": -5, "currency": "usd"},
                    {"date": "2026-04-25", "price": None, "currency": "usd"},
                    {"date": "2026-04-25", "price": 1000, "currency": "jpy"},
                    {"date": "2026-04-25", "price": 230.5, "currency": "usd"},
                ]
            },
            base_date=date(2026, 7, 25),
            stats=stats,
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(stats["row_rejected_price"], 3)
        self.assertEqual(stats["row_rejected_currency"], 1)

    def test_identical_sales_collapse_on_the_unique_fingerprint_and_get_counted(self) -> None:
        stats: Counter = Counter()
        rows = build_rows(
            {
                "saleHistory": [
                    {"date": "2026-04-25", "price": 230.5, "currency": "usd"},
                    {"date": "2026-04-25", "price": 230.5, "currency": "usd"},
                ]
            },
            base_date=date(2026, 7, 25),
            stats=stats,
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(stats["row_fingerprint_collision"], 1)


class AggregateTests(unittest.TestCase):
    def _rows(self) -> list:
        return build_rows(
            {
                "saleHistory": [
                    {"date": "2026-04-25", "price": 100, "currency": "usd"},
                    {"date": "2026-04-25", "price": 300, "currency": "usd"},
                    {"date": "2026-04-25", "price": 99999, "currency": "usd"},
                    {"date": "2026-04-26", "price": 250, "currency": "usd"},
                    {"date": "who knows", "price": 777, "currency": "usd"},
                ]
            },
            base_date=date(2026, 7, 25),
        )

    def test_daily_sales_rollup_counts_and_sums_accepted_rows_only(self) -> None:
        sales = ingest.daily_sales_rows(self._rows())
        self.assertEqual([row["observed_date"] for row in sales], [date(2026, 4, 25), date(2026, 4, 26)])
        self.assertEqual(sales[0]["sales_count"], 3)
        self.assertEqual(sales[0]["sales_value_usd"], Decimal("100399.000000"))
        self.assertEqual(sales[1]["sales_count"], 1)
        self.assertEqual(len(sales[0]["payload_sha256"]), 64)

    def test_daily_price_uses_median_and_skips_quarantined_rows(self) -> None:
        prices = ingest.daily_price_rows(self._rows())
        self.assertEqual([row["observed_date"] for row in prices], [date(2026, 4, 25), date(2026, 4, 26)])
        self.assertEqual(prices[0]["price_usd"], Decimal("300.000000"))
        self.assertEqual(prices[1]["price_usd"], Decimal("250.000000"))

    def test_daily_price_only_covers_psa_10(self) -> None:
        psa9 = ingest.sale_rows_from_document(
            {"saleHistory": [{"date": "2026-04-25", "price": 50, "currency": "usd"}]},
            variant_id=7,
            external_entity_id="card-uuid",
            grade_key="PSA_9",
            grader_code="psa",
            grade_label="9",
            fetched_at=datetime(2026, 7, 25, 21, 37, 0),
            base_date=date(2026, 7, 25),
            payload_sha256="0" * 64,
            stats=Counter(),
        )
        self.assertEqual(ingest.daily_price_rows(psa9), [])
        self.assertEqual(len(ingest.daily_sales_rows(psa9)), 1)

    def test_aggregates_are_stable_across_reruns_of_the_same_input(self) -> None:
        first = ingest.daily_sales_rows(self._rows())
        second = ingest.daily_sales_rows(self._rows())
        self.assertEqual(
            [row["payload_sha256"] for row in first],
            [row["payload_sha256"] for row in second],
        )


class RealFixtureShapeTests(unittest.TestCase):
    def test_parses_the_documented_g10_payload_shape(self) -> None:
        document = json.loads(
            '{"averagePrice": {"price": 214.558, "currency": "usd"},'
            ' "saleHistory": [{"date": "2026-06-14", "price": 230.5, "grade": "PSA 10", "currency": "usd"},'
            ' {"date": "1 day ago", "price": 199.0, "grade": "PSA 10", "currency": "usd"}]}'
        )
        rows = build_rows(document, base_date=date(2026, 7, 25))
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0].sold_at, datetime(2026, 6, 14, 0, 0, 0))
        self.assertEqual(rows[0].timestamp_quality, "exact_date")
        self.assertEqual(rows[1].sold_at, datetime(2026, 7, 24, 0, 0, 0))
        self.assertEqual(rows[1].timestamp_quality, "relative_resolved")
        self.assertTrue(all(row.coverage_status == "partial" for row in rows))
        self.assertTrue(all(row.grader_code == "psa" and row.grade_label == "10" for row in rows))


class IdentityScopeTests(unittest.TestCase):
    def test_identity_map_resolves_aliases_and_filters_to_frozen_cohort(self) -> None:
        class Cursor:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            def execute(self, statement, _params=None):
                self.statement = statement

            def fetchall(self):
                return [
                    {
                        "variant_id": 7,
                        "source_code": "ebay",
                        "external_entity_id": "uuid-7",
                    },
                    {
                        "variant_id": 8,
                        "source_code": "snkrdunk",
                        "external_entity_id": "800",
                    },
                ]

        cursor = Cursor()

        class Connection:
            def cursor(self):
                return cursor

        mapping = ingest.load_identity_map(Connection(), variant_ids={7})
        self.assertEqual(mapping, {("altxyz", "uuid-7"): 7})
        self.assertIn("catalog_variant_alias", cursor.statement)


if __name__ == "__main__":
    unittest.main()
