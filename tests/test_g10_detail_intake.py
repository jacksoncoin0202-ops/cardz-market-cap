from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "pipelines" / "g10_ingest.py"
SPEC = importlib.util.spec_from_file_location("cardz_g10_ingest_detail", MODULE_PATH)
assert SPEC and SPEC.loader
ingest = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = ingest
SPEC.loader.exec_module(ingest)


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


class Grade10DetailIntakeTests(unittest.TestCase):
    def test_detail_intake_emits_normalized_identity_metadata_populations_story_and_exact_sales(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            write_json(
                root / "index" / "ptcg" / "constituents.json",
                {
                    "rows": [
                        {
                            "url": "https://private.invalid/card/snkrdunk/123",
                            "name": "Pikachu V #001/024",
                            "setName": "Start Deck 100",
                            "lang": "JP",
                            "priceUsd": 100,
                        }
                    ]
                },
            )
            card = root / "cards" / "snkrdunk" / "123"
            write_json(
                card / "asset_info.json",
                {
                    "cardName": "Pikachu V",
                    "cardId": "001/024",
                    "setName": "Start Deck 100",
                    "language": "jp",
                    "year": "2022",
                    "image": "https://private.invalid/card.png",
                },
            )
            write_json(
                card / "populations.json",
                {
                    "population": [
                        {"gradeName": "PSA", "total": 1500, "topGrade": 1200},
                        {"gradeName": "CGC", "total": 20, "topGrade": 10},
                    ]
                },
            )
            write_json(card / "summary_en.json", {"summary": "Verified story."})
            write_json(
                card / "ebay_PSA_10.json",
                {
                    "saleHistory": [
                        {"date": "2026-07-20", "grade": "PSA 10", "price": 95, "currency": "usd"},
                        {"date": "1 day ago", "grade": "PSA 10", "price": 96, "currency": "usd"},
                    ]
                },
            )

            observations, quarantined = ingest.build_detail_observations(
                root,
                datetime(2026, 7, 22, tzinfo=timezone.utc),
            )
            daily, daily_quarantined = ingest.build_daily_observations(
                root,
                datetime(2026, 7, 22, tzinfo=timezone.utc),
                datetime(2026, 7, 22, tzinfo=timezone.utc),
            )

        by_kind = {row["observationKind"]: row for row in observations if row["observationKind"] != "grader_population_psa"}
        identity = by_kind["identity_candidate"]
        self.assertEqual(identity["payload"]["tcg"], "pokemon")
        self.assertEqual(identity["payload"]["language"], "ja")
        self.assertEqual(identity["payload"]["collectorNumber"], "001/024")
        self.assertEqual(identity["payload"]["edition"], None)
        self.assertEqual(identity["payload"]["parallel"], None)
        self.assertEqual(identity["payload"]["finish"], None)
        self.assertEqual(identity["payload"]["identityStatus"], "candidate")
        self.assertEqual(by_kind["story_pointer"]["payload"]["locale"], "en")
        self.assertEqual(by_kind["story_pointer"]["payload"]["sourcePath"], "cards/snkrdunk/123/summary_en.json")
        self.assertEqual(by_kind["image_metadata"]["payload"]["imageKind"], "unverified_remote")
        sales = [row for row in observations if row["observationKind"] == "sale_observation_psa10"]
        self.assertEqual(len(sales), 1)
        self.assertEqual(sales[0]["payload"]["soldAt"], "2026-07-20T00:00:00Z")
        self.assertTrue(any(item["reason"] == "relative_sale_date" for item in quarantined))
        self.assertIn("identity_candidate", {row["observationKind"] for row in daily})
        self.assertIn("grader_population_psa", {row["observationKind"] for row in daily})
        self.assertTrue(any(item["reason"] == "relative_sale_date" for item in daily_quarantined))

    def test_detail_intake_quarantines_missing_asset_identity_and_never_completes_short_number(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            write_json(
                root / "index" / "opcg" / "constituents.json",
                {
                    "rows": [
                        {
                            "url": "https://private.invalid/card/altxyz/opaque-1",
                            "name": "Monkey D. Luffy #10",
                            "priceUsd": 100,
                        }
                    ]
                },
            )
            card = root / "cards" / "altxyz" / "opaque-1"
            write_json(card / "populations.json", {"population": [{"gradeName": "PSA", "total": 2, "topGrade": 1}]})
            observations, quarantined = ingest.build_detail_observations(
                root,
                datetime(2026, 7, 22, tzinfo=timezone.utc),
            )

        self.assertFalse(observations)
        self.assertEqual(quarantined[0]["reason"], "missing_asset_info")

        self.assertIsNone(ingest.complete_collector_number("085"))
        self.assertEqual(ingest.complete_collector_number("OP01-120"), "OP01-120")
        self.assertEqual(ingest.complete_collector_number("001/024"), "001/024")


if __name__ == "__main__":
    unittest.main()
