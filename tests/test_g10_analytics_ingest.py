"""`pipelines/g10_analytics_ingest.py` 嘅回歸測試（唔掂 DB）。

重點守住三件容易出事嘅嘢：
1. `carried` 語意 —— 真成交日係 `carried=0`，唔係 `tx=0`（實測 1,667 條
   `carried=1 & tx>0`，用 tx 判斷會多當 1,667 日成交）。
2. payload / aggregate 嘅 sha256 要 deterministic，否則重跑會爆新行、唔 idempotent。
3. `market_ingest_run` 四個 count 唔准全部 0，而且 observed = accepted + quarantined + rejected。
"""

from __future__ import annotations

import importlib.util
import sys
import unittest
from collections import Counter
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "pipelines" / "g10_analytics_ingest.py"
SPEC = importlib.util.spec_from_file_location("cardz_g10_analytics_ingest", MODULE_PATH)
assert SPEC and SPEC.loader
ingest = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = ingest
SPEC.loader.exec_module(ingest)


def kline_row(**overrides):
    row = {
        "date": "2026-07-25",
        "open": "1000",
        "high": "1200",
        "low": "900",
        "close": "1100",
        "tx": "3",
        "volumeUsd": "3300",
        "carried": "0",
    }
    row.update(overrides)
    return row


def metric(**overrides):
    entry = {
        "id": "100090",
        "source": "snkrdunk",
        "name": "Sample Card",
        "volume1d": 2,
        "volume1dUsd": 1500.5,
        "volume7d": 9,
        "volume7dUsd": 6800.0,
        "volume30d": 40,
        "volume30dUsd": 31000.25,
    }
    entry.update(overrides)
    return entry


class ExternalKeyTests(unittest.TestCase):
    def test_altxyz_maps_to_ebay_identity_source(self):
        self.assertEqual(ingest.PROVIDER_IDENTITY_SOURCE["altxyz"], "ebay")
        self.assertEqual(ingest.external_key("altxyz", "06eaecff-1"), "ebay:06eaecff-1")

    def test_snkrdunk_keeps_its_own_source(self):
        self.assertEqual(ingest.external_key("snkrdunk", "100090"), "snkrdunk:100090")

    def test_kline_stem_split(self):
        self.assertEqual(
            ingest.split_kline_stem("altxyz_06eaecff-6b2e-4f_PSA_10"), ("altxyz", "06eaecff-6b2e-4f")
        )
        self.assertEqual(ingest.split_kline_stem("snkrdunk_471633_PSA_10"), ("snkrdunk", "471633"))

    def test_kline_stem_rejects_other_grades_and_providers(self):
        self.assertIsNone(ingest.split_kline_stem("snkrdunk_471633_PSA_9"))
        self.assertIsNone(ingest.split_kline_stem("bogus_471633_PSA_10"))
        self.assertIsNone(ingest.split_kline_stem("_PSA_10"))


class CarriedSemanticsTests(unittest.TestCase):
    """`carried=0` 先係真成交日 —— 唔准退化返用 `tx`。"""

    def test_carried_zero_is_a_real_trade_day(self):
        bar = ingest.parse_kline_row(kline_row(carried="0", tx="3"))
        self.assertTrue(bar.real_trade_day)

    def test_carried_one_is_not_a_trade_day_even_when_tx_positive(self):
        # 實測 47,582 條入面有 1,667 條係 carried=1 而 tx>0。
        bar = ingest.parse_kline_row(kline_row(carried="1", tx="7"))
        self.assertFalse(bar.real_trade_day)
        self.assertEqual(bar.tx, 7)

    def test_carried_zero_with_zero_tx_still_counts_as_real(self):
        bar = ingest.parse_kline_row(kline_row(carried="0", tx="0"))
        self.assertTrue(bar.real_trade_day)

    def test_carried_flag_survives_into_payload(self):
        payload = ingest.kline_payload(ingest.parse_kline_row(kline_row(carried="1")))
        self.assertEqual(payload["carried"], 1)
        self.assertEqual(payload["grade"], ingest.KLINE_GRADE)


