from __future__ import annotations

import importlib.util
import unittest
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "canonical_public_snapshot",
    ROOT / "pipelines" / "canonical_public_snapshot.py",
)
assert SPEC and SPEC.loader
snapshot = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(snapshot)


class ReferenceConnection:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self.rows = rows
        self.query = ""
        self.args: tuple[object, ...] = ()

    def cursor(self) -> "ReferenceConnection":
        return self

    def __enter__(self) -> "ReferenceConnection":
        return self

    def __exit__(self, *_: object) -> bool:
        return False

    def execute(self, query: str, args: tuple[object, ...] = ()) -> None:
        self.query = query
        self.args = args

    def fetchall(self) -> list[dict[str, object]]:
        return self.rows


def presentation() -> dict[str, object]:
    return {
        "id": "card-7",
        "windows": {window: {} for window in ("1d", "7d", "30d")},
        "graderPopulations": {},
        "historyDaily": [],
    }


def ranked_row() -> dict[str, object]:
    return {
        "variant_id": 7,
        "opaque_id": "card-7",
        "rank_position": 1,
        "metric_status": "ready",
        "reference_price_usd": 100.0,
        "psa10_population": 2000,
        "market_cap_usd": 200000.0,
        "change_1d_pct": None,
        "change_7d_pct": None,
        "change_30d_pct": None,
    }


class CanonicalUngradedReferenceTests(unittest.TestCase):
    def test_loader_keeps_newest_positive_reference_per_variant(self) -> None:
        connection = ReferenceConnection(
            [
                {"variant_id": 7, "price_usd": 19.5, "observed_at": datetime(2026, 7, 25, tzinfo=timezone.utc), "id": 9},
                {"variant_id": 7, "price_usd": 12.0, "observed_at": datetime(2026, 7, 24, tzinfo=timezone.utc), "id": 8},
                {"variant_id": 8, "price_usd": 4.0, "observed_at": datetime(2026, 7, 23, tzinfo=timezone.utc), "id": 7},
            ]
        )

        latest = snapshot.latest_ungraded_reference_prices(connection, [7, 8])

        self.assertEqual(latest[7]["price_usd"], 19.5)
        self.assertEqual(latest[8]["price_usd"], 4.0)
        self.assertEqual(connection.args, (7, 8))
        self.assertIn("market_ungraded_reference_price", connection.query)
        self.assertIn("price_usd > 0", connection.query)
        self.assertIn("ORDER BY observed_at DESC,id DESC", connection.query)

    def test_raw_reference_is_additive_and_never_changes_psa10_metrics(self) -> None:
        populations = {
            (7, "PSA"): {
                "top_grade_label": "10",
                "total_population": 5000,
                "top_grade_population": 2000,
                "estimated": False,
                "effective_at": datetime(2026, 7, 20, tzinfo=timezone.utc),
            }
        }
        result = snapshot.card_from_row(
            ranked_row(),
            presentation(),
            "2026-07-24T00:00:00Z",
            {},
            populations,
            {},
            {7: "2026-07-23T18:00:00Z"},
            ungraded_references={
                7: {"price_usd": 19.5, "observed_at": datetime(2026, 7, 22, tzinfo=timezone.utc)}
            },
        )

        self.assertEqual(result["priceUngradedReference"], {"value": 19.5, "status": "ready", "asOf": "2026-07-22T00:00:00Z"})
        self.assertEqual(result["pricePsa10"]["value"], 100.0)
        self.assertEqual(result["marketCap"]["value"], 200000.0)
        self.assertEqual(result["historyDaily"], [])

        unavailable = snapshot.card_from_row(
            ranked_row(), presentation(), "2026-07-24T00:00:00Z", {}, populations, {}, {7: "2026-07-23T18:00:00Z"}
        )
        self.assertEqual(unavailable["priceUngradedReference"], {"value": None, "status": "unavailable", "asOf": None})


if __name__ == "__main__":
    unittest.main()
