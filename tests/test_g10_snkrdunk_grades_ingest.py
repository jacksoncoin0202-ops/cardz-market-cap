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
MODULE_PATH = ROOT / "pipelines" / "g10_snkrdunk_grades_ingest.py"
SPEC = importlib.util.spec_from_file_location("cardz_g10_snkrdunk_grades_ingest", MODULE_PATH)
assert SPEC and SPEC.loader
ingest = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = ingest
SPEC.loader.exec_module(ingest)

FETCHED_AT = datetime(2026, 7, 25, 21, 37, 0)


def sale(date_text: str, grade: str, price: float, *, bundle: int = 1, tx: float | None = None) -> dict:
    return {
        "date": date_text,
        "grade": grade,
        "price": price,
        "txAmount": price * bundle if tx is None else tx,
        "currency": "usd",
        "bundleSize": bundle,
    }


def build_rows(
    document: object,
    *,
    grade_key: str = "23",
    fetched_at: datetime = FETCHED_AT,
    stats: Counter | None = None,
    seen: set[str] | None = None,
) -> list:
    return ingest.sale_rows_from_document(
        document,
        variant_id=7,
        external_entity_id="100081",
        grade_key=grade_key,
        fetched_at=fetched_at,
        payload_sha256="0" * 64,
        stats=stats if stats is not None else Counter(),
        seen=seen,
    )


class GradeMappingTests(unittest.TestCase):
    """N → grade 對照表由數據實測得出，呢度守住佢唔俾人靜靜改。"""

    def test_every_expected_file_label_is_mapped_to_a_grader(self) -> None:
        for grade_key, label in ingest.GRADE_FILE_EXPECTED_LABEL.items():
            with self.subTest(grade_key=grade_key):
                self.assertIn(label, ingest.SNKRDUNK_LABELS)

    def test_the_measured_mapping_is_pinned(self) -> None:
        self.assertEqual(
            ingest.GRADE_FILE_EXPECTED_LABEL,
            {
                "20": "C", "21": "D", "22": "PSA10", "23": "PSA9", "24": "PSA8以下",
                "25": "BGS10 BL", "26": "BGS10 GL", "27": "BGS9.5", "28": "BGS9以下",
                "29": "ARS10+", "30": "ARS10", "31": "ARS9", "32": "ARS8以下",
            },
        )
        self.assertEqual(ingest.SNKRDUNK_LABELS["PSA9"], ("psa", "9"))
        self.assertEqual(ingest.SNKRDUNK_LABELS["PSA8以下"], ("psa", "8_OR_LOWER"))
        self.assertEqual(ingest.SNKRDUNK_LABELS["BGS10 BL"], ("bgs", "10_BL"))
        self.assertEqual(ingest.SNKRDUNK_LABELS["ARS10+"], ("ars", "10_PLUS"))
        self.assertEqual(ingest.SNKRDUNK_LABELS["C"], ("raw", "C"))

    def test_grader_and_label_fit_the_column_widths(self) -> None:
        for label, (grader_code, grade_label) in ingest.SNKRDUNK_LABELS.items():
            with self.subTest(label=label):
                self.assertLessEqual(len(grader_code), 8)   # varchar(8)
                self.assertLessEqual(len(grade_label), 32)  # varchar(32)

    def test_grade_22_is_never_in_the_default_ingest_set(self) -> None:
        self.assertNotIn("22", ingest.INGEST_GRADE_KEYS)
        self.assertIn("22", ingest.SKIPPED_GRADE_KEYS)

    def test_default_ingest_set_is_the_twelve_ungrafted_grades_plus_the_mixed_feed(self) -> None:
        self.assertEqual(len(ingest.INGEST_GRADE_KEYS), 13)  # 12 個專屬 grade + `-1`
        self.assertEqual(ingest.INGEST_GRADE_KEYS[0], "-1")
        self.assertEqual(
            sorted(key for key in ingest.INGEST_GRADE_KEYS if key != "-1"),
            sorted(["20", "21", "23", "24", "25", "26", "27", "28", "29", "30", "31", "32"]),
        )

    def test_source_code_is_distinct_from_the_existing_snkrdunk_lines(self) -> None:
        self.assertNotIn(ingest.SOURCE_CODE, {"snk_psa10", "snkrdunk", "ebay", "gemrate", "tag"})
        self.assertLessEqual(len(ingest.SOURCE_CODE), 32)  # varchar(32)


