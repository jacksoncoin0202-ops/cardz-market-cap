from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

import market_alerts  # noqa: E402
from market_alerts import (  # noqa: E402
    CandidateSnapshot,
    POPULATION_SOURCE_PRIORITY,
    PRICE_SOURCE_PRIORITY,
    alert_signals,
    discovery_state,
    evaluate,
    evaluation_input_hash,
    mark_evaluation_passed,
    load_qc_report_variants,
    price_sales_eligible_variant_ids,
    percent_change,
    pick_observation,
    pick_rank_price,
    previous_snapshots,
    tracked_indexes,
)
from ranking_derivation import market_cap_usd  # noqa: E402


def candidate(**overrides: object) -> CandidateSnapshot:
    values: dict[str, object] = {
        "variant_id": 1,
        "candidate_key": "cmc_fixture",
        "member_role": "watchlist",
        "shadow_rank": 120,
        "eligible_rank": 120,
        "reference_price_usd": 100.0,
        "psa10_population": 1200,
        "market_cap_usd": 120000.0,
        "cutoff_ratio": 0.85,
        "projected_pop1000_ratio": 0.70,
        "change_1d_pct": 12.0,
        "change_7d_pct": 22.0,
        "change_30d_pct": 30.0,
        "population_change_7d_pct": None,
        "population_change_30d_pct": None,
        "metric_status": "ready",
    }
    values.update(overrides)
    return CandidateSnapshot(**values)  # type: ignore[arg-type]


