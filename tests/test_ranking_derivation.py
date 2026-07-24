from __future__ import annotations

from datetime import datetime, timezone
import unittest

from pipelines.ranking_derivation import derive_rankings


EFFECTIVE_AT = datetime(2026, 7, 24, 12, tzinfo=timezone.utc)


def candidate(
    card_id: str,
    tcg: str,
    *,
    population: int = 1_000,
    price: float = 100.0,
    price_as_of: str = "2026-07-23T12:00:00Z",
    **extra: object,
) -> dict[str, object]:
    return {
        "pokedexId": card_id,
        "pokedexStatus": "confirmed",
        "canonicalSourceCode": "fixture",
        "canonicalExternalId": card_id,
        "gemrateId": f"gem-{card_id}",
        "tcg": tcg,
        "language": "ja",
        "name": f"Card {card_id}",
        "setName": "Fixture Set",
        "collectorNumber": f"{card_id}/TEST",
        "populationPsa10": population,
        "populationSourceState": "gemrate_direct",
        "priceUsd": price,
        "priceAsOf": price_as_of,
        "priceSourceState": "snk_exact",
        **extra,
    }


class RankingDerivationTests(unittest.TestCase):
    def test_default_contract_keeps_complete_rankings_and_derives_views(self) -> None:
        rows = [
            candidate(f"p{index}", "pokemon", price=1000 - index)
            for index in range(351)
        ] + [
            candidate(f"o{index}", "one-piece", price=2000 - index)
            for index in range(350)
        ]
        document = derive_rankings(rows, effective_at=EFFECTIVE_AT)

        self.assertEqual(document["promotion"]["status"], "ready")
        self.assertEqual(document["counts"]["indexes"], {"tcg": 701, "pokemon": 351, "one-piece": 350})
        self.assertEqual(document["counts"]["uniqueRanked"], 701)
        self.assertEqual(document["policy"]["canonicalMembership"], "complete_eligible")
        self.assertNotIn("limitPerIndex", document["policy"])
        self.assertEqual(document["presentationViews"]["top300"]["range"], {"startRank": 1, "endRank": 300})
        self.assertEqual(document["presentationViews"]["reserve50"]["range"], {"startRank": 301, "endRank": 350})
        self.assertEqual(document["presentationViews"]["top100_plus_200"], {"aliasOf": "top300"})
        self.assertEqual(document["schemaVersion"], "5.0.0")

    def test_derives_all_three_rankings_from_validated_formula_and_dedups_printing(self) -> None:
        rows = [
            candidate("p1", "pokemon", price=300),
            candidate("p2", "pokemon", price=200),
            candidate("o1", "one-piece", price=400),
            candidate("o2", "one-piece", price=100),
            candidate("p1", "pokemon", price=999),
        ]
        document = derive_rankings(rows, effective_at=EFFECTIVE_AT)

        self.assertEqual(document["promotion"]["status"], "ready")
        self.assertEqual(document["counts"]["indexes"], {"tcg": 4, "pokemon": 2, "one-piece": 2})
        self.assertEqual(
            document["rankings"]["tcg"],
            [
                {"identityKey": "p1", "rank": 1},
                {"identityKey": "o1", "rank": 2},
                {"identityKey": "p2", "rank": 3},
                {"identityKey": "o2", "rank": 4},
            ],
        )
        p1 = next(row for row in document["cards"] if row["pokedexId"] == "p1")
        self.assertEqual(p1["marketCapUsd"], 999000.0)
        self.assertEqual(p1["rankMemberships"], {"pokemon": 1, "tcg": 1})

    def test_underfilled_views_do_not_block_canonical_ingestion(self) -> None:
        rows = [candidate("p1", "pokemon"), candidate("o1", "one-piece", population=999)]
        document = derive_rankings(rows, effective_at=EFFECTIVE_AT)

        self.assertEqual(document["promotion"], {"status": "ready", "blockers": []})
        self.assertEqual(
            document["rejected"],
            {"population_not_gemrate_confirmed_psa10_1000": 1},
        )
        self.assertEqual(document["publication"]["views"]["top100"]["status"], "blocked")

    def test_requested_view_is_the_only_publication_gate(self) -> None:
        rows = [
            candidate(f"p{index}", "pokemon", price=1000 - index)
            for index in range(100)
        ] + [
            candidate(f"o{index}", "one-piece", price=2000 - index)
            for index in range(100)
        ]
        top100 = derive_rankings(rows, effective_at=EFFECTIVE_AT, required_views=("top100",))
        top300 = derive_rankings(rows, effective_at=EFFECTIVE_AT, required_views=("top300",))

        self.assertEqual(top100["promotion"], {"status": "ready", "blockers": []})
        self.assertEqual(top300["promotion"]["status"], "blocked")
        self.assertEqual(
            top300["promotion"]["blockers"],
            [
                {"view": "top300", "index": "tcg", "required": 300, "actual": 200},
                {"view": "top300", "index": "pokemon", "required": 300, "actual": 100},
                {"view": "top300", "index": "one-piece", "required": 300, "actual": 100},
            ],
        )
        self.assertEqual(top300["counts"]["uniqueRanked"], 200)

    def test_requires_complete_identity_non_estimate_population_and_fresh_price(self) -> None:
        rows = [
            candidate("missing-language", "pokemon", language=""),
            candidate("estimated", "pokemon", populationEstimated=True),
            candidate("old-price", "one-piece", price_as_of="2026-07-20T11:59:59Z"),
        ]
        document = derive_rankings(rows, effective_at=EFFECTIVE_AT)

        self.assertEqual(document["counts"]["validatedCanonical"], 0)
        self.assertEqual(document["rejected"], {
            "identity_unconfirmed_or_incomplete": 1,
            "population_not_gemrate_confirmed_psa10_1000": 1,
            "reference_price_not_fresh": 1,
        })

    def test_membership_history_and_hash_are_idempotent(self) -> None:
        initial = derive_rankings(
            [candidate("p1", "pokemon", price=100), candidate("o1", "one-piece", price=200)],
            effective_at=EFFECTIVE_AT,
        )
        next_generation = derive_rankings(
            [candidate("p1", "pokemon", price=300), candidate("o1", "one-piece", price=200)],
            effective_at=EFFECTIVE_AT,
            previous=initial,
        )
        repeated = derive_rankings(
            [candidate("p1", "pokemon", price=300), candidate("o1", "one-piece", price=200)],
            effective_at=EFFECTIVE_AT,
            previous=initial,
        )

        p1_history = next(item for item in next_generation["membershipHistory"] if item["identityKey"] == "p1")
        self.assertEqual(p1_history["previous"], {"pokemon": 1, "tcg": 2})
        self.assertEqual(p1_history["current"], {"pokemon": 1, "tcg": 1})
        self.assertTrue(p1_history["changed"])
        self.assertEqual(next_generation["rankingSha256"], repeated["rankingSha256"])
        self.assertEqual(next_generation["payloadSha256"], repeated["payloadSha256"])

    def test_historical_backfill_queue_deduplicates_scopes_and_retries_until_ready(self) -> None:
        first = derive_rankings(
            [
                candidate("p1", "pokemon", price=300),
                candidate("o1", "one-piece", price=200, historyBackfillStatus="ready"),
            ],
            effective_at=EFFECTIVE_AT,
        )
        self.assertEqual(
            first["historicalBackfillQueue"],
            [
                {
                    "identityKey": "p1",
                    "pokedexId": "p1",
                    "tcg": "pokemon",
                    "rankMemberships": {"pokemon": 1, "tcg": 1},
                    "reason": "new_target",
                }
            ],
        )
        repeated = derive_rankings(
            [candidate("p1", "pokemon", price=300)],
            effective_at=EFFECTIVE_AT,
            previous=first,
        )
        self.assertEqual(repeated["historicalBackfillQueue"][0]["reason"], "pending_history")
        completed = derive_rankings(
            [candidate("p1", "pokemon", price=300, historyBackfillStatus="ready")],
            effective_at=EFFECTIVE_AT,
            previous=first,
        )
        self.assertEqual(completed["historicalBackfillQueue"], [])

    def test_pre_entry_monitoring_only_accepts_exact_gemrate_pop_971_to_999(self) -> None:
        rows = [
            candidate("formal", "pokemon", population=1000, price=100),
            candidate("near", "pokemon", population=999, price=100, isNew=True, priceAccelerating=True),
            candidate("entry", "one-piece", population=971, price=30, populationAccelerating=True),
            candidate("too-low", "pokemon", population=970, price=1000, isNew=True, priceAccelerating=True),
            candidate("estimated", "one-piece", population=999, populationEstimated=True),
        ]
        document = derive_rankings(rows, effective_at=EFFECTIVE_AT)

        monitoring = {row["identityKey"]: row for row in document["monitoringCandidates"]}
        self.assertEqual(set(monitoring), {"near", "entry"})
        self.assertEqual(monitoring["near"]["monitoringState"], "pre_entry_population_971_999")
        self.assertEqual(monitoring["near"]["collectionCadence"], "daily")
        self.assertEqual(monitoring["near"]["name"], "Card near")
        self.assertEqual(monitoring["near"]["setName"], "Fixture Set")
        self.assertIn("projected_rank300_proximity", monitoring["near"]["reasons"])
        self.assertEqual(monitoring["near"]["rank300Cutoff"]["basis"], "lowest_formal_rank_available")
        self.assertNotIn("near", {row["pokedexId"] for row in document["cards"]})
        self.assertEqual(document["monitoring"]["discoveryOnlyPopulationMaximum"], 970)
        self.assertNotEqual(document["payloadSha256"], document["collectionPayloadSha256"])


if __name__ == "__main__":
    unittest.main()