class DateResolutionTests(unittest.TestCase):
    def test_iso_date_resolves_to_midnight_and_is_marked_exact(self) -> None:
        sold_at, quality = ingest.resolve_sale_date("2026-04-25", FETCHED_AT)
        self.assertEqual(sold_at, datetime(2026, 4, 25, 0, 0, 0))
        self.assertEqual(quality, "exact_date")

    def test_relative_days_subtract_from_the_mtime_calendar_day(self) -> None:
        for text, expected in (
            ("0 day ago", date(2026, 7, 25)),
            ("1 day ago", date(2026, 7, 24)),
            ("2 days ago", date(2026, 7, 23)),
            ("3 days ago", date(2026, 7, 22)),
        ):
            with self.subTest(text=text):
                sold_at, quality = ingest.resolve_sale_date(text, FETCHED_AT)
                self.assertEqual(sold_at, datetime.combine(expected, datetime.min.time()))
                self.assertEqual(quality, "relative_resolved")

    def test_sub_day_offsets_are_parsed_not_dropped(self) -> None:
        """`N hours ago` / `N minutes ago` 佔 4.56%，eBay loader 冇呢兩個
        regex，唔加就即刻頂到 5% 閘。"""

        sold_at, quality = ingest.resolve_sale_date("14 hours ago", FETCHED_AT)
        self.assertEqual(sold_at, datetime(2026, 7, 25, 0, 0, 0))
        self.assertEqual(quality, "relative_subday")

        sold_at, quality = ingest.resolve_sale_date("30 minutes ago", FETCHED_AT)
        self.assertEqual(sold_at, datetime(2026, 7, 25, 0, 0, 0))
        self.assertEqual(quality, "relative_subday")

    def test_sub_day_offset_is_taken_off_the_clock_not_the_calendar_day(self) -> None:
        """mtime 02:00 UTC 減 5 個鐘要退到**前一日**。由曆日減會得返同一日，
        即係遲咗一日；由曆日減 1 日又會早咗。"""

        early = datetime(2026, 7, 25, 2, 0, 0)
        sold_at, quality = ingest.resolve_sale_date("5 hours ago", early)
        self.assertEqual(sold_at, datetime(2026, 7, 24, 0, 0, 0))
        self.assertEqual(quality, "relative_subday")

        # 未過午夜就唔應該退日
        sold_at, _ = ingest.resolve_sale_date("1 hour ago", early)
        self.assertEqual(sold_at, datetime(2026, 7, 25, 0, 0, 0))

    def test_unparseable_text_yields_null_sold_at_but_keeps_the_original_string(self) -> None:
        for text in ("", "last week", "2026/04/25", "yesterday", "2026-13-45", "a few days ago"):
            with self.subTest(text=text):
                sold_at, quality = ingest.resolve_sale_date(text, FETCHED_AT)
                self.assertIsNone(sold_at)
                self.assertEqual(quality, "unparsed")

        rows = build_rows({"saleHistory": [sale("last week", "PSA9", 100)]})
        self.assertEqual(len(rows), 1)
        self.assertIsNone(rows[0].sold_at)
        self.assertEqual(rows[0].source_date_text, "last week")
        self.assertEqual(rows[0].timestamp_quality, "unparsed")
        self.assertEqual(rows[0].coverage_status, "quarantined")
        self.assertFalse(rows[0].accepted)


class MtimeBaseTests(unittest.TestCase):
    def test_relative_base_comes_from_file_mtime_utc_not_from_today(self) -> None:
        """檔可能係幾日前抄落嚟。用 now() 會令成批相對日期整體偏移。"""

        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "apparel_grade_23.json"
            path.write_text("{}", encoding="utf-8")
            pinned = datetime(2026, 5, 2, 15, 30, 0, tzinfo=timezone.utc).timestamp()
            os.utime(path, (pinned, pinned))

            fetched_at = ingest.mtime_fetched_at(path)
            self.assertEqual(fetched_at, datetime(2026, 5, 2, 15, 30, 0))
            self.assertIsNone(fetched_at.tzinfo)
            self.assertNotEqual(fetched_at.date(), datetime.now().date())

            sold_at, _ = ingest.resolve_sale_date("2 days ago", fetched_at)
            self.assertEqual(sold_at, datetime(2026, 4, 30, 0, 0, 0))

    def test_mtime_day_is_the_utc_calendar_day(self) -> None:
        """實測：UTC 基準嘅「最舊相對日 − 最新 ISO 日」眾數 = 1（無縫），
        本機基準 = 2（系統性缺一日）。呢個 case 釘住 UTC。"""

        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "apparel_grade_23.json"
            path.write_text("{}", encoding="utf-8")
            # 2026-05-03 00:30 UTC。喺 JST(+9) 係 05-03 09:30，喺 UTC-8 係 05-02。
            pinned = datetime(2026, 5, 3, 0, 30, 0, tzinfo=timezone.utc).timestamp()
            os.utime(path, (pinned, pinned))
            self.assertEqual(ingest.mtime_fetched_at(path).date(), date(2026, 5, 3))