class KlineParsingTests(unittest.TestCase):
    def test_parses_typed_fields(self):
        bar = ingest.parse_kline_row(kline_row())
        self.assertEqual(bar.bar_date, date(2026, 7, 25))
        self.assertEqual((bar.open_usd, bar.high_usd, bar.low_usd, bar.close_usd), (1000.0, 1200.0, 900.0, 1100.0))
        self.assertEqual(bar.volume_usd, 3300.0)

    def test_rejects_ohlc_violation(self):
        self.assertIsNone(ingest.parse_kline_row(kline_row(high="800")))
        self.assertIsNone(ingest.parse_kline_row(kline_row(low="1500")))

    def test_rejects_non_positive_price_and_negative_volume(self):
        self.assertIsNone(ingest.parse_kline_row(kline_row(low="0", open="0", close="0", high="0")))
        self.assertIsNone(ingest.parse_kline_row(kline_row(volumeUsd="-1")))
        self.assertIsNone(ingest.parse_kline_row(kline_row(tx="-1")))

    def test_rejects_garbage_and_missing_columns(self):
        self.assertIsNone(ingest.parse_kline_row(kline_row(date="not-a-date")))
        self.assertIsNone(ingest.parse_kline_row({"date": "2026-07-25"}))

    def test_loader_drops_duplicate_dates_and_hashes_file(self):
        tmp = ROOT / "temp" / "snkrdunk_999999test_PSA_10.csv"
        tmp.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(
            "date,open,high,low,close,tx,volumeUsd,carried\n"
            "2026-07-24,10,10,10,10,1,10,0\n"
            "2026-07-24,11,11,11,11,1,11,0\n"
            "2026-07-25,12,12,12,12,0,0,1\n"
            "2026-07-26,9,1,1,1,0,0,0\n",
            encoding="utf-8",
        )
        try:
            stats: Counter = Counter()
            bars, file_sha = ingest.load_kline_file(tmp, stats)
        finally:
            tmp.unlink()
        self.assertEqual(len(bars), 2)
        self.assertEqual(stats["kline_row_duplicate_date"], 1)
        self.assertEqual(stats["kline_row_rejected"], 1)  # open=9 > high=1
        self.assertEqual(stats["kline_row_seen"], 4)
        self.assertEqual(len(file_sha), 64)
        self.assertEqual([bar.provider for bar in bars], ["snkrdunk", "snkrdunk"])


class DeterminismTests(unittest.TestCase):
    """sha256 唔穩定 = 重跑爆新行 = 唔 idempotent。"""

    def test_payload_sha_is_stable_across_calls(self):
        bar = ingest.parse_kline_row(kline_row())
        first = ingest.sha256_text(ingest.canonical_json(ingest.kline_payload(bar)))
        second = ingest.sha256_text(ingest.canonical_json(ingest.kline_payload(bar)))
        self.assertEqual(first, second)

    def test_payload_sha_ignores_dict_insertion_order(self):
        left = ingest.canonical_json({"b": 1, "a": 2})
        right = ingest.canonical_json({"a": 2, "b": 1})
        self.assertEqual(left, right)

    def test_payload_sha_changes_when_price_changes(self):
        base = ingest.kline_payload(ingest.parse_kline_row(kline_row()))
        moved = ingest.kline_payload(ingest.parse_kline_row(kline_row(close="1101")))
        self.assertNotEqual(ingest.sha256_text(ingest.canonical_json(base)),
                            ingest.sha256_text(ingest.canonical_json(moved)))

    def test_observation_sha_matches_payload_text(self):
        observation = ingest.Observation(
            source_code=ingest.SOURCE_CODE,
            external_entity_id="snkrdunk:1",
            observation_kind=ingest.KIND_KLINE_DAILY,
            effective_at=datetime(2026, 7, 25),
            observed_date=date(2026, 7, 25),
            payload={"a": 1},
            observed_at=datetime(2026, 7, 25, 1),
        )
        self.assertEqual(observation.payload_sha256, ingest.sha256_text(observation.payload_text))

    def test_tracked_rows_are_bit_identical_on_repeat(self):
        identity = {("snkrdunk", "100090"): 7}
        as_of = datetime(2026, 7, 25, 21, 46, 50)
        first = ingest.tracked_sales_rows([metric()], identity, as_of, Counter())
        second = ingest.tracked_sales_rows([metric()], identity, as_of, Counter())
        self.assertEqual([r["aggregate_sha256"] for r in first], [r["aggregate_sha256"] for r in second])
        self.assertEqual([r["window_end_at"] for r in first], [r["window_end_at"] for r in second])


