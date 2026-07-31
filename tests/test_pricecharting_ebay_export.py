from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

from pricecharting_ebay_export import export_row, sales_rows_to_listings  # noqa: E402
from ebay_sold_data import collect_from_input, normalize_transaction  # noqa: E402


class PricechartingEbayExportTests(unittest.TestCase):
    def test_sales_rows_stamp_identity_for_normalizer(self) -> None:
        sales = {
            "label": "PSA 10",
            "count": 1,
            "rows": [
                {
                    "date": "2026-05-04",
                    "ebay_itm": "188320306747",
                    "price_usd": 30100.0,
                    "title": "PSA 10 - Pokemon Charizard Holo #4/102 Base Set Unlimited Base Set 4/102",
                    "ebay_url": "https://www.ebay.com/itm/188320306747?nordt=true",
                }
            ],
        }
        card = {
            "language": "en",
            "tcg": "pokemon",
            "collectorNumber": "4/102",
            "edition": "",
            "parallel": "",
            "finish": "",
        }
        listings = sales_rows_to_listings(sales, card=card)
        self.assertEqual(len(listings), 1)
        self.assertEqual(listings[0]["url"], "https://www.ebay.com/itm/188320306747")
        self.assertEqual(listings[0]["language"], "English")
        self.assertEqual(listings[0]["tcg"], "Pokemon")
        row = normalize_transaction(card, listings[0], fetched_at="2026-07-28T00:00:00Z")
        self.assertIsNotNone(row)
        assert row is not None
        self.assertEqual(row["unitPrice"], 30100.0)
        self.assertEqual(row["soldDate"], "2026-05-04")

    def test_export_from_saved_html_and_feed_ebay_sold(self) -> None:
        html = ROOT / "data/private/pricecharting_session/html/capture_launch.html"
        if not html.is_file():
            self.skipTest("local PriceCharting HTML fixture missing")
        exported = export_row(
            {
                "canonicalSourceCode": "snkrdunk",
                "canonicalExternalId": "charizard-fixture",
                "language": "en",
                "tcg": "pokemon",
                "collectorNumber": "4",
                "htmlPath": str(html),
                "sourceUrl": "https://www.pricecharting.com/game/pokemon-base-set/charizard-4",
            },
            fetched_at="2026-07-28T12:00:00Z",
        )
        self.assertGreater(exported["listingCount"], 0)
        cards = [
            {
                "canonicalSourceCode": "snkrdunk",
                "canonicalExternalId": "charizard-fixture",
                "pokedexId": "cmc_fixture",
                "name": "Charizard",
                "tcg": "pokemon",
                "collectorNumber": "4",
                "language": "en",
            }
        ]
        collected = collect_from_input(cards, [exported])
        self.assertEqual(len(collected), 1)
        self.assertEqual(collected[0]["status"], "available")
        self.assertGreater(len(collected[0]["transactions"]), 0)


if __name__ == "__main__":
    unittest.main()
