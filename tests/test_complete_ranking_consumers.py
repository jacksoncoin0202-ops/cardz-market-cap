from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

import db_runtime  # noqa: E402
import market_source_sync  # noqa: E402
from ranking_derivation import derive_rankings  # noqa: E402


def complete_ranking_document(per_market: int = 351) -> dict[str, object]:
    cards: list[dict[str, object]] = []
    combined_rank = 0
    for tcg in ("pokemon", "one-piece"):
        for market_rank in range(1, per_market + 1):
            combined_rank += 1
            opaque = f"{tcg}-{market_rank}"
            cards.append(
                {
                    "pokedexId": opaque,
                    "pokedexStatus": "confirmed",
                    "canonicalSourceCode": "fixture",
                    "canonicalExternalId": opaque,
                    "tcg": tcg,
                    "language": "ja",
                    "name": opaque,
                    "setName": "Fixture",
                    "collectorNumber": f"{market_rank:03d}/TEST",
                    "populationPsa10": 1000,
                    "rankMemberships": {"tcg": combined_rank, tcg: market_rank},
                }
            )
    return {
        "schemaVersion": "4.0.0",
        "policy": {
            "indexes": ["tcg", "pokemon", "one-piece"],
            "canonicalMembership": "complete_eligible",
            "languagePartitioning": False,
        },
        "cards": cards,
        "payloadSha256": db_runtime.sha256(db_runtime.canonical_json(cards)),
    }


class CompleteRankingConsumerTests(unittest.TestCase):
    def test_consumers_accept_more_than_350_without_truncating_storage(self) -> None:
        document = complete_ranking_document()
        self.assertEqual(len(db_runtime.validate_active_universe(document)), 702)
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "ranking.json"
            path.write_text(json.dumps(document), encoding="utf-8")
            self.assertEqual(
                len(market_source_sync.load_active_universe(path)["cards"]),
                702,
            )

    def test_non_contiguous_complete_ranks_fail_closed(self) -> None:
        document = complete_ranking_document(per_market=2)
        document["cards"][1]["rankMemberships"]["tcg"] = 99
        document["payloadSha256"] = db_runtime.sha256(db_runtime.canonical_json(document["cards"]))
        with self.assertRaisesRegex(ValueError, "not contiguous"):
            db_runtime.validate_active_universe(document)
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "ranking.json"
            path.write_text(json.dumps(document), encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "non-contiguous"):
                market_source_sync.load_active_universe(path)

    def test_v5_monitoring_pool_is_validated_and_included_once_in_collection_union(self) -> None:
        rows = [
            {
                "pokedexId": "formal",
                "pokedexStatus": "confirmed",
                "canonicalSourceCode": "fixture",
                "canonicalExternalId": "formal",
                "gemrateId": "gem-formal",
                "snkItemId": 1,
                "tcg": "pokemon",
                "language": "ja",
                "name": "Formal fixture",
                "setName": "Fixture",
                "collectorNumber": "001/TEST",
                "populationPsa10": 1000,
                "populationSourceState": "gemrate_direct",
                "priceUsd": 100,
                "priceAsOf": "2026-07-24T00:00:00Z",
                "priceSourceState": "snk_exact",
            },
            {
                "pokedexId": "monitor",
                "pokedexStatus": "confirmed",
                "canonicalSourceCode": "fixture",
                "canonicalExternalId": "monitor",
                "gemrateId": "gem-monitor",
                "snkItemId": 2,
                "tcg": "pokemon",
                "language": "ja",
                "name": "Pre-entry fixture",
                "setName": "Fixture",
                "collectorNumber": "002/TEST",
                "populationPsa10": 999,
                "populationSourceState": "gemrate_direct",
                "priceUsd": 100,
                "priceAsOf": "2026-07-24T00:00:00Z",
                "priceSourceState": "snk_exact",
            },
        ]
        document = derive_rankings(rows, effective_at=datetime(2026, 7, 24, tzinfo=timezone.utc))
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "ranking.json"
            path.write_text(json.dumps(document), encoding="utf-8")
            loaded = market_source_sync.load_active_universe(path)
            self.assertEqual([row["pokedexId"] for row in loaded["cards"]], ["formal"])
            self.assertEqual([row["pokedexId"] for row in loaded["monitoringCandidates"]], ["monitor"])
            self.assertEqual(
                [row["pokedexId"] for row in market_source_sync.collection_cards(loaded)],
                ["formal", "monitor"],
            )
            observations, counts = market_source_sync.build_source_observations(
                loaded,
                Path(temporary) / "gemrate",
                {
                    1: {"fetched_at": "2026-07-24T00:00:00Z", "kline": [{"date": "2026-07-24", "price_jpy": 10000}]},
                    2: {"fetched_at": "2026-07-24T00:00:00Z", "kline": [{"date": "2026-07-24", "price_jpy": 12000}]},
                },
                {},
                {},
                100.0,
                datetime(2026, 7, 24, tzinfo=timezone.utc),
                1,
            )
            self.assertEqual(counts["snkCards"], 2)
            self.assertEqual(counts["snkPriceObservations"], 2)
            self.assertEqual(len([row for row in observations if row["observationKind"] == "index_constituent"]), 2)


if __name__ == "__main__":
    unittest.main()
