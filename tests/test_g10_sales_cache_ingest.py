from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from collections import Counter
from datetime import datetime
from decimal import Decimal
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "pipelines" / "g10_sales_cache_ingest.py"
SPEC = importlib.util.spec_from_file_location("cardz_g10_sales_cache_ingest", MODULE_PATH)
assert SPEC and SPEC.loader
ingest = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = ingest
SPEC.loader.exec_module(ingest)


FETCHED_AT = datetime(2026, 7, 25, 21, 46, 53, 233995)


def sale(dt: str, day: str, price: float, grade: str = "PSA 10", platform: str = "snkrdunk") -> dict:
    return {"dt": dt, "day": day, "price": price, "grade": grade, "platform": platform}


def build_rows(
    document: object,
    *,
    platforms: tuple[str, ...] = ("snkrdunk",),
    variant_id: int = 7,
    external_entity_id: str = "100081",
    stats: Counter | None = None,
    unknown: Counter | None = None,
) -> list:
    return ingest.sale_rows_from_document(
        document,
        variant_id=variant_id,
        external_entity_id=external_entity_id,
        platforms=platforms,
        fetched_at=FETCHED_AT,
        payload_sha256="0" * 64,
        stats=stats if stats is not None else Counter(),
        unknown_grades=unknown if unknown is not None else Counter(),
    )


class DateSemanticsTests(unittest.TestCase):
    """`day` 係發現日曆，`dt` 先係成交日曆。呢組測試守住個判斷。"""

    def test_midnight_dt_is_the_platform_iso_date_and_marked_exact(self) -> None:
        sold_at, quality = ingest.resolve_sale_date("2025-11-11T00:00:00+00:00")
        self.assertEqual(sold_at, datetime(2025, 11, 11, 0, 0, 0))
        self.assertEqual(quality, "exact_date")

    def test_dt_with_a_clock_time_came_from_a_relative_label_and_floors_to_midnight(self) -> None:
        sold_at, quality = ingest.resolve_sale_date("2026-07-22T21:46:53.233995+00:00")
        self.assertEqual(sold_at, datetime(2026, 7, 22, 0, 0, 0))
        self.assertEqual(quality, "relative_resolved")

    def test_non_utc_offsets_are_converted_before_the_day_is_taken(self) -> None:
        # 2026-07-23T08:00+09:00 == 2026-07-22T23:00Z → 成交日係 07-22 唔係 07-23
        sold_at, quality = ingest.resolve_sale_date("2026-07-23T08:00:00+09:00")
        self.assertEqual(sold_at, datetime(2026, 7, 22, 0, 0, 0))
        self.assertEqual(quality, "relative_resolved")

    def test_unparseable_dt_yields_null_sold_at(self) -> None:
        for text in ("", "yesterday", "2026-13-45", "3 days ago"):
            with self.subTest(text=text):
                sold_at, quality = ingest.resolve_sale_date(text)
                self.assertIsNone(sold_at)
                self.assertEqual(quality, "unparsed")

    def test_sold_at_follows_dt_and_never_the_day_bucket(self) -> None:
        # 真實樣本：2022 年嘅成交，第一次爬到係 2026-07-22，`day` 就係嗰日。
        rows = build_rows({"sales": [sale("2022-10-05T00:00:00+00:00", "2026-07-22", 21.5)]})
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].sold_at, datetime(2022, 10, 5, 0, 0, 0))

    def test_both_calendars_survive_in_source_date_text(self) -> None:
        rows = build_rows({"sales": [sale("2022-10-05T00:00:00+00:00", "2026-07-22", 21.5)]})
        self.assertEqual(rows[0].source_date_text, "dt=2022-10-05T00:00:00+00:00|day=2026-07-22")

    def test_source_date_text_stays_within_the_column_width(self) -> None:
        rows = build_rows({"sales": [sale("2026-07-22T21:46:53.233995+00:00", "2026-07-25", 1.0)]})
        self.assertLessEqual(len(rows[0].source_date_text), 100)


