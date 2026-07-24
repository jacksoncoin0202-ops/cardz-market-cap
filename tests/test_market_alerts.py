from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

from market_alerts import (  # noqa: E402
    CandidateSnapshot,
    POPULATION_SOURCE_PRIORITY,
    alert_signals,
    discovery_state,
    evaluation_input_hash,
    percent_change,
    pick_observation,
    tracked_indexes,
)


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