class MixedFeedTests(unittest.TestCase):
    """`apparel_grade_-1` 係「全 grade 未過濾」嘅第一頁，唔係 raw。"""

    def test_mixed_feed_routes_each_row_by_its_own_grade_field(self) -> None:
        stats: Counter = Counter()
        rows = build_rows(
            {
                "saleHistory": [
                    sale("2026-07-20", "PSA9", 70.0),
                    sale("2026-07-19", "A", 20.0),
                    sale("2026-07-18", "BGS9.5", 300.0),
                    sale("2026-07-17", "他鑑定品", 55.0),
                ]
            },
            grade_key="-1",
            stats=stats,
        )
        self.assertEqual(
            [(row.grader_code, row.grade_label) for row in rows],
            [("psa", "9"), ("raw", "A"), ("bgs", "9.5"), ("other", "OTHER")],
        )
        # `-1` 冇 expected label，所以唔應該報 mismatch
        self.assertEqual(stats["row_label_mismatch"], 0)

    def test_psa10_rows_are_excluded_so_they_cannot_double_count_snk_psa10(self) -> None:
        """`-1` 有 73.86% PSA10 行同 grade_22 重覆，照入就係 double count。"""

        stats: Counter = Counter()
        rows = build_rows(
            {
                "saleHistory": [
                    sale("2026-07-20", "PSA10", 500.0),
                    sale("2026-07-20", "PSA9", 70.0),
                ]
            },
            grade_key="-1",
            stats=stats,
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].grade_label, "9")
        self.assertEqual(stats["row_excluded_psa10"], 1)
        self.assertNotIn("10", {row.grade_label for row in rows})

    def test_a_row_shared_by_the_mixed_feed_and_a_dedicated_file_is_stored_once(self) -> None:
        """同一張卡跨檔共用 `seen`，所以 `-1` ∩ grade_23 嘅重覆行只會出一次。"""

        stats: Counter = Counter()
        shared: set[str] = set()
        record = sale("2026-07-20", "PSA9", 72.63952728588474)

        first = build_rows({"saleHistory": [record]}, grade_key="23", stats=stats, seen=shared)
        second = build_rows({"saleHistory": [record]}, grade_key="-1", stats=stats, seen=shared)

        self.assertEqual(len(first), 1)
        self.assertEqual(len(second), 0)
        self.assertEqual(stats["row_fingerprint_collision"], 1)


