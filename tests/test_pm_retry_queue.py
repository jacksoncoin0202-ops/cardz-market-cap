from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from pipelines.pm_retry_queue import (
    SALES_POLICY,
    _canonical_release_items,
    build_queue,
    write_report,
)


class PmRetryQueueTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _write(self, name: str, value: object, *, jsonl: bool = False) -> Path:
        path = self.root / name
        if jsonl:
            path.write_text("\n".join(json.dumps(row) for row in value) + "\n", encoding="utf-8")
        else:
            path.write_text(json.dumps(value), encoding="utf-8")
        return path

    def test_builds_deduplicated_private_queue_with_language_and_sales_guards(self) -> None:
        geometry = self._write("geometry.json", {
            "exactInputCount": 7,
            "sourceLedger": "data/runtime/private-reports/pm-image-remediation-20260731/geometry/v2/geometry-remediation-ledger-v2.json",
            "records": [
                *[{"status": "success"} for _ in range(5)],
                *[{"status": "unresolved", "variantId": index, "assetId": 1000 + index, "expectedLanguage": "ja", "tcg": "one-piece", "unresolvedReason": "fill_too_small"} for index in range(1, 3)],
            ],
        })
        def target(variant: int, language: str) -> dict[str, object]:
            return {"variantId": variant, "target": {"assetId": variant + 10, "language": language, "game": "one-piece"}, "attemptCount": 1, "decisionReason": "test", "detailArtifact": "receipt.json", "selectedCandidate": {"source": "snkrdunk", "sourceId": str(variant)}}
        limitless = self._write("limitless.json", {
            "counts": {"ready": 13, "reject": 15, "review": 10, "total": 146, "unresolved": 108},
            "ready": [target(index, "en" if index <= 130 else "ja") for index in range(1, 14)],
            "reject": [target(index, "en" if index <= 130 else "ja") for index in range(14, 29)],
            "review": [target(index, "en" if index <= 130 else "ja") for index in range(29, 39)],
            "unresolved": [target(index, "en" if index <= 130 else "ja") for index in range(39, 147)],
        })
        quarantine = self._write("quarantine.json", {
            "action": "image-source-family-quarantine",
            "family": "limitless-one-piece-en",
            "mode": "write",
            "transaction": "committed",
            "qcInserted": 320,
            "pointersDisabled": 320,
            "matched": 320,
            "releaseCohortOverlap": 146,
            "outerNonRelease": 174,
            "assets": [
                {"variantId": index, "assetId": 5000 + index, "contentSha256": f"{index:064x}"}
                for index in range(1, 321)
            ],
        })
        outer = self._write("outer.json", [
            {"variantId": index, "language": "ja", "tcg": "pokemon", "printingIdentityStatus": "canonical" if index == 2001 else "", "opaqueId": f"card-{index}", "exactSourceIdentities": []}
            for index in range(2001, 2245)
        ])
        price_sales = self._write("price-sales.jsonl", [
            {"variantId": index, "state": "review" if index <= 204 else "unresolved", "tcg": "pokemon", "language": "en", "nextAction": "fetch_exact_psa10", "priceGapReasons": [], "salesGapReasons": ["psa10_sales_30d_missing"], "exactBindings": {}}
            for index in range(1, 379)
        ], jsonl=True)

        queue = build_queue(geometry_path=geometry, limitless_path=limitless, quarantine_path=quarantine, outer_missing_path=outer, price_sales_path=price_sales, generated_at="2026-07-31T00:00:00Z")
        self.assertEqual(queue["count"], 945)
        self.assertEqual(queue["byCohort"], {"all": 1, "outer": 418, "release": 526})
        self.assertEqual(queue["byCategory"], {"image_geometry": 2, "image_raw_front_missing": 244, "image_source_family": 1, "image_source_family_child": 320, "psa10_price_sales_gap": 378})
        self.assertEqual(queue["salesPolicy"], SALES_POLICY)
        self.assertEqual(len({item["itemKey"] for item in queue["items"]}), 945)
        limitless_ja = [item for item in queue["items"] if item["category"] == "image_source_family_child" and item["requiredLanguage"] == "ja"]
        self.assertEqual(len(limitless_ja), 16)
        self.assertTrue(all(item["languageGuard"] == "ja_requires_ja_replacement" for item in limitless_ja))
        self.assertEqual(sum(item["status"] == "blocked_missing_printing_identity" for item in queue["items"]), 243)

        output = self.root / "out"
        first = write_report(queue, output_dir=output)
        first_queue = (output / "agent-retry-queue.json").read_bytes()
        second = write_report(queue, output_dir=output)
        self.assertEqual(first, second)
        self.assertEqual(first_queue, (output / "agent-retry-queue.json").read_bytes())
        self.assertEqual(json.loads((output / "agent-retry-queue.json").read_text(encoding="utf-8"))["count"], 945)

    def test_rejects_a_quarantine_receipt_that_cannot_prove_the_320_family(self) -> None:
        geometry = self._write("geometry.json", {"exactInputCount": 7, "sourceLedger": "data/runtime/private-reports/pm-image-remediation-20260731/geometry/v2/geometry-remediation-ledger-v2.json", "records": [{"status": "success"}] * 5 + [{"status": "unresolved", "variantId": i, "assetId": i} for i in range(1, 3)]})
        limitless = self._write("limitless.json", {"counts": {"ready": 13, "reject": 15, "review": 10, "total": 146, "unresolved": 108}, "ready": [ {"variantId": i, "target": {"language": "en"}} for i in range(1,14)], "reject": [{"variantId": i, "target": {"language": "en"}} for i in range(14,29)], "review": [{"variantId": i, "target": {"language": "en"}} for i in range(29,39)], "unresolved": [{"variantId": i, "target": {"language": "en"}} for i in range(39,147)]})
        bad = self._write("bad-quarantine.json", {"action": "image-source-family-quarantine", "family": "limitless-one-piece-en", "mode": "write", "transaction": "committed", "qcInserted": 320, "pointersDisabled": 320, "matched": 146, "releaseCohortOverlap": 146, "outerNonRelease": 0, "assets": []})
        outer = self._write("outer.json", [{"variantId": i, "printingIdentityStatus": ""} for i in range(2001,2245)])
        gaps = self._write("gaps.jsonl", [{"variantId": i, "state": "review" if i <= 204 else "unresolved"} for i in range(1,379)], jsonl=True)
        with self.assertRaisesRegex(ValueError, "320 = 146 release"):
            build_queue(geometry_path=geometry, limitless_path=limitless, quarantine_path=bad, outer_missing_path=outer, price_sales_path=gaps)

    def test_canonical_qc_adds_one_durable_item_per_blocked_release_card(self) -> None:
        report = {
            "readOnly": True,
            "database": {"authority": "canonical_mysql", "name": "cardz_market_cap"},
            "counts": {
                "catalog": 2,
                "releaseBlockedQualified": 1,
                "releaseReadyQualified": 1,
            },
            "releaseGate": {"blockerCardCount": 1},
            "cards": [
                {
                    "variantId": 41,
                    "id": "cmc_blocked",
                    "decision": "failed",
                    "blockers": [
                        "image_language_match_failed",
                        "image_source_known_sample",
                    ],
                    "facts": {
                        "identity": {
                            "tcg": "one-piece",
                            "cardLanguage": "ja",
                            "printingSha256": "a" * 64,
                        },
                    },
                    "evidenceSha256": "b" * 64,
                },
                {
                    "variantId": 42,
                    "id": "cmc_ready",
                    "decision": "passed",
                    "blockers": [],
                },
            ],
        }
        items = _canonical_release_items(
            report,
            {"path": "qc/report.json", "sha256": "c" * 64},
        )
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["itemKey"], "retry:canonical_release_blocker:variant:41")
        self.assertEqual(items[0]["requiredLanguage"], "ja")
        self.assertEqual(
            items[0]["nextAction"],
            "replace_with_exact_authoritative_raw_front",
        )
        self.assertEqual(
            items[0]["metadata"]["blockers"],
            ["image_language_match_failed", "image_source_known_sample"],
        )


if __name__ == "__main__":
    unittest.main()
