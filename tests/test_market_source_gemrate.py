from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

from market_source_sync import build_source_observations  # noqa: E402


class MarketSourceGemRateTests(unittest.TestCase):
    def test_keyless_normalized_current_population_reaches_canonical_observation(self) -> None:
        gemrate_id = "a" * 40
        crosswalk = {
            "cards": [
                {
                    "canonicalSourceCode": "snkrdunk",
                    "canonicalExternalId": "101",
                    "gemrateId": gemrate_id,
                    "tcg": "one-piece",
                }
            ]
        }
        with tempfile.TemporaryDirectory() as temporary:
            card_root = Path(temporary) / gemrate_id
            card_root.mkdir(parents=True)
            (card_root / "current.json").write_text(
                json.dumps(
                    {
                        "schemaVersion": "1.0.0",
                        "authority": "gemrate",
                        "transport": "public_card_details",
                        "populationPsa10": 4321,
                        "effectiveDate": "2026-07-24",
                        "fetchedAt": "2026-07-24T00:00:00Z",
                    }
                ),
                encoding="utf-8",
            )

            observations, counts = build_source_observations(
                crosswalk,
                Path(temporary),
                {},
                {},
                {},
                160,
                datetime(2026, 7, 24, tzinfo=timezone.utc),
                None,
            )

        row = next(item for item in observations if item["observationKind"] == "grader_population_psa")
        self.assertEqual(counts["gemrateCards"], 1)
        self.assertEqual(row["payload"]["topGradePopulation"], 4321)
        self.assertEqual(row["payload"]["authority"], "gemrate")
        self.assertEqual(row["payload"]["transport"], "public_card_details")

    def test_newer_keyless_current_overrides_older_direct_psa_but_keeps_other_graders(self) -> None:
        gemrate_id = "b" * 40
        crosswalk = {
            "cards": [
                {
                    "canonicalSourceCode": "snkrdunk",
                    "canonicalExternalId": "202",
                    "gemrateId": gemrate_id,
                    "tcg": "pokemon",
                }
            ]
        }
        with tempfile.TemporaryDirectory() as temporary:
            card_root = Path(temporary) / gemrate_id
            card_root.mkdir(parents=True)
            (card_root / "population.json").write_text(
                json.dumps(
                    {
                        "data": {
                            "population": {
                                "population_data": {
                                    "data_last_updated": "2026-07-22",
                                    "by_grader": {
                                        "psa": {"grades": {"psa_10": 1000}},
                                        "cgc": {"grades": {"cgc_10_perfect": 12}},
                                    },
                                }
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            (card_root / "current.json").write_text(
                json.dumps(
                    {
                        "schemaVersion": "1.0.0",
                        "authority": "gemrate",
                        "transport": "grade10_gemrate_mirror",
                        "populationPsa10": 1200,
                        "effectiveDate": "2026-07-23",
                        "fetchedAt": "2026-07-24T00:00:00Z",
                    }
                ),
                encoding="utf-8",
            )

            observations, _ = build_source_observations(
                crosswalk,
                Path(temporary),
                {},
                {},
                {},
                160,
                datetime(2026, 7, 24, tzinfo=timezone.utc),
                None,
            )

        by_kind = {item["observationKind"]: item for item in observations}
        self.assertEqual(by_kind["grader_population_psa"]["payload"]["topGradePopulation"], 1200)
        self.assertEqual(by_kind["grader_population_psa"]["payload"]["transport"], "grade10_gemrate_mirror")
        self.assertEqual(by_kind["grader_population_cgc"]["payload"]["topGradePopulation"], 12)
        self.assertEqual(by_kind["grader_population_cgc"]["payload"]["transport"], "direct_api")


if __name__ == "__main__":
    unittest.main()