class LabelIntegrityTests(unittest.TestCase):
    def test_a_label_that_does_not_match_the_file_is_counted_not_coerced(self) -> None:
        """純度實測 100%。唔對即係源頭契約變咗，要報出嚟，唔准夾硬歸一。"""

        stats: Counter = Counter()
        rows = build_rows(
            {"saleHistory": [sale("2026-07-20", "BGS9.5", 300.0)]},
            grade_key="23",  # 呢個檔應該全部 PSA9
            stats=stats,
        )
        self.assertEqual(stats["row_label_mismatch"], 1)
        self.assertEqual(stats["row_label_mismatch_23"], 1)
        # 照跟行自己嘅標籤，唔會被檔名夾成 PSA9
        self.assertEqual((rows[0].grader_code, rows[0].grade_label), ("bgs", "9.5"))

    def test_unknown_labels_are_rejected_rather_than_guessed(self) -> None:
        stats: Counter = Counter()
        rows = build_rows(
            {"saleHistory": [sale("2026-07-20", "CGC10", 300.0), sale("2026-07-20", "PSA9", 70.0)]},
            grade_key="-1",
            stats=stats,
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(stats["row_rejected_unknown_label"], 1)
        self.assertEqual(stats["row_unknown_label::CGC10"], 1)


class RowShapeTests(unittest.TestCase):
    def test_bundle_size_becomes_quantity_and_txamount_becomes_transaction_value(self) -> None:
        """實測 271 條 price != txAmount，271/271 都啱 txAmount == price × bundleSize。"""

        rows = build_rows({"saleHistory": [sale("2026-07-20", "PSA9", 70.0, bundle=3, tx=210.0)]})
        self.assertEqual(rows[0].unit_price_usd, Decimal("70.0"))
        self.assertEqual(rows[0].quantity, 3)
        self.assertEqual(rows[0].transaction_value_usd, Decimal("210.000000"))

    def test_missing_txamount_is_derived_and_missing_bundle_defaults_to_one(self) -> None:
        stats: Counter = Counter()
        rows = build_rows(
            {"saleHistory": [{"date": "2026-07-20", "grade": "PSA9", "price": 12.5, "currency": "usd"}]},
            stats=stats,
        )
        self.assertEqual(rows[0].quantity, 1)
        self.assertEqual(rows[0].transaction_value_usd, Decimal("12.500000"))
        self.assertEqual(stats["row_bundle_defaulted"], 1)
        self.assertEqual(stats["row_txamount_derived"], 1)

    def test_bad_prices_and_non_usd_rows_are_rejected_not_filled_with_guesses(self) -> None:
        stats: Counter = Counter()
        rows = build_rows(
            {
                "saleHistory": [
                    sale("2026-07-20", "PSA9", 0),
                    sale("2026-07-20", "PSA9", -5),
                    {"date": "2026-07-20", "grade": "PSA9", "price": True, "currency": "usd"},
                    {"date": "2026-07-20", "grade": "PSA9", "price": 100, "currency": "jpy"},
                    sale("2026-07-20", "PSA9", 70.0),
                ]
            },
            stats=stats,
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(stats["row_rejected_price"], 3)
        self.assertEqual(stats["row_rejected_currency"], 1)

    def test_missing_or_malformed_history_is_reported_not_crashed(self) -> None:
        stats: Counter = Counter()
        self.assertEqual(build_rows({"averagePrice": {}}, stats=stats), [])
        self.assertEqual(stats["file_no_history"], 1)
        self.assertEqual(build_rows({"saleHistory": "nope"}, stats=stats), [])
        self.assertEqual(build_rows([], stats=stats), [])
        self.assertEqual(stats["file_invalid"], 2)


class FingerprintTests(unittest.TestCase):
    def test_fingerprint_uses_the_raw_date_text_so_reruns_are_stable(self) -> None:
        """相對日期解析結果會隨 mtime 變；fingerprint 食原文先至重跑 idempotent。"""

        early = build_rows({"saleHistory": [sale("1 day ago", "PSA9", 70.0)]}, fetched_at=FETCHED_AT)
        later = build_rows(
            {"saleHistory": [sale("1 day ago", "PSA9", 70.0)]},
            fetched_at=datetime(2026, 8, 9, 3, 0, 0),
        )
        self.assertNotEqual(early[0].sold_at, later[0].sold_at)
        self.assertEqual(early[0].fingerprint, later[0].fingerprint)

    def test_fingerprint_separates_bundle_sizes(self) -> None:
        one = build_rows({"saleHistory": [sale("2026-07-20", "PSA9", 70.0, bundle=1)]})
        two = build_rows({"saleHistory": [sale("2026-07-20", "PSA9", 70.0, bundle=2)]})
        self.assertNotEqual(one[0].fingerprint, two[0].fingerprint)

    def test_fingerprint_separates_grades_of_the_same_card_and_day(self) -> None:
        rows = build_rows(
            {"saleHistory": [sale("2026-07-20", "PSA9", 70.0), sale("2026-07-20", "ARS9", 70.0)]},
            grade_key="-1",
        )
        self.assertEqual(len({row.fingerprint for row in rows}), 2)

    def test_float_and_int_prices_produce_the_same_fingerprint(self) -> None:
        as_int = build_rows({"saleHistory": [sale("2026-07-20", "PSA9", 70)]})
        as_float = build_rows({"saleHistory": [sale("2026-07-20", "PSA9", 70.0)]})
        self.assertEqual(as_int[0].fingerprint, as_float[0].fingerprint)


class ForbiddenWriteTests(unittest.TestCase):
    """呢個 loader 只准寫 market_sale_observation。"""

    SOURCE = MODULE_PATH.read_text(encoding="utf-8")

    def test_it_never_writes_the_daily_sales_aggregate(self) -> None:
        """`canonical_public_snapshot.daily_history()` 讀嗰張表冇 source filter，
        last-row-wins，寫落去會蓋咗前端 sparkline 嘅成交數。"""

        self.assertNotIn("INSERT INTO market_daily_sales_aggregate", self.SOURCE)
        self.assertNotIn("UPDATE market_daily_sales_aggregate", self.SOURCE)

    def test_it_never_writes_price_observations(self) -> None:
        """UNIQUE key 係 (variant_id, source_code, observed_date)，冇 grade 維度；
        而 36.2% (variant, day) 喺嗰張表冇任何其他源，寫落去會直接變咗代表價。"""

        self.assertNotIn("INSERT INTO market_price_observation", self.SOURCE)
        self.assertNotIn("UPDATE market_price_observation", self.SOURCE)

    def test_it_only_inserts_into_the_two_expected_tables(self) -> None:
        inserted = {
            line.split("INSERT INTO", 1)[1].strip().split()[0]
            for line in self.SOURCE.splitlines()
            if "INSERT INTO" in line
        }
        self.assertEqual(inserted, {"market_sale_observation", "market_ingest_run"})


class RunCounterTests(unittest.TestCase):
    def test_gate_is_five_percent(self) -> None:
        self.assertEqual(ingest.UNPARSED_FAIL_RATIO, 0.05)

    def test_counters_reconcile_to_observed(self) -> None:
        """`market_ingest_run` 唔准全部寫 0：observed 要等於
        accepted + quarantined + rejected。"""

        stats: Counter = Counter()
        rows = build_rows(
            {
                "saleHistory": [
                    sale("2026-07-20", "PSA9", 70.0),        # accepted
                    sale("last week", "PSA9", 71.0),         # quarantined
                    sale("2026-07-20", "PSA10", 500.0),      # rejected (psa10)
                    sale("2026-07-20", "CGC10", 500.0),      # rejected (unknown)
                    sale("2026-07-20", "PSA9", 0),           # rejected (price)
                ]
            },
            grade_key="-1",
            stats=stats,
        )
        observed = stats["row_seen"]
        accepted = sum(1 for row in rows if row.accepted)
        quarantined = len(rows) - accepted
        rejected = observed - accepted - quarantined

        self.assertEqual(observed, 5)
        self.assertEqual(accepted, 1)
        self.assertEqual(quarantined, 1)
        self.assertEqual(rejected, 3)
        self.assertEqual(observed, accepted + quarantined + rejected)


class RealFixtureTests(unittest.TestCase):
    """跑真檔，唔淨係跑手砌 fixture。"""

    G10 = Path(r"C:\Users\jackson0202\Documents\Playground\grade10-scraper\data\cards")

    def test_a_real_grade_file_parses_with_a_pure_label(self) -> None:
        path = self.G10 / "snkrdunk" / "100081" / "apparel_grade_23.json"
        if not path.is_file():
            self.skipTest(f"G10 fixture not present: {path}")
        stats: Counter = Counter()
        rows = ingest.sale_rows_from_document(
            json.loads(path.read_text(encoding="utf-8")),
            variant_id=7,
            external_entity_id="100081",
            grade_key="23",
            fetched_at=ingest.mtime_fetched_at(path),
            payload_sha256="0" * 64,
            stats=stats,
        )
        self.assertGreater(len(rows), 0)
        self.assertEqual(stats["row_label_mismatch"], 0)
        self.assertEqual({(row.grader_code, row.grade_label) for row in rows}, {("psa", "9")})
        self.assertEqual(stats["quality_unparsed"], 0)

    def test_the_real_mixed_feed_is_mixed_and_drops_psa10(self) -> None:
        path = self.G10 / "snkrdunk" / "100081" / "apparel_grade_-1.json"
        if not path.is_file():
            self.skipTest(f"G10 fixture not present: {path}")
        stats: Counter = Counter()
        rows = ingest.sale_rows_from_document(
            json.loads(path.read_text(encoding="utf-8")),
            variant_id=7,
            external_entity_id="100081",
            grade_key="-1",
            fetched_at=ingest.mtime_fetched_at(path),
            payload_sha256="0" * 64,
            stats=stats,
        )
        self.assertGreater(stats["row_excluded_psa10"], 0, "真 `-1` 檔應該有 PSA10 行俾我哋剔走")
        self.assertGreater(len({row.grade_label for row in rows}), 1, "`-1` 應該係混合，唔係單一 grade")
        self.assertNotIn(("psa", "10"), {(row.grader_code, row.grade_label) for row in rows})


if __name__ == "__main__":
    unittest.main()
