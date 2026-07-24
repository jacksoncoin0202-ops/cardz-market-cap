from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

from market_discovery import build_radar  # noqa: E402


class MarketDiscoveryTests(unittest.TestCase):
    def test_roster_keeps_unavailable_price_and_only_positive_momentum_is_high(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            active = root / "active.json"
            active.write_text(
                json.dumps(
                    {
                        "cards": [
                            {"canonicalSourceCode": "snkrdunk", "canonicalExternalId": "1"}
                        ]
                    }
                ),
                encoding="utf-8",
            )
            rows = {
                "ptcg": [
                    self.row("snkrdunk", "1", rank=1, price=100, change=1),
                    self.row("snkrdunk", "2", rank=131, price=50, change=25),
                    self.row("snkrdunk", "3", rank=150, price=50, change=-25),
                    self.row("ebay", "missing-price", rank=120, price=0, change=-100),
                    {"rank": 10, "priceUsd": 1, "url": ""},
                ],
                "opcg": [self.row("snkrdunk", "4", rank=200, price=40, change=5)],
            }
            for index_name, values in rows.items():
                path = source / "index" / index_name / "constituents.json"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps({"rows": values}), encoding="utf-8")

            captured = datetime(2026, 7, 23, tzinfo=timezone.utc)
            first = build_radar(source, active, captured)
            second = build_radar(source, active, captured)

            self.assertEqual(first["rosterCount"], 5)
            self.assertEqual(first["activeMatchedCount"], 1)
            self.assertEqual(first["outsideLockCount"], 4)
            self.assertEqual(first["unresolvedHighPotentialCount"], 2)
            self.assertEqual(first["unavailablePriceCount"], 1)
            self.assertEqual(first["rejectedCount"], 1)
            self.assertEqual(first["coverageStatus"], "blocked")
            self.assertEqual(first["discoverySha256"], second["discoverySha256"])
            by_id = {row["externalId"]: row for row in first["candidates"]}
            self.assertEqual(by_id["2"]["potentialStatus"], "high")
            self.assertEqual(by_id["3"]["potentialStatus"], "candidate")
            self.assertIsNone(by_id["missing-price"]["priceUsd"])
            self.assertEqual(by_id["missing-price"]["priceStatus"], "unavailable")
            self.assertEqual(by_id["missing-price"]["potentialStatus"], "high")

    def test_missing_required_index_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            active = root / "active.json"
            active.write_text('{"cards": []}', encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "discovery index is missing"):
                build_radar(root / "source", active, datetime.now(timezone.utc))

    @staticmethod
    def row(source: str, external_id: str, *, rank: int, price: float, change: float) -> dict[str, object]:
        return {
            "name": external_id,
            "rank": rank,
            "priceUsd": price,
            "change30dPct": change,
            "url": f"https://private.invalid/research/card/{source}/{external_id}",
        }


if __name__ == "__main__":
    unittest.main()