class TrackedSalesTests(unittest.TestCase):
    def test_emits_one_row_per_window_for_matched_variant(self):
        rows = ingest.tracked_sales_rows(
            [metric()], {("snkrdunk", "100090"): 7}, datetime(2026, 7, 25), Counter()
        )
        self.assertEqual({row["window_code"] for row in rows}, {"1d", "7d", "30d"})
        self.assertTrue(all(row["variant_id"] == 7 for row in rows))
        self.assertTrue(all(row["grader_code"] == "psa" and row["grade_label"] == "10" for row in rows))
        self.assertTrue(all(row["coverage_status"] == ingest.COVERAGE_PARTIAL for row in rows))

    def test_window_bounds_span_the_declared_days(self):
        rows = ingest.tracked_sales_rows(
            [metric()], {("snkrdunk", "100090"): 7}, datetime(2026, 7, 25), Counter()
        )
        by_window = {row["window_code"]: row for row in rows}
        self.assertEqual((by_window["30d"]["window_end_at"] - by_window["30d"]["window_start_at"]).days, 30)
        self.assertEqual((by_window["7d"]["window_end_at"] - by_window["7d"]["window_start_at"]).days, 7)

    def test_unmatched_variant_writes_nothing(self):
        self.assertEqual(ingest.tracked_sales_rows([metric()], {}, datetime(2026, 7, 25), Counter()), [])

    def test_altxyz_resolves_through_ebay_identity(self):
        rows = ingest.tracked_sales_rows(
            [metric(source="altxyz", id="06eaecff-1")],
            {("ebay", "06eaecff-1"): 11},
            datetime(2026, 7, 25),
            Counter(),
        )
        self.assertTrue(rows and all(row["variant_id"] == 11 for row in rows))

    def test_missing_volume_is_skipped_not_zero_filled(self):
        """缺失同零係兩件事 —— 唔准填 0 當「冇成交」。"""

        stats: Counter = Counter()
        rows = ingest.tracked_sales_rows(
            [metric(volume7d=None, volume30dUsd=None)],
            {("snkrdunk", "100090"): 7},
            datetime(2026, 7, 25),
            stats,
        )
        self.assertEqual({row["window_code"] for row in rows}, {"1d"})
        self.assertEqual(stats["window_missing_7d"], 1)
        self.assertEqual(stats["window_missing_30d"], 1)

    def test_genuine_zero_volume_is_written(self):
        rows = ingest.tracked_sales_rows(
            [metric(volume1d=0, volume1dUsd=0)],
            {("snkrdunk", "100090"): 7},
            datetime(2026, 7, 25),
            Counter(),
        )
        one_day = next(row for row in rows if row["window_code"] == "1d")
        self.assertEqual(one_day["sales_count"], 0)
        self.assertEqual(one_day["sales_value_usd"], Decimal("0"))

    def test_negative_volume_is_rejected(self):
        stats: Counter = Counter()
        rows = ingest.tracked_sales_rows(
            [metric(volume1d=-3)], {("snkrdunk", "100090"): 7}, datetime(2026, 7, 25), stats
        )
        self.assertNotIn("1d", {row["window_code"] for row in rows})
        self.assertEqual(stats["window_rejected_1d"], 1)

    def test_bool_is_not_accepted_as_count(self):
        stats: Counter = Counter()
        ingest.tracked_sales_rows(
            [metric(volume1d=True)], {("snkrdunk", "100090"): 7}, datetime(2026, 7, 25), stats
        )
        self.assertEqual(stats["window_missing_1d"], 1)

    def test_money_quantises_to_six_decimals(self):
        self.assertEqual(ingest.money(Decimal("1500.5")), "1500.500000")