class FingerprintTests(unittest.TestCase):
    def test_repeat_discoveries_of_one_sale_collapse_to_a_single_row(self) -> None:
        """同一筆成交連續三日爬到 → cache 出三行，只係 `day` 唔同。要收埋一行。"""
        document = {"sales": [
            sale("2026-07-19T00:00:00+00:00", "2026-07-22", 219.75),
            sale("2026-07-19T00:00:00+00:00", "2026-07-24", 219.75),
            sale("2026-07-19T00:00:00+00:00", "2026-07-25", 219.75),
        ]}
        rows = build_rows(document)
        self.assertEqual(len({row.fingerprint for row in rows}), 1)

    def test_fingerprint_ignores_the_day_bucket(self) -> None:
        a = ingest.transaction_fingerprint(7, "psa", "10", "snkrdunk", "2026-07-19T00:00:00+00:00", Decimal("219.75"))
        b = ingest.transaction_fingerprint(7, "psa", "10", "snkrdunk", "2026-07-19T00:00:00+00:00", Decimal("219.75"))
        self.assertEqual(a, b)

    def test_fingerprint_separates_variant_grade_platform_date_and_price(self) -> None:
        base = dict(variant_id=7, grader_code="psa", grade_label="10", platform="snkrdunk",
                    dt_text="2026-07-19T00:00:00+00:00", unit_price_usd=Decimal("219.75"))
        baseline = ingest.transaction_fingerprint(**base)
        for field, value in (
            ("variant_id", 8),
            ("grader_code", "bgs"),
            ("grade_label", "9"),
            ("platform", "ebay"),
            ("dt_text", "2026-07-20T00:00:00+00:00"),
            ("unit_price_usd", Decimal("219.76")),
        ):
            with self.subTest(field=field):
                self.assertNotEqual(baseline, ingest.transaction_fingerprint(**{**base, field: value}))

    def test_float_noise_does_not_change_the_fingerprint(self) -> None:
        a = ingest.transaction_fingerprint(7, "psa", "10", "snkrdunk", "d", Decimal("219.7498304446"))
        b = ingest.transaction_fingerprint(7, "psa", "10", "snkrdunk", "d", Decimal("219.74983044469332"))
        self.assertEqual(a, b)


class PlatformScopeTests(unittest.TestCase):
    def test_ebay_rows_are_skipped_by_default_because_run_70_71_already_has_them(self) -> None:
        stats: Counter = Counter()
        rows = build_rows({"sales": [
            sale("2026-07-19T00:00:00+00:00", "2026-07-25", 100.0, platform="snkrdunk"),
            sale("2026-07-19T00:00:00+00:00", "2026-07-25", 16100.0, platform="ebay"),
        ]}, stats=stats)
        self.assertEqual([row.platform for row in rows], ["snkrdunk"])
        self.assertEqual(stats["row_out_of_scope_platform"], 1)
        self.assertEqual(stats["row_seen"], 1)

    def test_ebay_can_be_opted_in_explicitly(self) -> None:
        rows = build_rows({"sales": [
            sale("2026-07-19T00:00:00+00:00", "2026-07-25", 16100.0, platform="ebay"),
        ]}, platforms=("snkrdunk", "ebay"))
        self.assertEqual([row.platform for row in rows], ["ebay"])


class GradeMappingTests(unittest.TestCase):
    def test_every_grade_observed_in_the_corpus_is_mapped(self) -> None:
        observed = {
            "PSA 10", "PSA 9", "ARS 10", "Psa8以下", "D", "BGS 10", "Bgs9以下", "Ars8以下",
            "Bgs10 Gl", "C", "Bgs10 Bl", "BGS 9.5", "ARS 9", "ARS 10+", "CGC 10", "Ungraded",
        }
        self.assertEqual(observed - set(ingest.GRADE_MAP), set())

    def test_grade_codes_fit_the_column_widths(self) -> None:
        for grader_code, grade_label in ingest.GRADE_MAP.values():
            self.assertLessEqual(len(grader_code), 8)
            self.assertLessEqual(len(grade_label), 32)

    def test_psa10_maps_the_same_way_as_the_existing_ebay_rows(self) -> None:
        self.assertEqual(ingest.GRADE_MAP["PSA 10"], ("psa", "10"))
        self.assertEqual(ingest.GRADE_MAP["Bgs10 Bl"], ("bgs", "BL"))

    def test_unknown_grade_is_rejected_and_recorded_rather_than_guessed(self) -> None:
        stats: Counter = Counter()
        unknown: Counter = Counter()
        rows = build_rows({"sales": [sale("2026-07-19T00:00:00+00:00", "2026-07-25", 10.0, grade="SGC 10")]},
                          stats=stats, unknown=unknown)
        self.assertEqual(rows, [])
        self.assertEqual(stats["row_rejected_grade"], 1)
        self.assertEqual(unknown["SGC 10"], 1)


class RowRejectionTests(unittest.TestCase):
    def test_non_positive_and_non_numeric_prices_are_rejected(self) -> None:
        stats: Counter = Counter()
        rows = build_rows({"sales": [
            sale("2026-07-19T00:00:00+00:00", "2026-07-25", 0),
            sale("2026-07-19T00:00:00+00:00", "2026-07-25", -5),
            sale("2026-07-19T00:00:00+00:00", "2026-07-25", "119"),
            sale("2026-07-19T00:00:00+00:00", "2026-07-25", True),
        ]}, stats=stats)
        self.assertEqual(rows, [])
        self.assertEqual(stats["row_rejected_price"], 4)

    def test_malformed_documents_do_not_raise(self) -> None:
        for document in (None, [], {"sales": "nope"}, {}):
            with self.subTest(document=document):
                self.assertEqual(build_rows(document), [])

    def test_unparsed_dt_is_kept_but_quarantined(self) -> None:
        rows = build_rows({"sales": [sale("yesterday", "2026-07-25", 10.0)]})
        self.assertEqual(len(rows), 1)
        self.assertIsNone(rows[0].sold_at)
        self.assertEqual(rows[0].coverage_status, ingest.QUARANTINED_COVERAGE)

    def test_accepted_rows_use_the_coverage_the_snapshot_expects(self) -> None:
        rows = build_rows({"sales": [sale("2026-07-19T00:00:00+00:00", "2026-07-25", 10.0)]})
        self.assertEqual(rows[0].coverage_status, "partial")


