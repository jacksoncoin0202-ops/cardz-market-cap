from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from datetime import datetime, timezone
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

    def test_database_metrics_replace_presentation_pack_metrics(self) -> None:
        card = self.presentation_card()
        row = {
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
        result = snapshot.card_from_row(
            row,
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
            {},
            {},
        )
        self.assertEqual(result["pricePsa10"]["value"], 100.0)
        self.assertEqual(result["populationPsa10"]["value"], 2000)
        self.assertEqual(result["marketCap"]["value"], 200000.0)
        self.assertEqual(result["windows"]["1d"]["changePct"], {"value": None, "status": "accumulating", "asOf": None})
        self.assertEqual(result["windows"]["7d"]["trackedSales"]["valueUsd"]["value"], 1200.0)
        self.assertEqual(result["windows"]["30d"]["changePct"]["value"], -4.0)
        self.assertTrue(all(value["topGradePopulation"]["status"] == "unavailable" for value in result["graderPopulations"].values()))

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
            )

    def test_atomic_json_replaces_complete_document(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "snapshot.json"
            snapshot.atomic_json(output, {"generation": "canonical"})
            self.assertEqual(json.loads(output.read_text(encoding="utf-8")), {"generation": "canonical"})
            self.assertEqual(list(output.parent.glob("*.tmp")), [])


if __name__ == "__main__":
    unittest.main()
