from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "canonical_public_snapshot",
    ROOT / "pipelines" / "canonical_public_snapshot.py",
)
assert SPEC and SPEC.loader
snapshot = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(snapshot)


class FakeConnection:
    """`fetchall()` 只用到 cursor 三個方法，唔使真 DB 就試到窗口切分。

    要按 query 分派，唔可以一律返同一批 rows：`latest_sales()` 行兩個 query
    （SNKRDUNK 日表 `market_daily_sales_aggregate` + eBay 逐筆表
    `market_sale_observation`），一律返同一批就等於每筆成交計兩次，
    窗口切分斷言會見到雙倍金額。測試 rows 係日表口徑。
    """

    def __init__(self, rows: list[dict[str, object]]) -> None:
        self._rows = rows
        self._query = ""

    def cursor(self) -> "FakeConnection":
        return self

    def __enter__(self) -> "FakeConnection":
        return self

    def __exit__(self, *_: object) -> bool:
        return False

    def execute(self, query: str, _args: tuple[object, ...] = ()) -> None:
        self._query = query

    def fetchall(self) -> list[dict[str, object]]:
        if "market_sale_observation" in self._query:
            return []
        return self._rows


class CanonicalPublicSnapshotTests(unittest.TestCase):
    def test_public_view_alias_uses_the_top300_card_count(self) -> None:
        self.assertEqual(snapshot.presentation_view_limit("top100"), 100)
        self.assertEqual(snapshot.presentation_view_limit("top300"), 300)
        self.assertEqual(snapshot.presentation_view_limit("top350"), 350)
        self.assertEqual(snapshot.presentation_view_limit("top100_plus_200"), 300)
        with self.assertRaisesRegex(snapshot.SnapshotExportError, "unknown public presentation view"):
            snapshot.presentation_view_limit("reserve50")

    def test_json_numbers_do_not_hash_integral_decimals_as_floats(self) -> None:
        self.assertEqual(snapshot.number(Decimal("100.000000")), 100)
        self.assertEqual(snapshot.number(Decimal("100.25")), 100.25)

    def presentation_card(self) -> dict[str, object]:
        document = json.loads((ROOT / "data/public/seed-snapshot.json").read_text(encoding="utf-8"))
        return document["top100"][0]

    def ranked_row(self, card: dict[str, object]) -> dict[str, object]:
        return {
            "variant_id": 7,
            "opaque_id": card["id"],
            "rank_position": 1,
            "reference_price_usd": 100.0,
            "psa10_population": 2000,
            "market_cap_usd": 200000.0,
            "metric_status": "ready",
            "change_1d_pct": None,
            "change_7d_pct": 2.5,
            "change_30d_pct": -4.0,
        }

    def psa_population(self, effective_at: datetime) -> dict[tuple[int, str], dict[str, object]]:
        return {
            (7, "PSA"): {
                "top_grade_label": "10",
                "total_population": 5000,
                "top_grade_population": 2000,
                "estimated": False,
                "effective_at": effective_at,
            }
        }

    def test_database_metrics_replace_presentation_pack_metrics(self) -> None:
        card = self.presentation_card()
        result = snapshot.card_from_row(
            self.ranked_row(card),
            card,
            "2026-07-24T00:00:00Z",
            {
                (7, "7d"): {
                    "sales_value_usd": 1200,
                    "sales_count": 3,
                    "coverage_status": "partial",
                    "window_end_at": datetime(2026, 7, 24, tzinfo=timezone.utc),
                }
            },
            self.psa_population(datetime(2026, 7, 20, 12, tzinfo=timezone.utc)),
            {},
            {7: "2026-07-23T18:00:00Z"},
        )
        self.assertEqual(result["pricePsa10"]["value"], 100.0)
        self.assertEqual(result["populationPsa10"]["value"], 2000)
        self.assertEqual(result["marketCap"]["value"], 200000.0)
        self.assertEqual(result["windows"]["1d"]["changePct"], {"value": None, "status": "accumulating", "asOf": None})
        self.assertEqual(result["windows"]["7d"]["trackedSales"]["valueUsd"]["value"], 1200.0)
        self.assertEqual(result["windows"]["30d"]["changePct"]["value"], -4.0)
        self.assertEqual(result["graderPopulations"]["PSA"]["topGradePopulation"]["status"], "ready")
        self.assertTrue(
            all(
                result["graderPopulations"][grader]["topGradePopulation"]["status"] == "unavailable"
                for grader in ("BGS", "CGC", "SGC", "TAG")
            )
        )

    def test_metric_as_of_uses_the_real_observation_not_the_snapshot_stamp(self) -> None:
        """asOf 唔可以係 snapshot 自己嘅 effectiveAt。

        stamp 咗自己個時間，`validate.ts` 嗰個 48h 新鮮度閘算出嚟嘅 age 由構造上
        永遠係 0，爬蟲死幾耐都唔會響。呢個 test 就係鎖死呢個回歸。
        """
        card = self.presentation_card()
        effective_at = "2026-07-24T00:00:00Z"
        result = snapshot.card_from_row(
            self.ranked_row(card),
            card,
            effective_at,
            {},
            self.psa_population(datetime(2026, 7, 20, 12, tzinfo=timezone.utc)),
            {},
            {7: "2026-07-23T18:00:00Z"},
        )
        self.assertEqual(result["pricePsa10"]["asOf"], "2026-07-23T18:00:00Z")
        self.assertEqual(result["populationPsa10"]["asOf"], "2026-07-20T12:00:00Z")
        # market cap = 價 × POP，跟舊嗰個輸入，唔可以扮返新
        self.assertEqual(result["marketCap"]["asOf"], "2026-07-20T12:00:00Z")
        for field in ("pricePsa10", "populationPsa10", "marketCap"):
            self.assertNotEqual(result[field]["asOf"], effective_at, f"{field} 又 stamp 返 snapshot 自己個時間")

    def test_missing_price_observation_fails_closed(self) -> None:
        card = self.presentation_card()
        with self.assertRaisesRegex(snapshot.SnapshotExportError, "no backing price observation"):
            snapshot.card_from_row(
                self.ranked_row(card),
                card,
                "2026-07-24T00:00:00Z",
                {},
                self.psa_population(datetime(2026, 7, 20, 12, tzinfo=timezone.utc)),
                {},
                {},
            )

    def test_missing_population_observation_fails_closed(self) -> None:
        card = self.presentation_card()
        with self.assertRaisesRegex(snapshot.SnapshotExportError, "no backing PSA population observation"):
            snapshot.card_from_row(
                self.ranked_row(card),
                card,
                "2026-07-24T00:00:00Z",
                {},
                {},
                {},
                {7: "2026-07-23T18:00:00Z"},
            )

    def test_stale_population_observation_fails_closed_per_grader(self) -> None:
        """168h date floor：過期 grader 觀測成個 block 歸 unavailable。

        TAG freeze 之後冇新觀測，07-29 起最先觸發——過期唔准扮新、
        唔准出負 delta，label 回退 TOP_GRADE 表。PSA 新鮮嗰邊唔受影響。
        """
        card = self.presentation_card()
        populations = self.psa_population(datetime(2026, 7, 23, 12, tzinfo=timezone.utc))
        populations[(7, "TAG")] = {
            "top_grade_label": "10",
            "total_population": 900,
            "top_grade_population": 400,
            "estimated": False,
            # 07-16 00:00 vs 快照 07-24 00:00 = 192h > 168h
            "effective_at": datetime(2026, 7, 16, tzinfo=timezone.utc),
        }
        result = snapshot.card_from_row(
            self.ranked_row(card),
            card,
            "2026-07-24T00:00:00Z",
            {},
            populations,
            {},
            {7: "2026-07-23T18:00:00Z"},
        )
        tag = result["graderPopulations"]["TAG"]
        self.assertEqual(tag["topGradePopulation"], {"value": None, "status": "unavailable", "asOf": None, "estimated": False})
        self.assertEqual(tag["total"]["status"], "unavailable")
        self.assertEqual(tag["topGrade"], "10")
        for window in ("1d", "7d", "30d"):
            self.assertEqual(tag["topGradePopulationChangePct"][window]["status"], "unavailable")
        self.assertEqual(result["graderPopulations"]["PSA"]["topGradePopulation"]["status"], "ready")

    def test_population_stale_boundary_is_exactly_168h(self) -> None:
        base = "2026-07-24T00:00:00Z"
        fresh = {"effective_at": datetime(2026, 7, 17, tzinfo=timezone.utc)}  # 168h 整，唔算過期
        stale = {"effective_at": datetime(2026, 7, 16, 23, 59, tzinfo=timezone.utc)}  # 168h+1min
        self.assertFalse(snapshot.population_is_stale(fresh, base))
        self.assertTrue(snapshot.population_is_stale(stale, base))
        # effective_at 唔係 datetime（斷鏈／髒行）一律當過期
        self.assertTrue(snapshot.population_is_stale({"effective_at": None}, base))

    def test_top_grade_literal_top_maps_to_grader_default(self) -> None:
        """DB ingest bug 令 9,862 行 label 全部係字面 'top'——快照層要回退
        TOP_GRADE 表，唔准俾 'top' 出街做 grade label。"""
        self.assertEqual(snapshot.top_grade_label({"top_grade_label": "top"}, "PSA"), "10")
        self.assertEqual(snapshot.top_grade_label({"top_grade_label": "Top"}, "BGS"), "10")
        self.assertEqual(snapshot.top_grade_label({"top_grade_label": ""}, "CGC"), "10")
        self.assertEqual(snapshot.top_grade_label(None, "TAG"), "10")
        # 將來 ingest 修好之後，真 label 原样直出
        self.assertEqual(snapshot.top_grade_label({"top_grade_label": "9.5"}, "BGS"), "9.5")

    def test_earlier_is_not_fooled_by_microsecond_precision(self) -> None:
        # 字串直接 min() 會揀錯：'.' (0x2E) 細過 'Z' (0x5A)
        self.assertEqual(
            snapshot.earlier("2026-07-24T00:00:00Z", "2026-07-24T00:00:00.123456Z"),
            "2026-07-24T00:00:00Z",
        )

    def test_ranked_card_with_unavailable_market_metric_fails_closed(self) -> None:
        card = self.presentation_card()
        with self.assertRaisesRegex(snapshot.SnapshotExportError, "unavailable"):
            snapshot.card_from_row(
                {
                    "variant_id": 7,
                    "opaque_id": card["id"],
                    "rank_position": 1,
                    "reference_price_usd": None,
                    "psa10_population": 2000,
                    "market_cap_usd": None,
                    "metric_status": "unavailable",
                },
                card,
                "2026-07-24T00:00:00Z",
                {},
                {},
                {},
                {},
            )

    def population_points(self) -> dict[date, int]:
        # 週線 GemRate 歷史 + 尾段兩日 DB 觀測，即係真實混合粒度。
        return {
            date(2026, 6, 27): 1000,
            date(2026, 7, 4): 1020,
            date(2026, 7, 11): 1040,
            date(2026, 7, 18): 1060,
            date(2026, 7, 24): 1080,
            date(2026, 7, 25): 1100,
        }

    def test_population_change_windows_fill_every_window_key(self) -> None:
        # 前端 `card.graderPopulations[grader]` 冇 optional chaining，三個 key 缺一即炸。
        for points, value, day in (
            ({}, None, None),
            ({}, 10, date(2026, 7, 25)),
            (self.population_points(), 1100, date(2026, 7, 25)),
        ):
            windows = snapshot.population_change_windows(points, value, day, "ready")
            self.assertEqual(set(windows), set(snapshot.WINDOWS))

    def test_population_change_windows_anchor_each_window(self) -> None:
        windows = snapshot.population_change_windows(
            self.population_points(), 1100, date(2026, 7, 25), "ready"
        )
        self.assertEqual(windows["1d"]["anchorAt"], "2026-07-24")
        self.assertEqual(windows["7d"]["anchorAt"], "2026-07-18")
        self.assertEqual(windows["30d"]["anchorAt"], "2026-06-27")
        self.assertAlmostEqual(windows["1d"]["value"], 1.851852, places=6)
        self.assertEqual(windows["30d"]["asOf"], "2026-07-25T00:00:00Z")

    def test_population_change_windows_never_emit_a_negative_delta(self) -> None:
        # POP 係存量指標，只升唔跌。算到負數 = 上游漏數，唔可以照顯示跌箭嘴。
        windows = snapshot.population_change_windows(
            {date(2026, 7, 24): 5000, date(2026, 7, 25): 4000}, 4000, date(2026, 7, 25), "ready"
        )
        self.assertEqual(windows["1d"]["status"], "unavailable")
        self.assertIsNone(windows["1d"]["value"])

    def test_population_change_windows_reject_a_zero_anchor(self) -> None:
        windows = snapshot.population_change_windows(
            {date(2026, 7, 24): 0, date(2026, 7, 25): 4000}, 4000, date(2026, 7, 25), "ready"
        )
        self.assertEqual(windows["1d"]["status"], "unavailable")

    def test_population_change_windows_split_accumulating_from_unavailable(self) -> None:
        # 歷史真係唔夠長 = accumulating；夠長但嗰日冇觀測 = unavailable。
        short = snapshot.population_change_windows(
            {date(2026, 7, 24): 900, date(2026, 7, 25): 1000}, 1000, date(2026, 7, 25), "ready"
        )
        self.assertEqual(short["30d"]["status"], "accumulating")
        gapped = snapshot.population_change_windows(
            {date(2026, 6, 1): 900, date(2026, 7, 25): 1000}, 1000, date(2026, 7, 25), "ready"
        )
        self.assertEqual(gapped["7d"]["status"], "unavailable")

    def test_population_change_windows_refuse_a_far_anchor_as_a_near_window(self) -> None:
        # 3 日前嘅點唔可以扮 1 日變動：容差同 derive_price_windows 對齊。
        windows = snapshot.population_change_windows(
            {date(2026, 7, 21): 1000, date(2026, 7, 24): 1080}, 1080, date(2026, 7, 24), "ready"
        )
        self.assertIsNone(windows["1d"]["value"])

    def test_population_change_windows_never_anchor_on_the_current_day(self) -> None:
        # 單點觀測唔可以自己同自己比，砌返個 ready 0%。
        windows = snapshot.population_change_windows(
            {date(2026, 7, 25): 1000}, 1000, date(2026, 7, 25), "ready"
        )
        self.assertIsNone(windows["1d"]["value"])

    def test_population_change_windows_follow_a_stale_population(self) -> None:
        windows = snapshot.population_change_windows(
            self.population_points(), 1100, date(2026, 7, 25), "stale"
        )
        self.assertEqual(windows["1d"]["status"], "stale")

    # --- 市值 delta / 成交 delta（斷點 #5 · #6）---------------------------------
    # 市值 = 價 × POP，所以市值變動 = (1+Δ價)(1+ΔPOP)−1，**唔係** Δ價。
    # 舊版 `rankings.tsx:154` 直接攞 `changePct`（純價格）頂替，POP 只升唔跌
    # 所以幅度永遠低估，價跌而 POP 升得多過嗰陣仲會由負變正 —— 箭嘴指錯方向。

    def test_compose_change_pct_multiplies_instead_of_adding(self) -> None:
        composed = snapshot.compose_change_pct(
            snapshot.metric(-5.0, "ready", "2026-07-25T00:00:00Z"),
            snapshot.metric(3.0, "ready", "2026-07-25T00:00:00Z"),
        )
        # (0.95 × 1.03 − 1) × 100 = −2.15，唔係 −5 + 3 = −2
        self.assertAlmostEqual(composed["value"], -2.15, places=9)
        self.assertEqual(composed["status"], "ready")

    def test_compose_change_pct_flips_the_sign_when_population_outruns_a_price_drop(self) -> None:
        composed = snapshot.compose_change_pct(
            snapshot.metric(-0.08, "ready", "2026-07-25T00:00:00Z"),
            snapshot.metric(2.66, "ready", "2026-07-25T00:00:00Z"),
        )
        self.assertGreater(composed["value"], 0)

    def test_compose_change_pct_fails_closed_instead_of_reusing_the_price_change(self) -> None:
        """冇 ΔPOP 就出 null。**唔准**退返去單用 Δ價頂替 —— 頂替就係原本嗰個 bug。"""
        price = snapshot.metric(-5.32, "ready", "2026-07-25T00:00:00Z")
        for factor_status in ("accumulating", "unavailable"):
            composed = snapshot.compose_change_pct(price, snapshot.metric(None, factor_status, None))
            self.assertIsNone(composed["value"])
            self.assertNotEqual(composed["value"], price["value"])
            # accumulating（等數據）同 unavailable（計唔到）唔可以撈埋
            self.assertEqual(composed["status"], factor_status)
            self.assertIsNone(composed["asOf"])

    def test_compose_change_pct_is_no_fresher_than_its_stalest_input(self) -> None:
        composed = snapshot.compose_change_pct(
            snapshot.metric(2.0, "stale", "2026-07-19T00:00:00Z"),
            snapshot.metric(3.0, "ready", "2026-07-22T00:00:00Z"),
        )
        self.assertEqual(composed["status"], "stale")
        self.assertEqual(composed["asOf"], "2026-07-19T00:00:00Z")

    def test_ratio_change_pct_refuses_to_divide_by_an_empty_previous_window(self) -> None:
        for previous in (None, 0, 0.0, -1):
            self.assertIsNone(snapshot.ratio_change_pct(1200.0, previous, "ready", "2026-07-25")["value"])
        self.assertIsNone(snapshot.ratio_change_pct(None, 800.0, "ready", "2026-07-25")["value"])
        self.assertAlmostEqual(
            snapshot.ratio_change_pct(1200.0, 800.0, "ready", "2026-07-25")["value"], 50.0, places=9
        )

    def dated_psa_population(self) -> dict[tuple[int, str], dict[str, object]]:
        # `observed_day()` 讀 `observed_date`，唔係 `effective_at` —— 冇呢條欄
        # 就搵唔到錨點，三個窗口全部空白。
        populations = self.psa_population(datetime(2026, 7, 25, 12, tzinfo=timezone.utc))
        populations[(7, "PSA")]["observed_date"] = date(2026, 7, 25)
        populations[(7, "PSA")]["top_grade_population"] = 1100
        return populations

    def test_card_from_row_composes_the_market_cap_delta_from_price_and_population(self) -> None:
        card = self.presentation_card()
        result = snapshot.card_from_row(
            self.ranked_row(card),
            card,
            "2026-07-24T00:00:00Z",
            {},
            self.dated_psa_population(),
            {},
            {7: "2026-07-25T18:00:00Z"},
            {(7, "PSA"): self.population_points()},
        )
        window = result["windows"]["30d"]
        population_change = result["graderPopulations"]["PSA"]["topGradePopulationChangePct"]["30d"]
        self.assertIsNotNone(population_change["value"])
        expected = ((1 - 4.0 / 100) * (1 + population_change["value"] / 100) - 1) * 100
        self.assertAlmostEqual(window["marketCapChangePct"]["value"], expected, places=6)
        # 唔可以再等於價格變動本身
        self.assertNotAlmostEqual(window["marketCapChangePct"]["value"], -4.0, places=6)

    def test_card_from_row_blanks_the_market_cap_delta_when_population_history_is_missing(self) -> None:
        """冇 POP 史 → 市值 delta 空白，**唔係**退返去顯示價格變動。"""
        card = self.presentation_card()
        result = snapshot.card_from_row(
            self.ranked_row(card), card, "2026-07-24T00:00:00Z", {},
            self.psa_population(datetime(2026, 7, 25, 12, tzinfo=timezone.utc)), {},
            {7: "2026-07-25T18:00:00Z"},
        )
        for window in ("1d", "7d", "30d"):
            self.assertIsNone(result["windows"][window]["marketCapChangePct"]["value"])
        self.assertEqual(result["windows"]["30d"]["changePct"]["value"], -4.0)

    def test_card_from_row_never_derives_a_sales_delta_from_the_price_change(self) -> None:
        """成交額同價格變動 % 冇任何數學關係。冇前期成交就空白。"""
        card = self.presentation_card()
        aggregate = {
            "sales_value_usd": 1200, "sales_count": 3, "coverage_status": "partial",
            "window_end_at": datetime(2026, 7, 24, tzinfo=timezone.utc),
            "prev_sales_value_usd": 800, "prev_sales_count": 2, "prev_days": 7,
        }
        result = snapshot.card_from_row(
            self.ranked_row(card), card, "2026-07-24T00:00:00Z",
            {(7, "7d"): aggregate, (7, "30d"): {**aggregate, "prev_sales_value_usd": 0}},
            self.psa_population(datetime(2026, 7, 25, 12, tzinfo=timezone.utc)), {},
            {7: "2026-07-25T18:00:00Z"},
        )
        self.assertAlmostEqual(result["windows"]["7d"]["trackedSalesChangePct"]["value"], 50.0, places=6)
        # 前期零成交 → 除唔到 → 空白，唔准借 change_30d_pct(-4.0) 頂替
        self.assertIsNone(result["windows"]["30d"]["trackedSalesChangePct"]["value"])
        self.assertIsNone(result["windows"]["1d"]["trackedSalesChangePct"]["value"])

    def test_latest_sales_splits_the_current_window_from_the_preceding_one(self) -> None:
        rows = [
            # 30d 當前窗口 (07-25−30, 07-25]
            {"variant_id": 7, "observed_date": date(2026, 7, 20), "sales_count": 2,
             "sales_value_usd": Decimal("300"), "coverage_status": "partial"},
            {"variant_id": 7, "observed_date": date(2026, 7, 25), "sales_count": 1,
             "sales_value_usd": Decimal("900"), "coverage_status": "partial"},
            # 前一個 30d 窗口 (07-25−60, 07-25−30]
            {"variant_id": 7, "observed_date": date(2026, 6, 20), "sales_count": 4,
             "sales_value_usd": Decimal("800"), "coverage_status": "partial"},
        ]
        aggregates = snapshot.latest_sales(FakeConnection(rows), [7], date(2026, 7, 25))
        window = aggregates[(7, "30d")]
        self.assertEqual(window["sales_value_usd"], 1200.0)
        self.assertEqual(window["prev_sales_value_usd"], 800.0)
        # window_end_at 跟窗口內最新嗰日，唔係最舊
        self.assertEqual(window["window_end_at"], date(2026, 7, 25))
        # 1d 窗口只食 07-25 嗰日，前期窗口係 07-24 —— 冇成交
        self.assertEqual(aggregates[(7, "1d")]["sales_value_usd"], 900.0)
        self.assertEqual(aggregates[(7, "1d")]["prev_sales_value_usd"], 0.0)

    def test_latest_sales_drops_a_card_that_only_traded_in_the_previous_window(self) -> None:
        """淨係前期有成交 = 當前窗口冇成交。唔可以令 trackedSales 由 unavailable 變 ready/0。"""
        rows = [{"variant_id": 7, "observed_date": date(2026, 6, 20), "sales_count": 4,
                 "sales_value_usd": Decimal("800"), "coverage_status": "partial"}]
        aggregates = snapshot.latest_sales(FakeConnection(rows), [7], date(2026, 7, 25))
        self.assertNotIn((7, "30d"), aggregates)

    def test_atomic_json_replaces_complete_document(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "snapshot.json"
            snapshot.atomic_json(output, {"generation": "canonical"})
            self.assertEqual(json.loads(output.read_text(encoding="utf-8")), {"generation": "canonical"})
            self.assertEqual(list(output.parent.glob("*.tmp")), [])


class JsSafeNumbersTests(unittest.TestCase):
    """contentSha256 契約：Python 序列化文字必須 == JS parse 完 restringify 嘅文字。"""

    def test_integral_floats_serialize_as_ints(self) -> None:
        self.assertEqual(
            snapshot.stable_json({"a": 0.0, "b": [1.0, -50.0], "c": 0.1}),
            b'{"a":0,"b":[1,-50],"c":0.1}',
        )

    def test_atomic_json_uses_same_normalization(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "snapshot.json"
            snapshot.atomic_json(output, {"value": 25.0})
            self.assertEqual(output.read_text(encoding="utf-8"), '{"value":25}\n')

    def test_exponent_notation_fails_closed(self) -> None:
        with self.assertRaises(snapshot.SnapshotExportError):
            snapshot.stable_json({"a": 1e-05})


if __name__ == "__main__":
    unittest.main()
