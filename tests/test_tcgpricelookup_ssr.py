from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

from tcgpricelookup_ssr import merge_incremental, parse_rsc  # noqa: E402


SAMPLE_RSC = r'''
["$","$Lf",null,{"card":{"id":"abc","slug":"pokemon-base-set-charizard-004-102-holofoil","tcgplayer_id":"42382","name":"Charizard","number":"004/102","rarity":"Holo Rare","variant":"Holofoil","image_url":"https://cdn.example/x.webp","updated_at":"2026-07-11T00:00:00.000Z","set":{"id":"s1","slug":"pokemon--base-set","name":"Base Set"},"game":{"id":"g1","slug":"pokemon","name":"Pokemon"},"prices":{"raw":{"near_mint":{"ebay":{"avg_1d":410},"tcgplayer":{"market":720.34}}},"graded":{"psa":{"10":{"ebay":{"avg_1d":30100,"avg_7d":30100,"avg_30d":30100}}}}},"history":[]}}]
{"date":"2025-09-15","prices":[{"source":"ebay","condition":null,"grader":"psa","grade":"10","avg_1d":17500,"avg_7d":17500,"avg_30d":17500}]}
{"date":"2026-05-04","prices":[{"source":"ebay","condition":null,"grader":"psa","grade":"10","avg_1d":30100,"avg_7d":30100,"avg_30d":30100}]}
'''


class TplSsrParseTests(unittest.TestCase):
    def test_parse_card_and_psa10_history(self) -> None:
        row = parse_rsc(SAMPLE_RSC, source_url="https://example/card/x")
        self.assertTrue(row["ok"])
        self.assertEqual(row["slug"], "pokemon-base-set-charizard-004-102-holofoil")
        self.assertEqual(row["tcgplayerId"], "42382")
        self.assertEqual(row["number"], "004/102")
        self.assertEqual(row["historyDaysEmbedded"], 2)
        self.assertEqual(row["psa10"]["historyDays"], 2)
        self.assertEqual(row["psa10"]["history"][0]["avg_1d"], 17500)
        self.assertEqual(row["psa10"]["ebayAvg1d"], 30100)

    def test_incremental_upsert_keeps_union_of_dates(self) -> None:
        full = parse_rsc(SAMPLE_RSC)
        # fresh only has newer day + new price
        fresh_rsc = SAMPLE_RSC.replace("17500", "99999").replace("2025-09-15", "2026-07-01")
        # rebuild a minimal fresh with one overlapping + one new
        fresh = parse_rsc(
            '''
            {"card":{"id":"abc","slug":"pokemon-base-set-charizard-004-102-holofoil","tcgplayer_id":"42382","name":"Charizard","number":"004/102","prices":{"graded":{"psa":{"10":{"ebay":{"avg_1d":31000}}}}}}}
            {"date":"2026-05-04","prices":[{"source":"ebay","grader":"psa","grade":"10","avg_1d":30500}]}
            {"date":"2026-07-01","prices":[{"source":"ebay","grader":"psa","grade":"10","avg_1d":32000}]}
            '''
        )
        self.assertTrue(fresh["ok"])
        merged = merge_incremental(full, fresh)
        dates = [d["date"] for d in merged["history"]]
        self.assertIn("2025-09-15", dates)
        self.assertIn("2026-05-04", dates)
        self.assertIn("2026-07-01", dates)
        self.assertEqual(merged["mergeMode"], "incremental_upsert")
        # overlapping day overwritten by fresh
        by_date = {d["date"]: d for d in merged["psa10"]["history"]}
        self.assertEqual(by_date["2026-05-04"]["avg_1d"], 30500)


if __name__ == "__main__":
    unittest.main()