class MetricObservationTests(unittest.TestCase):
    def test_unmatched_metric_still_lands_in_ledger(self):
        """對唔到 variant 都要落 ledger —— identity 擴充之後重跑先撿得返。"""

        stats: Counter = Counter()
        observations = ingest.metric_observations(
            [metric()], datetime(2026, 7, 25), datetime(2026, 7, 25, 1), stats
        )
        self.assertEqual(len(observations), 1)
        self.assertEqual(observations[0].external_entity_id, "snkrdunk:100090")
        self.assertEqual(observations[0].observation_kind, ingest.KIND_CARD_METRICS)
        self.assertEqual(observations[0].source_code, ingest.SOURCE_CODE)

    def test_unknown_provider_is_rejected(self):
        stats: Counter = Counter()
        observations = ingest.metric_observations(
            [metric(source="mystery")], datetime(2026, 7, 25), datetime(2026, 7, 25), stats
        )
        self.assertEqual(observations, [])
        self.assertEqual(stats["metric_row_rejected"], 1)

    def test_blank_id_is_rejected(self):
        stats: Counter = Counter()
        ingest.metric_observations([metric(id="")], datetime(2026, 7, 25), datetime(2026, 7, 25), stats)
        self.assertEqual(stats["metric_row_rejected"], 1)

    def test_kline_observation_uses_bar_date_as_effective_and_observed_date(self):
        bar = ingest.parse_kline_row(kline_row())
        bar = ingest.KlineBar(
            provider="snkrdunk", entity_id="100090", bar_date=bar.bar_date, open_usd=bar.open_usd,
            high_usd=bar.high_usd, low_usd=bar.low_usd, close_usd=bar.close_usd, tx=bar.tx,
            volume_usd=bar.volume_usd, carried=bar.carried,
        )
        fetched = datetime(2026, 7, 26, 3, 0, 0)
        observation = ingest.kline_observations([bar], fetched)[0]
        self.assertEqual(observation.observed_date, date(2026, 7, 25))
        self.assertEqual(observation.effective_at, datetime(2026, 7, 25, 0, 0, 0))
        self.assertEqual(observation.observed_at, fetched)


class RunAccountingTests(unittest.TestCase):
    """`market_ingest_run` 四個 count 唔准全部 0，而且要對得掂。"""

    def _collected(self, **overrides):
        defaults = dict(
            observations=[object()], bars=[], metrics=[], file_hashes=[], as_of=datetime(2026, 7, 25),
            unmatched_metrics=[], unmatched_klines=[], unmatched_bar_count=0,
        )
        defaults.update(overrides)
        return ingest.Collected(**defaults)

    def test_counts_are_internally_consistent(self):
        stats = Counter({
            "metric_row_seen": 641, "kline_row_seen": 47582, "index_file_seen": 27,
            "metric_row_rejected": 1, "kline_row_rejected": 2, "kline_row_duplicate_date": 0,
            "index_file_missing": 3, "index_file_rejected": 0, "metric_unmatched": 246,
        })
        collected = self._collected(unmatched_bar_count=17000)
        counts = ingest.run_counts(collected, stats)
        self.assertEqual(counts["observed"], 641 + 47582 + 27)
        self.assertEqual(counts["rejected"], 1 + 2 + 3)
        self.assertEqual(counts["quarantined"], 246 + 17000)
        self.assertEqual(
            counts["accepted"], counts["observed"] - counts["rejected"] - counts["quarantined"]
        )

    def test_counts_are_not_all_zero_for_real_input(self):
        stats = Counter({"metric_row_seen": 641, "kline_row_seen": 100, "index_file_seen": 27})
        counts = ingest.run_counts(self._collected(), stats)
        self.assertGreater(counts["observed"], 0)
        self.assertGreater(counts["accepted"], 0)

    def test_accepted_never_goes_negative(self):
        stats = Counter({"metric_row_seen": 10, "metric_row_rejected": 10, "metric_unmatched": 10})
        counts = ingest.run_counts(self._collected(), stats)
        self.assertEqual(counts["accepted"], 0)