class MarketAlertPolicyTests(unittest.TestCase):
    def test_liquidity_query_requires_ten_exact_single_psa10_sales(self) -> None:
        cursor = Mock()
        cursor.fetchall.return_value = [{"variant_id": 7}]

        self.assertEqual(market_alerts.variants_with_sale_30d(cursor), {7})
        sql = cursor.execute.call_args.args[0]
        self.assertIn("HAVING COUNT(*) >= 10", sql)
        self.assertIn("sale.quantity=1", sql)
        self.assertIn("identity.match_status='exact'", sql)
        self.assertIn("sale.source_code='ebay'", sql)

    def test_staging_main_does_not_fall_through_to_active_evaluation(self) -> None:
        connection = Mock()
        connection.close = Mock()
        candidate_sha = "a" * 64
        result = {
            "status": "evaluated",
            "candidates": 1,
            "coverageStatus": "blocked",
        }
        argv = [
            "market_alerts.py",
            "--effective-date",
            "2026-07-30",
            "--staging-universe-lock-sha256",
            "b" * 64,
            "--universe-qc-report",
            "report.json",
            "--expected-universe-candidate-sha256",
            candidate_sha,
        ]
        with (
            patch.object(sys, "argv", argv),
            patch("db_runtime.connection_from_args", return_value=connection),
            patch.object(
                market_alerts,
                "load_staging_cohort",
                return_value=(26, [7], candidate_sha),
            ),
            patch.object(
                market_alerts,
                "discovery_state",
                return_value=("c" * 64, "blocked", 0),
            ),
            patch.object(market_alerts, "evaluate", return_value=result) as evaluate_mock,
            patch("builtins.print"),
        ):
            self.assertEqual(market_alerts.main(), 0)
        evaluate_mock.assert_called_once()
        self.assertEqual(evaluate_mock.call_args.kwargs["variant_ids"], [7])
        self.assertEqual(evaluate_mock.call_args.kwargs["universe_lock_id"], 26)
        self.assertTrue(evaluate_mock.call_args.kwargs["staging"])

    def test_qc_report_price_sales_only_filters_before_dry_run_ranking(self) -> None:
        connection = Mock()
        connection.close = Mock()
        candidate_sha = "a" * 64
        argv = [
            "market_alerts.py",
            "--effective-date",
            "2026-07-31",
            "--dry-run",
            "--universe-qc-report",
            "report.json",
            "--expected-universe-candidate-sha256",
            candidate_sha,
            "--price-sales-only",
        ]
        with (
            patch.object(sys, "argv", argv),
            patch("db_runtime.connection_from_args", return_value=connection),
            patch.object(
                market_alerts,
                "load_qc_report_variants",
                return_value=([7, 8, 9], candidate_sha),
            ),
            patch.object(
                market_alerts,
                "price_sales_eligible_variant_ids",
                return_value=[7, 9],
            ),
            patch.object(
                market_alerts,
                "dry_run_evaluation",
                return_value={"status": "dry-run"},
            ) as dry_run,
            patch("builtins.print") as print_mock,
        ):
            self.assertEqual(market_alerts.main(), 0)

        self.assertEqual(dry_run.call_args.kwargs["variant_ids"], [7, 9])
        self.assertEqual(
            dry_run.call_args.kwargs["universe_candidate_sha256"],
            candidate_sha,
        )
        printed = json.loads(print_mock.call_args.args[0])
        self.assertEqual(printed["priceSalesEligibleVariantIds"], [7, 9])

    def test_qc_report_cohort_requires_matching_receipt_and_explicit_hash(self) -> None:
        candidate_sha = "a" * 64
        with tempfile.TemporaryDirectory() as temporary:
            report_path = Path(temporary) / "report.json"
            report = {
                "universe": {"schemaVersion": "qc-discovery-v1", "candidateSha256": candidate_sha, "qualified": 2},
                "cards": [{"variantId": 9}, {"variantId": 3}],
            }
            payload = json.dumps(report, sort_keys=True).encode("utf-8")
            report_path.write_bytes(payload)
            (report_path.with_name("receipt.json")).write_text(
                json.dumps({"universeCandidateSha256": candidate_sha, "reportSha256": hashlib.sha256(payload).hexdigest()}),
                encoding="utf-8",
            )
            self.assertEqual(load_qc_report_variants(report_path, candidate_sha), ([3, 9], candidate_sha))
            with self.assertRaisesRegex(RuntimeError, "does not match"):
                load_qc_report_variants(report_path, "b" * 64)

    def test_price_sales_only_uses_qc_price_and_sales_gates(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            report_path = Path(temporary) / "report.json"
            report_path.write_text(
                json.dumps(
                    {
                        "cards": [
                            {
                                "variantId": 7,
                                "blockers": ["image_not_human_or_vision_confirmed"],
                                "facts": {
                                    "price": {
                                        "priorityMeta": {
                                            "crossSourceQc": {"status": "confirmed"}
                                        }
                                    },
                                    "sales30d": {"purePsa10Count": 10},
                                },
                            },
                            {
                                "variantId": 8,
                                "blockers": [],
                                "facts": {
                                    "price": {
                                        "priorityMeta": {
                                            "crossSourceQc": {
                                                "status": "exact_psa10_price_source_spread_gt_2x"
                                            }
                                        }
                                    },
                                    "sales30d": {"purePsa10Count": 20},
                                },
                            },
                            {
                                "variantId": 9,
                                "blockers": ["sale_source_identity_not_exact"],
                                "facts": {
                                    "price": {
                                        "priorityMeta": {
                                            "crossSourceQc": {"status": "confirmed"}
                                        }
                                    },
                                    "sales30d": {"purePsa10Count": 20},
                                },
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )
            self.assertEqual(price_sales_eligible_variant_ids(report_path), [7])

    def test_revision_migration_binds_each_index_snapshot_to_one_evaluation(self) -> None:
        document = (
            ROOT / "pipelines/migrations/012_index_evaluation_revisions.mysql.sql"
        ).read_text(encoding="utf-8")
        self.assertIn("evaluation_id BIGINT UNSIGNED NULL", document)
        self.assertIn("uq_market_index_snapshot_evaluation", document)
        self.assertIn("FOREIGN KEY (evaluation_id) REFERENCES market_alert_evaluation(id)", document)
        self.assertIn("publish_gate_status VARCHAR(16) NOT NULL DEFAULT ''pending''", document)
        self.assertIn("publish_gate_passed_at DATETIME(6) NULL", document)

    def test_pending_reused_evaluation_can_be_marked_passed(self) -> None:
        class Cursor:
            rowcount = 0
            selected: dict[str, object] | None = None
            queries: list[str] = []

            def __enter__(self) -> "Cursor":
                return self

            def __exit__(self, *_: object) -> bool:
                return False

            def execute(self, query: str, _args: object = ()) -> None:
                compact = " ".join(query.split())
                self.queries.append(compact)
                if compact.startswith("SELECT e.id"):
                    self.selected = {
                        "id": 77,
                        "coverage_status": "observed",
                        "publish_gate_status": "pending",
                        "combined_count": 300,
                        "pokemon_count": 100,
                        "one_piece_count": 100,
                    }
                elif compact.startswith("UPDATE market_alert_evaluation"):
                    self.rowcount = 1

            def fetchone(self) -> dict[str, object] | None:
                return self.selected

        class Connection:
            cursor_value = Cursor()
            commits = 0
            rollbacks = 0

            def cursor(self) -> Cursor:
                return self.cursor_value

            def commit(self) -> None:
                self.commits += 1

            def rollback(self) -> None:
                self.rollbacks += 1

        connection = Connection()
        report = mark_evaluation_passed(connection, 77)
        self.assertEqual(report["publishGateStatus"], "passed")
        self.assertTrue(report["updated"])
        self.assertEqual(connection.commits, 1)
        self.assertEqual(connection.rollbacks, 0)
        self.assertTrue(
            any("SET publish_gate_status='passed'" in query for query in connection.cursor_value.queries)
        )

    def test_mark_gate_rejects_blocked_or_underfilled_evaluation(self) -> None:
        class Cursor:
            def __init__(self, selected: dict[str, object]) -> None:
                self.selected = selected
                self.updated = False

            def __enter__(self) -> "Cursor":
                return self

            def __exit__(self, *_: object) -> bool:
                return False

            def execute(self, query: str, _args: object = ()) -> None:
                if "UPDATE market_alert_evaluation" in query:
                    self.updated = True

            def fetchone(self) -> dict[str, object]:
                return self.selected

        class Connection:
            def __init__(self, selected: dict[str, object]) -> None:
                self.cursor_value = Cursor(selected)
                self.rollbacks = 0

            def cursor(self) -> Cursor:
                return self.cursor_value

            def commit(self) -> None:
                raise AssertionError("a rejected gate must not commit")

            def rollback(self) -> None:
                self.rollbacks += 1

        cases = (
            (
                {
                    "id": 1,
                    "coverage_status": "blocked",
                    "publish_gate_status": "pending",
                    "combined_count": 300,
                    "pokemon_count": 100,
                    "one_piece_count": 100,
                },
                "blocked coverage",
            ),
            (
                {
                    "id": 2,
                    "coverage_status": "observed",
                    "publish_gate_status": "pending",
                    "combined_count": 299,
                    "pokemon_count": 100,
                    "one_piece_count": 99,
                },
                "top300_boards counts",
            ),
        )
        for selected, message in cases:
            with self.subTest(message=message):
                connection = Connection(selected)
                with self.assertRaisesRegex(RuntimeError, message):
                    mark_evaluation_passed(connection, int(selected["id"]))
                self.assertEqual(connection.rollbacks, 1)
                self.assertFalse(connection.cursor_value.updated)

    def test_exact_reuse_materializes_missing_evaluation_bound_indexes(self) -> None:
        snapshots = [
            candidate(variant_id=1, candidate_key="pokemon"),
            candidate(variant_id=2, candidate_key="one-piece"),
        ]

        class Cursor:
            def __init__(self) -> None:
                self.selected: dict[str, object] | None = None
                self.rows: list[dict[str, object]] = []
                self.lastrowid = 100
                self.index_inserts: list[tuple[object, ...]] = []

            def __enter__(self) -> "Cursor":
                return self

            def __exit__(self, *_: object) -> bool:
                return False

            def execute(self, query: str, args: tuple[object, ...] = ()) -> None:
                compact = " ".join(query.split())
                self.selected = None
                self.rows = []
                if compact.startswith("SELECT GET_LOCK"):
                    self.selected = {"acquired": 1}
                elif compact.startswith("SELECT id, input_sha256"):
                    self.selected = {
                        "id": 77,
                        "input_sha256": "a" * 64,
                        "coverage_status": "observed",
                        "publish_gate_status": "pending",
                        "eligible_count": 300,
                    }
                elif compact.startswith("SELECT id FROM market_ingest_run"):
                    self.selected = {"id": 9}
                elif compact.startswith("SELECT id,tcg_code"):
                    self.rows = [
                        {"id": 1, "tcg_code": "pokemon"},
                        {"id": 2, "tcg_code": "one-piece"},
                    ]
                elif compact.startswith("SELECT id FROM market_index_snapshot"):
                    self.selected = None
                elif compact.startswith("INSERT INTO market_index_snapshot"):
                    self.lastrowid += 1
                    self.index_inserts.append(args)

            def fetchone(self) -> dict[str, object] | None:
                return self.selected

            def fetchall(self) -> list[dict[str, object]]:
                return self.rows

        class Connection:
            cursor_value = Cursor()
            commits = 0
            rollbacks = 0

            def cursor(self) -> Cursor:
                return self.cursor_value

            def commit(self) -> None:
                self.commits += 1

            def rollback(self) -> None:
                self.rollbacks += 1

        original_load = market_alerts.load_candidates
        original_previous = market_alerts.previous_snapshots
        market_alerts.load_candidates = lambda *_args: (5, snapshots)
        market_alerts.previous_snapshots = lambda *_args: {}
        self.addCleanup(setattr, market_alerts, "load_candidates", original_load)
        self.addCleanup(setattr, market_alerts, "previous_snapshots", original_previous)

        connection = Connection()
        report = evaluate(
            connection,
            date(2026, 7, 28),
            discovery_sha256="b" * 64,
            coverage_status="observed",
            unresolved_high_potential_count=0,
        )
        self.assertEqual(report["status"], "reused")
        self.assertEqual(report["indexRevisionsCreated"], 3)
        self.assertEqual(len(connection.cursor_value.index_inserts), 3)
        self.assertTrue(
            all(insert_args[1] == 77 for insert_args in connection.cursor_value.index_inserts)
        )

    def test_previous_snapshot_query_selects_the_latest_prior_revision(self) -> None:
        class Cursor:
            query = ""

            def __enter__(self) -> "Cursor":
                return self

            def __exit__(self, *_: object) -> bool:
                return False

            def execute(self, query: str, _args: object) -> None:
                self.query = query

            def fetchall(self) -> list[dict[str, object]]:
                return []

        class Connection:
            cursor_value = Cursor()

            def cursor(self) -> Cursor:
                return self.cursor_value

        connection = Connection()
        self.assertEqual(previous_snapshots(connection, date(2026, 7, 28)), {})
        compact = " ".join(connection.cursor_value.query.split())
        self.assertIn("s.evaluation_id = (", compact)
        self.assertIn("ORDER BY e2.effective_date DESC, e2.id DESC", compact)

    def test_top100_entry_is_critical_without_duplicate_near_signal(self) -> None:
        previous = candidate(shadow_rank=105, eligible_rank=105)
        current = candidate(shadow_rank=99, eligible_rank=99, cutoff_ratio=1.1)
        signals = alert_signals(current, previous)
        self.assertEqual([signal.alert_type for signal in signals], ["entered_top100"])
        self.assertEqual(signals[0].severity, "critical")
        self.assertTrue(signals[0].immediate)

    def test_initial_baseline_does_not_alert_every_top100_card(self) -> None:
        current = candidate(shadow_rank=50, eligible_rank=50, cutoff_ratio=2.0)
        self.assertEqual(alert_signals(current, None), [])

    def test_near_cutoff_momentum_requires_two_hits_unless_extreme(self) -> None:
        previous = candidate(shadow_rank=125, eligible_rank=125)
        normal = alert_signals(candidate(shadow_rank=115, eligible_rank=115), previous)
        self.assertEqual([signal.alert_type for signal in normal], ["near_top100"])
        self.assertFalse(normal[0].immediate)
        extreme = alert_signals(candidate(change_1d_pct=30.0), previous)
        self.assertTrue(extreme[0].immediate)

    def test_pre1000_breakout_uses_population_and_projected_cap(self) -> None:
        current = candidate(
            member_role="monitoring",
            eligible_rank=None,
            psa10_population=999,
            market_cap_usd=99900.0,
            cutoff_ratio=0.70,
            projected_pop1000_ratio=0.90,
            population_change_7d_pct=12.0,
        )
        signals = alert_signals(current, candidate(member_role="monitoring", psa10_population=998))
        self.assertIn("pre1000_breakout", [signal.alert_type for signal in signals])

    def test_discovery_only_population_never_opens_pre_entry_alert(self) -> None:
        current = candidate(
            member_role="monitoring",
            eligible_rank=None,
            psa10_population=970,
            market_cap_usd=97000.0,
            projected_pop1000_ratio=0.95,
            population_change_7d_pct=20.0,
        )
        self.assertNotIn("pre1000_breakout", [signal.alert_type for signal in alert_signals(current, None)])

    def test_population_current_fact_is_stale_after_48_hours(self) -> None:
        rows = [
            {"id": 1, "source_code": "gemrate", "observed_date": date(2026, 7, 21), "top_grade_population": 1000, "estimated": 0},
        ]
        self.assertIsNone(
            pick_observation(
                rows,
                date(2026, 7, 24),
                POPULATION_SOURCE_PRIORITY,
                max_gap_days=2,
                value_key="top_grade_population",
            )
        )

    def test_rank_price_rejects_untrusted_tpl_and_keeps_exact_ebay(self) -> None:
        """Market cap binds exact real-market legs, never the removed TPL source."""
        rows = [
            {
                "id": 10,
                "source_code": "ebay",
                "observed_date": date(2026, 7, 26),
                "effective_at": datetime(2026, 7, 27, 21, 0, 0),
                "price_usd": 4305.0,
                "source_priority": 100,
            },
            {
                "id": 20,
                "source_code": "tcgpricelookup",
                "observed_date": date(2026, 6, 23),
                "effective_at": datetime(2026, 7, 28, 22, 31, 46),
                "price_usd": 4472.345,
                "source_priority": 80,
            },
        ]
        selected = pick_rank_price(
            rows,
            date(2026, 7, 29),
            PRICE_SOURCE_PRIORITY,
            max_gap_days=90,
        )
        self.assertIsNotNone(selected)
        assert selected is not None
        self.assertEqual(selected["source_code"], "ebay")
        self.assertEqual(float(selected["price_usd"]), 4305.0)
        self.assertEqual(market_cap_usd(4305.0, 21308), round(4305.0 * 21308, 2))

    def test_rank_price_averages_fresh_exact_authority_families(self) -> None:
        rows = [
            {"id": 1, "source_code": "snk_psa10", "observed_date": date(2026, 7, 30), "effective_at": datetime(2026, 7, 30), "price_usd": 100.0, "source_priority": 10},
            {"id": 2, "source_code": "ebay", "observed_date": date(2026, 7, 30), "effective_at": datetime(2026, 7, 30), "price_usd": 110.0, "source_priority": 40},
            {"id": 3, "source_code": "pricecharting", "observed_date": date(2026, 7, 30), "effective_at": datetime(2026, 7, 30), "price_usd": 120.0, "source_priority": 5},
        ]
        selected = pick_rank_price(
            rows, date(2026, 7, 31), PRICE_SOURCE_PRIORITY, max_gap_days=90,
            identities=[
                {"source_code": "snkrdunk", "match_status": "exact"},
                {"source_code": "ebay", "match_status": "exact"},
                {"source_code": "pricecharting", "match_status": "exact"},
            ],
        )
        self.assertIsNotNone(selected)
        assert selected is not None
        self.assertEqual(selected["source_code"], "pricecharting")
        self.assertEqual(float(selected["price_usd"]), 110.0)

    def test_rank_price_rejects_authority_spread_over_two_times(self) -> None:
        rows = [
            {"id": 1, "source_code": "snk_psa10", "observed_date": date(2026, 7, 30), "effective_at": datetime(2026, 7, 30), "price_usd": 100.0, "source_priority": 10},
            {"id": 2, "source_code": "pricecharting", "observed_date": date(2026, 7, 30), "effective_at": datetime(2026, 7, 30), "price_usd": 250.0, "source_priority": 5},
        ]
        self.assertIsNone(
            pick_rank_price(
                rows,
                date(2026, 7, 31),
                PRICE_SOURCE_PRIORITY,
                max_gap_days=90,
                identities=[
                    {"source_code": "snkrdunk", "match_status": "exact"},
                    {"source_code": "pricecharting", "match_status": "exact"},
                ],
            )
        )

    def test_rank_price_accepts_pricecharting_bound_ebay_median(self) -> None:
        selected = pick_rank_price(
            [
                {
                    "id": 1,
                    "source_code": "ebay",
                    "observed_date": date(2026, 7, 30),
                    "effective_at": datetime(2026, 7, 30),
                    "price_usd": 105.0,
                    "source_priority": 40,
                }
            ],
            date(2026, 7, 31),
            PRICE_SOURCE_PRIORITY,
            max_gap_days=90,
            identities=[
                {"source_code": "pricecharting", "match_status": "exact"}
            ],
        )
        self.assertIsNotNone(selected)
        assert selected is not None
        self.assertEqual(float(selected["price_usd"]), 105.0)

    def test_missing_or_stale_data_never_becomes_zero_or_signal(self) -> None:
        missing = candidate(
            reference_price_usd=None,
            market_cap_usd=None,
            change_1d_pct=None,
            change_7d_pct=None,
            metric_status="accumulating",
        )
        self.assertIsNone(percent_change(100, None))
        self.assertEqual(alert_signals(missing, None), [])

    def test_population_resolver_prefers_fresh_exact_source(self) -> None:
        rows = [
            {"id": 1, "source_code": "g10", "observed_date": date(2026, 7, 22), "top_grade_population": 1000, "estimated": 0},
            {"id": 2, "source_code": "gemrate", "observed_date": date(2026, 7, 22), "top_grade_population": 1010, "estimated": 0},
        ]
        selected = pick_observation(
            rows,
            date(2026, 7, 22),
            POPULATION_SOURCE_PRIORITY,
            max_gap_days=7,
            value_key="top_grade_population",
        )
        self.assertIsNotNone(selected)
        self.assertEqual(selected["source_code"], "gemrate")

    def test_evaluation_hash_binds_discovery_evidence(self) -> None:
        snapshots = [candidate()]
        first = evaluation_input_hash(
            snapshots,
            discovery_sha256="1" * 64,
            coverage_status="observed",
            unresolved_high_potential_count=0,
        )
        changed = evaluation_input_hash(
            snapshots,
            discovery_sha256="2" * 64,
            coverage_status="blocked",
            unresolved_high_potential_count=1,
        )
        self.assertNotEqual(first, changed)

    def test_missing_discovery_manifest_is_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            digest, status, unresolved = discovery_state(Path(temporary) / "missing.json", 48)
        self.assertEqual(digest, "0" * 64)
        self.assertEqual(status, "blocked")
        self.assertEqual(unresolved, 0)

    def test_tracked_indexes_are_independent_and_keep_complete_eligible_rankings(self) -> None:
        pokemon = [candidate(variant_id=index, market_cap_usd=1000000 - index) for index in range(1, 361)]
        one_piece = [candidate(variant_id=index, market_cap_usd=2000000 - index) for index in range(361, 721)]
        tcg = {row.variant_id: "pokemon" for row in pokemon}
        tcg.update({row.variant_id: "one-piece" for row in one_piece})

        indexes = tracked_indexes([*pokemon, *one_piece], tcg)

        self.assertEqual({key: len(rows) for key, rows in indexes.items()}, {
            "tcg-combined": 720,
            "pokemon": 360,
            "one-piece": 360,
        })
        self.assertEqual(indexes["tcg-combined"][0].variant_id, 361)


if __name__ == "__main__":
    unittest.main()
