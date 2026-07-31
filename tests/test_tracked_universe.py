from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest

from pipelines.tracked_universe import (
    candidate_manifest_rows,
    merge_snk_refill_worklist,
    select_ranked_union,
    write_outputs,
)


EFFECTIVE_AT = datetime(2026, 7, 24, 12, tzinfo=timezone.utc)


def card(card_id: str, tcg: str, market_cap: float, population: int = 1500) -> dict:
    return {
        "pokedexId": card_id,
        "pokedexStatus": "confirmed",
        "canonicalSourceCode": "fixture",
        "canonicalExternalId": card_id,
        "tcg": tcg,
        "marketCapUsd": market_cap,
        "populationPsa10": population,
        "gemrateId": f"gem-{card_id}",
        "snkItemId": int(card_id.removeprefix("c")),
        "language": "ja",
        "collectorNumber": f"{card_id}/TEST",
        "populationSourceState": "gemrate_direct",
        "priceUsd": market_cap / population,
        "priceAsOf": "2026-07-23T12:00:00Z",
        "priceSourceState": "snk_exact",
    }


class TrackedUniverseTests(unittest.TestCase):
    def test_union_stores_card_once_and_keeps_multiple_rank_memberships(self) -> None:
        document = select_ranked_union(
            [
                card("c1", "pokemon", 300),
                card("c2", "one-piece", 200),
                card("c3", "pokemon", 100),
            ],
            limit=2,
            generated_at=EFFECTIVE_AT,
        )
        self.assertEqual(document["counts"]["uniqueRanked"], 3)
        self.assertEqual(document["cards"][0]["rankMemberships"], {"pokemon": 1, "tcg": 1})
        self.assertEqual(document["cards"][1]["rankMemberships"], {"one-piece": 1, "tcg": 2})
        self.assertEqual(document["cards"][2]["rankMemberships"], {"pokemon": 2, "tcg": 3})

    def test_population_below_1000_and_missing_fresh_price_are_excluded(self) -> None:
        rows = [
            card("c1", "pokemon", 300, population=999),
            {**card("c2", "pokemon", 200), "priceUsd": None},
            card("c3", "one-piece", 100, population=1000),
        ]
        document = select_ranked_union(rows, limit=300, generated_at=EFFECTIVE_AT)
        self.assertEqual([row["pokedexId"] for row in document["cards"]], ["c3"])
        self.assertEqual(document["rejected"], {
            "population_not_gemrate_confirmed_psa10_1000": 1,
            "reference_price_not_fresh": 1,
        })

    def test_each_index_keeps_complete_eligible_membership(self) -> None:
        rows = [card(f"c{index}", "pokemon", 1000 - index) for index in range(1, 6)]
        rows += [card(f"c{index}", "one-piece", 2000 - index) for index in range(6, 11)]
        document = select_ranked_union(rows, limit=3, generated_at=EFFECTIVE_AT)
        self.assertEqual(document["counts"]["indexes"], {"one-piece": 5, "pokemon": 5, "tcg": 10})
        self.assertEqual(document["counts"]["uniqueRanked"], 10)

    def test_underfilled_presentation_does_not_block_canonical_membership_output(self) -> None:
        ready = select_ranked_union(
            [card("c1", "pokemon", 300), card("c2", "one-piece", 200)],
            limit=1,
            generated_at=EFFECTIVE_AT,
        )
        incomplete = select_ranked_union(
            [card("c3", "pokemon", 300)],
            limit=1,
            generated_at=EFFECTIVE_AT,
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "tracked-universe.json"
            gemrate = root / "gemrate.txt"
            snk = root / "snk.txt"
            write_outputs(ready, output, gemrate, snk)
            before = output.read_bytes()
            write_outputs(incomplete, output, gemrate, snk)
            self.assertNotEqual(output.read_bytes(), before)
            self.assertEqual(json.loads(output.read_text(encoding="utf-8"))["counts"]["uniqueRanked"], 1)

    def test_pre_entry_candidates_extend_provider_worklists_without_becoming_ranked_cards(self) -> None:
        document = select_ranked_union(
            [
                card("c1", "pokemon", 300, population=1000),
                card("c2", "pokemon", 250, population=999),
                card("c3", "one-piece", 200, population=970),
            ],
            generated_at=EFFECTIVE_AT,
        )
        self.assertEqual([row["pokedexId"] for row in document["cards"]], ["c1"])
        self.assertEqual([row["pokedexId"] for row in document["monitoringCandidates"]], ["c2"])
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "tracked-universe.json"
            gemrate = root / "gemrate.txt"
            snk = root / "snk.txt"
            write_outputs(document, output, gemrate, snk)
            self.assertEqual(gemrate.read_text(encoding="ascii").splitlines(), ["gem-c1", "gem-c2"])
            self.assertEqual(snk.read_text(encoding="ascii").splitlines(), ["1", "2"])

    def test_candidate_overlay_keeps_unresolved_rows_private_and_promotes_exact_snk_price(self) -> None:
        manifest = {
            "candidates": [
                {
                    "status": "resolved",
                    "trackingStatus": "eligible",
                    "identityStatus": "exact_confirmed",
                    "gemrateId": "gem-candidate",
                    "snkItemId": 99,
                    "populationPsa10": 1200,
                    "effectiveDate": "2026-07-24",
                    "canonicalSourceCode": "gemrate",
                    "canonicalExternalId": "candidate-1",
                    "canonicalIdentity": {
                        "tcg": "one-piece",
                        "language": "ja",
                        "setName": "OP Test",
                        "collectorNumber": "OP01-001",
                        "name": "Candidate",
                    },
                },
                {"status": "unavailable", "trackingStatus": "eligible"},
            ]
        }
        rows, rejected = candidate_manifest_rows(
            manifest,
            snk_rows={99: {"item_id": 99, "kline": [{"date": "2026-07-24", "price_jpy": 15000}]}},
            jpy_per_usd=150,
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["priceUsd"], 100)
        self.assertEqual(rejected["tracked_candidate_unresolved"], 1)
        document = select_ranked_union(rows, generated_at=EFFECTIVE_AT)
        self.assertEqual(document["counts"]["uniqueRanked"], 1)

    def test_exact_snk_worklist_association_is_merged_before_candidate_promotion(self) -> None:
        identity = {
            "tcg": "one-piece",
            "language": "ja",
            "setName": "OP Test",
            "collectorNumber": "OP01-001",
            "name": "Candidate",
            "edition": "standard",
            "parallel": "manga",
            "finish": "foil",
        }
        manifest = {
            "candidates": [
                {
                    "status": "resolved",
                    "trackingStatus": "eligible",
                    "identityStatus": "exact_confirmed",
                    "gemrateId": "gem-candidate",
                    "snkItemId": None,
                    "populationPsa10": 1200,
                    "effectiveDate": "2026-07-24",
                    "canonicalSourceCode": "gemrate",
                    "canonicalExternalId": "candidate-1",
                    "canonicalSource": {
                        "sourceCode": "gemrate",
                        "externalId": "candidate-1",
                        "snkItemId": None,
                    },
                    "canonicalIdentity": identity,
                }
            ]
        }
        worklist = {
            "cards": [
                {
                    "status": "resolved",
                    "identityStatus": "exact_confirmed",
                    "snkItemId": 99,
                    "canonicalSourceCode": "gemrate",
                    "canonicalExternalId": "candidate-1",
                    "canonicalIdentity": {**identity, "language": "en"},
                }
            ]
        }

        merged, counts = merge_snk_refill_worklist(manifest, worklist)
        rows, rejected = candidate_manifest_rows(
            merged,
            snk_rows={99: {"item_id": 99, "kline": [{"date": "2026-07-24", "price_jpy": 15000}]}},
            jpy_per_usd=150,
        )

        self.assertEqual(counts["attached"], 1)
        self.assertEqual(merged["candidates"][0]["canonicalSource"]["snkItemId"], 99)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rejected, {})

    def test_exact_snk_worklist_requires_complete_printing_not_language(self) -> None:
        identity = {
            "tcg": "one-piece",
            "language": "ja",
            "setName": "OP Test",
            "collectorNumber": "OP01-001",
            "edition": "standard",
            "parallel": "manga",
            "finish": "foil",
        }
        candidate = {
            "identityStatus": "exact_confirmed",
            "snkItemId": None,
            "canonicalSourceCode": "gemrate",
            "canonicalExternalId": "candidate-1",
            "canonicalIdentity": identity,
        }
        worklist_card = {
            "status": "resolved",
            "identityStatus": "exact_confirmed",
            "snkItemId": 99,
            "canonicalSourceCode": "gemrate",
            "canonicalExternalId": "candidate-1",
            "canonicalIdentity": {**identity, "language": "en"},
        }
        merged, counts = merge_snk_refill_worklist(
            {"candidates": [candidate]},
            {"cards": [worklist_card]},
        )
        self.assertEqual(counts["attached"], 1)
        self.assertEqual(merged["candidates"][0]["snkItemId"], 99)

        incomplete = {**worklist_card, "canonicalIdentity": {**identity, "finish": ""}}
        with self.assertRaisesRegex(RuntimeError, "identity mismatch"):
            merge_snk_refill_worklist(
                {"candidates": [{**candidate, "snkItemId": None}]},
                {"cards": [incomplete]},
            )

    def test_exact_snk_worklist_rebind_is_blocked(self) -> None:
        identity = {
            "tcg": "one-piece",
            "language": "ja",
            "setName": "OP Test",
            "collectorNumber": "OP01-001",
            "edition": "standard",
            "parallel": "manga",
            "finish": "foil",
        }
        manifest = {
            "candidates": [
                {
                    "identityStatus": "exact_confirmed",
                    "snkItemId": 98,
                    "canonicalSourceCode": "gemrate",
                    "canonicalExternalId": "candidate-1",
                    "canonicalIdentity": identity,
                }
            ]
        }
        worklist = {
            "cards": [
                {
                    "status": "resolved",
                    "identityStatus": "exact_confirmed",
                    "snkItemId": 99,
                    "canonicalSourceCode": "gemrate",
                    "canonicalExternalId": "candidate-1",
                    "canonicalIdentity": identity,
                }
            ]
        }

        with self.assertRaisesRegex(RuntimeError, "SNK identity rebind blocked"):
            merge_snk_refill_worklist(manifest, worklist)


if __name__ == "__main__":
    unittest.main()
