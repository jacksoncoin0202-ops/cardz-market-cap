from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

from data_coverage_audit import build_audit, presentation_view_readiness  # noqa: E402


class DataCoverageAuditTests(unittest.TestCase):
    def test_top300_boards_uses_combined_300_and_each_board_100(self) -> None:
        ready, requirements = presentation_view_readiness(
            "top300_boards",
            {"tcg": 300, "pokemon": 100, "onePiece": 100},
        )
        self.assertEqual(requirements, {"tcg": 300, "pokemon": 100, "onePiece": 100})
        self.assertTrue(all(ready.values()))

        short, _ = presentation_view_readiness(
            "top300_boards",
            {"tcg": 300, "pokemon": 100, "onePiece": 99},
        )
        self.assertFalse(short["onePiece"])

    def test_mapping_only_is_not_counted_as_verified_data(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            crosswalk = self.write_crosswalk(
                root,
                [
                    self.card("1", "a" * 40, 101, "001/100"),
                    self.card("2", "b" * 40, 102, "002/100"),
                ],
            )
            self.write_gemrate(root, "a" * 40, 2500)
            snk = self.write_snk(root, [self.snk_row(101, "001/100", 200_000)])

            report = build_audit(
                crosswalk,
                root / "gemrate",
                snk,
                captured_at=datetime(2026, 7, 23, tzinfo=timezone.utc),
                jpy_per_usd=160,
            )

            self.assertEqual(report["counts"]["mapped"], 2)
            self.assertEqual(report["counts"]["gemrateVerified"], 1)
            self.assertEqual(report["counts"]["snkVerified"], 1)
            self.assertEqual(report["counts"]["marketReady"], 1)
            self.assertEqual(report["counts"]["top100Eligible"], 1)
            self.assertFalse(report["coverage"]["globalTop100Verified"])
            self.assertEqual(report["refill"]["gemrateIds"], ["b" * 40])
            self.assertEqual(report["refill"]["snkItemIds"], [102])

    def test_wrong_grade_and_collector_are_quarantined(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            crosswalk = self.write_crosswalk(
                root,
                [
                    self.card("1", "a" * 40, 101, "OP01-120", market="one-piece"),
                    self.card("2", "b" * 40, 102, "OP06-118", market="one-piece"),
                ],
            )
            self.write_gemrate(root, "a" * 40, 4000)
            self.write_gemrate(root, "b" * 40, 3000)
            snk = self.write_snk(
                root,
                [
                    self.snk_row(
                        101,
                        "OP01-120",
                        300_000,
                        condition="trading_card_single_raw",
                    ),
                    self.snk_row(102, "OP05-118", 100_000),
                ],
            )

            report = build_audit(
                crosswalk,
                root / "gemrate",
                snk,
                captured_at=datetime(2026, 7, 23, tzinfo=timezone.utc),
                jpy_per_usd=160,
            )

            self.assertEqual(report["counts"]["marketReady"], 0)
            reasons = {row["reason"] for row in report["quarantine"]}
            self.assertIn("snk_grade_mismatch", reasons)
            self.assertIn("snk_collector_mismatch", reasons)

    def test_market_cap_and_rank_are_derived_from_verified_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            crosswalk = self.write_crosswalk(
                root,
                [
                    self.card("1", "a" * 40, 101, "001/100"),
                    self.card("2", "b" * 40, 102, "002/100"),
                ],
            )
            self.write_gemrate(root, "a" * 40, 2000)
            self.write_gemrate(root, "b" * 40, 4000)
            snk = self.write_snk(
                root,
                [
                    self.snk_row(101, "001/100", 160_000),
                    self.snk_row(102, "002/100", 80_000),
                ],
            )

            first = build_audit(
                crosswalk,
                root / "gemrate",
                snk,
                captured_at=datetime(2026, 7, 23, tzinfo=timezone.utc),
                jpy_per_usd=160,
            )
            second = build_audit(
                crosswalk,
                root / "gemrate",
                snk,
                captured_at=datetime(2026, 7, 23, tzinfo=timezone.utc),
                jpy_per_usd=160,
            )

            self.assertEqual(first["payloadSha256"], second["payloadSha256"])
            self.assertEqual([row["canonicalExternalId"] for row in first["rankings"]["combined"]], ["1", "2"])
            self.assertEqual(first["rankings"]["combined"][0]["marketCapUsd"], 2_000_000)
            self.assertEqual(first["rankings"]["combined"][1]["marketCapUsd"], 2_000_000)

    def test_top350_worklist_inventories_mirrors_and_ebay_without_relaxing_primary_gate(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cards = [
                self.card(str(index), f"{index:040x}", index, f"{index:03d}/100")
                for index in range(1, 351)
            ] + [
                self.card(str(index + 350), f"{index + 350:040x}", index + 350, f"OP01-{index:03d}", market="one-piece")
                for index in range(1, 351)
            ]
            crosswalk = self.write_crosswalk(root, cards)
            snk_rows = []
            for card in cards:
                gemrate_id = str(card["gemrateId"])
                item_id = int(card["snkItemId"])
                collector = str(card["collectorNumberRaw"])
                self.write_gemrate(root, gemrate_id, 1000)
                self.write_g10_mirror(root, card, 1000)
                snk_rows.append(self.snk_row(item_id, collector, 160_000))
            snk = self.write_snk(root, snk_rows)
            ebay = self.write_ebay(root, cards[0])

            report = build_audit(
                crosswalk,
                root / "gemrate",
                snk,
                captured_at=datetime(2026, 7, 23, tzinfo=timezone.utc),
                jpy_per_usd=160,
                discovery_complete=True,
                g10_mirror_root=root / "g10",
                ebay_path=ebay,
            )

            self.assertTrue(report["coverage"]["top350Ready"]["pokemon"])
            self.assertTrue(report["coverage"]["top350Ready"]["onePiece"])
            self.assertTrue(report["coverage"]["top350Ready"]["tcg"])
            self.assertTrue(report["coverage"]["globalTop350Verified"])
            self.assertEqual(len(report["rankings"]["combined"]), 700)
            self.assertTrue(report["coverage"]["presentationViews"]["top100"]["verified"])
            self.assertTrue(report["coverage"]["presentationViews"]["top300"]["verified"])
            self.assertTrue(report["coverage"]["presentationViews"]["top100_plus_200"]["verified"])
            self.assertTrue(report["coverage"]["presentationViews"]["reserve50"]["verified"])
            self.assertEqual(report["sourceInventory"]["grade10GemrateMirror"]["matchesDirect"], 700)
            self.assertEqual(report["sourceInventory"]["ebay"]["fallbackReadyCards"], 1)
            self.assertEqual(report["gapWorklist"][0]["required"], [])
            self.assertEqual(report["gapWorklist"][0]["advisory"], ["ebay_exact_psa10_sold_30d"])

    @staticmethod
    def card(
        external_id: str,
        gemrate_id: str,
        snk_item_id: int,
        collector: str,
        *,
        market: str = "pokemon",
    ) -> dict[str, object]:
        return {
            "canonicalSourceCode": "snkrdunk",
            "canonicalExternalId": external_id,
            "storageScope": "snkrdunk",
            "market": market,
            "gemrateId": gemrate_id,
            "snkItemId": snk_item_id,
            "name": f"Card {external_id}",
            "collectorNumberRaw": collector,
            "language": "ja",
            "setName": "Fixture Set",
        }

    @staticmethod
    def write_crosswalk(root: Path, cards: list[dict[str, object]]) -> Path:
        path = root / "crosswalk.json"
        path.write_text(json.dumps({"cards": cards}), encoding="utf-8")
        return path

    @staticmethod
    def write_gemrate(root: Path, gemrate_id: str, psa10: int) -> None:
        path = root / "gemrate" / gemrate_id / "population.json"
        path.parent.mkdir(parents=True)
        path.write_text(
            json.dumps(
                {
                    "data": {
                        "gemrate_id": gemrate_id,
                        "population": {
                            "population_data": {
                                "data_last_updated": "2026-07-22",
                                "by_grader": {
                                    "psa": {
                                        "grades": {"psa_10": psa10},
                                        "total": psa10 + 100,
                                    }
                                },
                            }
                        },
                    }
                }
            ),
            encoding="utf-8",
        )

    @staticmethod
    def snk_row(
        item_id: int,
        collector: str,
        price_jpy: int,
        *,
        condition: str = "trading_card_single_psa10",
    ) -> dict[str, object]:
        return {
            "item_id": item_id,
            "name": f"Fixture [{collector}]",
            "product_number": collector,
            "condition_filter": condition,
            "fetched_at": "2026-07-23T00:00:00Z",
            "kline": [{"date": "2026-07-22", "price_jpy": price_jpy}],
            "daily_activity": {},
        }

    @staticmethod
    def write_snk(root: Path, rows: list[dict[str, object]]) -> Path:
        path = root / "snk.jsonl"
        path.write_text(
            "\n".join(json.dumps(row) for row in rows) + "\n",
            encoding="utf-8",
        )
        return path

    @staticmethod
    def write_g10_mirror(root: Path, card: dict[str, object], psa10: int) -> None:
        path = root / "g10" / "cards" / str(card["storageScope"]) / str(card["canonicalExternalId"]) / "populations.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({"population": [{"gradeName": "PSA", "topGrade": psa10}]}), encoding="utf-8")

    @staticmethod
    def write_ebay(root: Path, card: dict[str, object]) -> Path:
        path = root / "ebay.jsonl"
        transactions = [
            {
                "transactionId": f"sold-{index}",
                "soldDate": f"2026-07-{20 + index:02d}",
                "unitPrice": 100 + index,
                "currency": "USD",
                "quantity": 1,
            }
            for index in range(1, 4)
        ]
        path.write_text(
            json.dumps(
                {
                    "schemaVersion": "1.0.0",
                    "sourceCode": card["canonicalSourceCode"],
                    "externalEntityId": card["canonicalExternalId"],
                    "transactions": transactions,
                }
            ) + "\n",
            encoding="utf-8",
        )
        return path


if __name__ == "__main__":
    unittest.main()
