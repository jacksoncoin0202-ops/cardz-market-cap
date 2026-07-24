from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

from gemrate_candidate_discovery import build_candidates  # noqa: E402


class GemRateCandidateDiscoveryTests(unittest.TestCase):
    def test_search_population_is_never_promoted_to_psa10_population(self) -> None:
        gemrate_id = "a" * 40
        document = build_candidates(
            {
                "results": [
                    {
                        "gemrate_id": gemrate_id,
                        "name": "Nami",
                        "set_name": "One Piece OP05",
                        "card_number": "016",
                        "population_type": "Universal",
                        "total_population": 5204,
                        "gems": 4893,
                    }
                ]
            }
        )
        candidate = document["candidates"][0]
        self.assertEqual(document["populationAuthority"], "gemrate")
        self.assertEqual(candidate["populationStatus"], "api_required")
        self.assertNotIn("populationPsa10", candidate)

    def test_non_one_piece_result_is_rejected(self) -> None:
        document = build_candidates(
            {
                "results": [
                    {
                        "gemrate_id": "b" * 40,
                        "name": "Pikachu",
                        "set_name": "Pokemon Base Set",
                    }
                ]
            }
        )
        self.assertEqual(document["candidateCount"], 0)
        self.assertEqual(document["rejectedCount"], 1)


if __name__ == "__main__":
    unittest.main()
