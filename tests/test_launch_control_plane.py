from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

import collect_control as collect  # noqa: E402
import operator_control as operator  # noqa: E402


class IncrementalCheckpointTests(unittest.TestCase):
    def test_stock_rows_without_checkpoint_are_due(self) -> None:
        observed = datetime.now(timezone.utc).replace(tzinfo=None)
        self.assertEqual(
            collect._poll_mode(
                has_stock=True,
                observed_at=observed,
                checkpoint=None,
            ),
            "incr",
        )
        self.assertEqual(
            collect._poll_mode(
                has_stock=True,
                observed_at=observed,
                checkpoint={"last_effective_at": observed},
            ),
            "ok",
        )
        self.assertEqual(
            collect._poll_mode(
                has_stock=True,
                observed_at=observed,
                checkpoint={"last_effective_at": observed - timedelta(hours=37)},
            ),
            "incr",
        )

    def test_snk_harvest_requires_every_exact_id(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "harvest.jsonl"
            path.write_text(
                json.dumps({"item_id": 10, "condition_filter": "trading_card_single_psa10"}) + "\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(RuntimeError, "exact-ID mismatch"):
                collect._validate_snk_harvest(path, [10, 11])

    def test_english_card_with_exact_snk_id_is_collectable_not_bind_due(self) -> None:
        row = {
            "variantId": 7,
            "lang": "en",
            "ids": {"gemrate": "g" * 40, "snkrdunk": "123", "pricecharting": None, "ebay": None},
            "sales": {"snkAny": True, "ebayAny": False},
            "prices": {"snkAny": True, "enAny": False},
            "_popMax": datetime.now(timezone.utc).replace(tzinfo=None),
            "_snkSaleMax": datetime.now(timezone.utc).replace(tzinfo=None),
            "_ebaySaleMax": None,
            "_snkPriceMax": datetime.now(timezone.utc).replace(tzinfo=None),
            "_enPriceMax": None,
        }
        needs = collect.classify_needs(row, {})
        self.assertIn("snk_trades", {item["adapter"] for item in needs})
        self.assertIn("snk_price", {item["adapter"] for item in needs})
        self.assertNotIn("bind_pc_or_ebay", {item["adapter"] for item in needs})


class ProductSubsetPromotionTests(unittest.TestCase):
    def _fixture(self) -> tuple[dict, dict]:
        cards = [{"id": f"card_{index:04d}", "marketRank": index} for index in range(1, 763)]
        snapshot = {
            "schemaVersion": "2.0.0",
            "generation": {
                "id": "product_subset_20260804T000000Z",
                "generatedAt": "2026-08-04T00:00:00Z",
                "effectiveAt": "2026-08-04T00:00:00Z",
                "contentSha256": "",
                "qcReceiptSha256": "",
                "mode": "demo",
                "productionEligible": False,
                "blockers": ["product_subset_export", "awaiting_daddy_promote"],
            },
            "coverage": {
                "claim": "verified-top-n",
                "requestedCount": 100,
                "verifiedCount": 100,
                "top100Count": 100,
                "watchlistCount": 662,
                "completeIdentityCount": 762,
            },
            "top100": cards[:100],
            "watchlist": cards[100:],
        }
        snapshot["generation"]["contentSha256"] = operator.canonical_snapshot_sha256(snapshot)
        adapters = list(collect.CHECKPOINT_ADAPTERS)
        receipt = {
            "action": "pass",
            "productExport": {
                "cards": 762,
                "generationId": snapshot["generation"]["id"],
                "contentSha256": snapshot["generation"]["contentSha256"],
            },
            "universe": {"lockId": 42, "lockSha256": "a" * 64, "memberCount": 762},
            "refresh": {
                "ok": True,
                "results": [{"adapter": adapter, "ok": True} for adapter in adapters],
                "postFreshness": {
                    "polls": {adapter: {"slaOk": True} for adapter in adapters},
                },
            },
            "statusCounts": {
                "members": 762,
                "withExactSource": 762,
                "withPrice": 762,
                "withImage": 762,
                "frozenIdentity": 762,
                "frozenSource": 762,
                "frozenImage": 762,
                "productSubsetReady": 762,
                "gapCards": 0,
            },
            "candidateCounts": {"activeUniverse": 762, "newCandidates": 776},
            "gapCount": 0,
        }
        return snapshot, receipt

    def test_promotion_binds_exact_receipt_and_clears_only_staging_blockers(self) -> None:
        snapshot, receipt = self._fixture()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            snapshot_path = root / "snapshot.json"
            receipt_path = root / "receipt.json"
            output_path = root / "promoted.json"
            snapshot_path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
            receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
            receipt_sha = hashlib.sha256(receipt_path.read_bytes()).hexdigest()

            result = operator.cmd_promote_product_subset(
                snapshot_path=snapshot_path,
                receipt_path=receipt_path,
                output_path=output_path,
            )
            promoted = json.loads(output_path.read_text(encoding="utf-8"))

            self.assertEqual(result["cards"], 762)
            self.assertFalse(result["deployed"])
            self.assertTrue(promoted["generation"]["productionEligible"])
            self.assertEqual(promoted["generation"]["mode"], "production")
            self.assertEqual(promoted["generation"]["blockers"], [])
            self.assertEqual(promoted["generation"]["qcReceiptSha256"], receipt_sha)
            self.assertEqual(promoted["coverage"]["claim"], "verified-top-100")
            self.assertEqual(promoted["coverage"]["verifiedCount"], 762)
            self.assertEqual(
                promoted["generation"]["contentSha256"],
                operator.canonical_snapshot_sha256(promoted),
            )


if __name__ == "__main__":
    unittest.main()