class ForbiddenTableTests(unittest.TestCase):
    """呢個 pipeline 唔准掂另外兩張表。"""

    def test_module_never_writes_the_aggregate_or_price_tables(self) -> None:
        source = MODULE_PATH.read_text(encoding="utf-8")
        for table in ("market_daily_sales_aggregate", "market_price_observation"):
            self.assertNotIn(f"INSERT INTO {table}", source)
            self.assertNotIn(f"UPDATE {table}", source)

    def test_source_code_cannot_collide_with_the_ebay_ingest(self) -> None:
        self.assertNotEqual(ingest.SOURCE_CODE, "ebay")


class CollectTests(unittest.TestCase):
    def test_collect_skips_files_with_no_identity_and_reports_them(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "snkrdunk").mkdir()
            (root / "altxyz").mkdir()
            payload = {"version": 1, "updatedAt": "2026-07-25T21:46:53.233995+00:00",
                       "sales": [sale("2026-07-19T00:00:00+00:00", "2026-07-25", 10.0)]}
            (root / "snkrdunk" / "100081.json").write_text(json.dumps(payload), encoding="utf-8")
            (root / "snkrdunk" / "999999.json").write_text(json.dumps(payload), encoding="utf-8")
            rows, stats, hashes, unmatched, unknown = ingest.collect(
                root, ("snkrdunk",), {("snkrdunk", "100081"): 7}, None
            )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].variant_id, 7)
        self.assertEqual(stats["file_seen"], 2)
        self.assertEqual(stats["file_matched"], 1)
        self.assertEqual(stats["file_unmatched"], 1)
        self.assertEqual(unmatched, ["snkrdunk/999999"])
        self.assertEqual(len(hashes), 1)
        self.assertEqual(unknown, Counter())

    def test_fetched_at_comes_from_updated_at_not_file_mtime(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "snkrdunk").mkdir()
            payload = {"version": 1, "updatedAt": "2026-07-25T21:46:53.233995+00:00",
                       "sales": [sale("2026-07-19T00:00:00+00:00", "2026-07-25", 10.0)]}
            (root / "snkrdunk" / "100081.json").write_text(json.dumps(payload), encoding="utf-8")
            rows, stats, _, _, _ = ingest.collect(root, ("snkrdunk",), {("snkrdunk", "100081"): 7}, None)
        self.assertEqual(rows[0].fetched_at, FETCHED_AT)
        self.assertEqual(stats["file_updatedat_missing"], 0)

    def test_the_same_sale_reaching_one_variant_from_two_providers_is_written_once(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "snkrdunk").mkdir()
            (root / "altxyz").mkdir()
            payload = {"version": 1, "updatedAt": "2026-07-25T21:46:53.233995+00:00",
                       "sales": [sale("2026-07-19T00:00:00+00:00", "2026-07-25", 10.0)]}
            (root / "snkrdunk" / "100081.json").write_text(json.dumps(payload), encoding="utf-8")
            (root / "altxyz" / "abc-uuid.json").write_text(json.dumps(payload), encoding="utf-8")
            rows, stats, _, _, _ = ingest.collect(
                root, ("snkrdunk",),
                {("snkrdunk", "100081"): 7, ("altxyz", "abc-uuid"): 7}, None
            )
        self.assertEqual(len(rows), 1)
        self.assertEqual(stats["row_fingerprint_collapsed"], 1)

    def test_limit_caps_the_number_of_files_scanned(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "snkrdunk").mkdir()
            payload = {"version": 1, "updatedAt": "2026-07-25T21:46:53.233995+00:00", "sales": []}
            for name in ("1.json", "2.json", "3.json"):
                (root / "snkrdunk" / name).write_text(json.dumps(payload), encoding="utf-8")
            _, stats, _, _, _ = ingest.collect(root, ("snkrdunk",), {}, 2)
        self.assertEqual(stats["file_seen"], 2)


class MoneyTests(unittest.TestCase):
    def test_money_quantizes_to_the_column_scale(self) -> None:
        self.assertEqual(ingest.money(Decimal("219.74983044469332")), "219.749830")
        self.assertEqual(ingest.money(Decimal("16100")), "16100.000000")


if __name__ == "__main__":
    unittest.main()