class ForbiddenWriteTests(unittest.TestCase):
    """硬禁：呢個 loader 唔准掂 `market_daily_sales_aggregate`（會撞 snapshot 個
    無 source filter 嘅 `daily_history()`），亦唔准改 catalog 身份欄。"""

    SOURCE = MODULE_PATH.read_text(encoding="utf-8")

    def test_never_writes_market_daily_sales_aggregate(self):
        for statement in ("INSERT INTO market_daily_sales_aggregate",
                          "INSERT IGNORE INTO market_daily_sales_aggregate",
                          "UPDATE market_daily_sales_aggregate",
                          "DELETE FROM market_daily_sales_aggregate"):
            self.assertNotIn(statement, self.SOURCE)

    def test_never_updates_catalog_identity_columns(self):
        for statement in ("UPDATE catalog_variant", "UPDATE catalog_source_identity",
                          "INSERT INTO catalog_variant", "canonical_name="):
            self.assertNotIn(statement, self.SOURCE)

    def test_never_writes_market_price_observation(self):
        self.assertNotIn("INTO market_price_observation", self.SOURCE)

    def test_no_canonical_tables_are_written(self):
        written = {line.split("INTO", 1)[1].strip().split()[0]
                   for line in self.SOURCE.splitlines() if "INSERT" in line and "INTO" in line}
        self.assertEqual(written, set())

    def test_never_writes_market_tracked_sales_aggregate(self):
        """2026-07-26 起嗰張表冇 consumer 又冇 source_code 欄
        (`scripts/audit_wiring_gaps.py:89`)，寫落去會重演
        `market_daily_sales_aggregate` 個分唔清源嘅病。"""

        for statement in ("INSERT INTO market_tracked_sales_aggregate",
                          "INSERT IGNORE INTO market_tracked_sales_aggregate",
                          "UPDATE market_tracked_sales_aggregate"):
            self.assertNotIn(statement, self.SOURCE)

    def test_window_mapping_is_still_specified_for_the_future_table(self):
        """唔寫唔代表唔知點對 —— `tracked_sales_rows()` 保留住做可執行規格。"""

        rows = ingest.tracked_sales_rows(
            [metric()], {("snkrdunk", "100090"): 7}, datetime(2026, 7, 25), Counter()
        )
        self.assertEqual(len(rows), 3)


class ParseHelperTests(unittest.TestCase):
    def test_parses_offset_and_zulu_timestamps(self):
        self.assertEqual(
            ingest.parse_iso_datetime("2026-07-25T21:46:50.660461+00:00"),
            datetime(2026, 7, 25, 21, 46, 50, 660461),
        )
        self.assertEqual(
            ingest.parse_iso_datetime("2026-07-23T00:00:00.000Z"), datetime(2026, 7, 23, 0, 0, 0)
        )

    def test_returns_none_for_junk(self):
        self.assertIsNone(ingest.parse_iso_datetime(""))
        self.assertIsNone(ingest.parse_iso_datetime(None))
        self.assertIsNone(ingest.parse_iso_datetime("yesterday"))


if __name__ == "__main__":
    unittest.main()
