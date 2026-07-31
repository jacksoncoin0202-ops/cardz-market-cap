from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

from tcgplayer_images import rank_hits  # noqa: E402


class TcgplayerImagesTests(unittest.TestCase):
    def test_rank_hits_prefers_exact_collector_number(self) -> None:
        hits = [
            {
                "productId": 1,
                "productName": "Other Card",
                "setName": "Random",
                "number": "999",
            },
            {
                "productId": 641620,
                "productName": "Monkey.D.Luffy EB02-010",
                "setName": "Extra Booster",
                "number": "EB02-010",
            },
        ]
        ranked = rank_hits(
            hits,
            collector_number="EB02-010",
            set_name="Extra Booster",
            name="Monkey.D.Luffy",
        )
        self.assertEqual(ranked[0]["productId"], 641620)
        self.assertGreaterEqual(ranked[0]["matchScore"], 100)


if __name__ == "__main__":
    unittest.main()
