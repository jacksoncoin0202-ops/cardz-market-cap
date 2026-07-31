from __future__ import annotations

import unittest
from pathlib import Path

from scripts import repair_mega_charizard_125 as repair


class RepairMegaCharizard125Tests(unittest.TestCase):
    def test_target_identity_is_stable_and_language_aware(self) -> None:
        self.assertEqual(
            repair.TARGET_OPAQUE_ID,
            "cmc_dcd1e3c85ae09c85de08ac6e",
        )
        self.assertEqual(
            repair.TARGET_PRINTING_SHA256,
            "be655b25f12779e634c1019cbb9680d602bb01f6962bf069c08ae71d0697c444",
        )

    def test_current_identity_row_drops_stale_rare_candy_tpl_mapping(self) -> None:
        row, changed = repair.transform_row(
            Path("qualified-940-identity.jsonl"),
            {
                "gemrateId": repair.GEMRATE_ID,
                "variantId": repair.OLD_VARIANT_ID,
                "opaqueId": "old",
                "collectorNumber": "125",
                "tplSlug": "rare-candy",
                "tcgplayerId": "654464",
                "links": {"tpl": "wrong", "tcgplayer": "wrong"},
                "lookup": {
                    "tcgpricelookup": {"key": "wrong", "link": "wrong"},
                    "tcgplayer": {"key": "654464", "link": "wrong"},
                    "cardz": {"key": "old"},
                },
            },
            variant_id=999,
            stamp="2026-07-31T00:00:00Z",
            population=28156,
            population_as_of="2026-07-28",
        )

        self.assertTrue(changed)
        self.assertEqual(row["variantId"], 999)
        self.assertEqual(row["collectorNumber"], "125/094")
        self.assertIsNone(row["tplSlug"])
        self.assertTrue(row["tplNeedsReview"])
        self.assertEqual(row["tcgplayerId"], "662184")
        self.assertEqual(row["lookup"]["cardz"]["key"], repair.TARGET_OPAQUE_ID)

    def test_c11_row_moves_only_for_the_exact_pricecharting_product(self) -> None:
        row, changed = repair.transform_row(
            Path("c11_pc_ebay_map_full900.jsonl"),
            {
                "variant_id": repair.OLD_VARIANT_ID,
                "pc_product_id": int(repair.PC_ID),
                "collector_number": "125/132",
            },
            variant_id=999,
            stamp="2026-07-31T00:00:00Z",
            population=28156,
            population_as_of="2026-07-28",
        )
        other, other_changed = repair.transform_row(
            Path("c11_pc_ebay_map_full900.jsonl"),
            {
                "variant_id": repair.OLD_VARIANT_ID,
                "pc_product_id": 1,
            },
            variant_id=999,
            stamp="2026-07-31T00:00:00Z",
            population=28156,
            population_as_of="2026-07-28",
        )

        self.assertTrue(changed)
        self.assertEqual(row["variant_id"], 999)
        self.assertEqual(row["collector_number"], "125/094")
        self.assertFalse(other_changed)
        self.assertEqual(other["variant_id"], repair.OLD_VARIANT_ID)

    def test_replay_does_not_rewrite_only_for_a_new_timestamp(self) -> None:
        existing = {
            "gemrateId": repair.GEMRATE_ID,
            "variantId": 999,
            "opaqueId": repair.TARGET_OPAQUE_ID,
            "collectorNumber": "125/094",
            "psa10Population": 28156,
            "populationAsOf": "2026-07-28",
            "tplSlug": None,
            "tplSlugCandidate": None,
            "tplNeedsReview": True,
            "tplMatchScore": 0,
            "tcgplayerId": "662184",
            "tplHistoryDays": 0,
            "tplPsa10EbayUsd": None,
            "tplImageUrl": None,
            "updatedAt": "2026-07-30T00:00:00Z",
            "links": {
                "tpl": None,
                "tcgplayer": "https://www.tcgplayer.com/product/662184",
            },
            "lookup": {
                "tcgpricelookup": {"key": None, "link": None},
                "tcgplayer": {
                    "key": "662184",
                    "link": "https://www.tcgplayer.com/product/662184",
                },
                "cardz": {"key": repair.TARGET_OPAQUE_ID},
            },
        }
        row, changed = repair.transform_row(
            Path("qualified-940-identity.jsonl"),
            existing,
            variant_id=999,
            stamp="2026-07-31T00:00:00Z",
            population=28156,
            population_as_of="2026-07-28",
        )

        self.assertFalse(changed)
        self.assertEqual(row, existing)


if __name__ == "__main__":
    unittest.main()
