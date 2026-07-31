from __future__ import annotations

import hashlib
import io
import json
import shutil
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from pipelines import public_snapshot_qc as qc
from pipelines import verify_images as media


def webp_bytes(
    size: tuple[int, int] = (429, 600),
    color: tuple[int, int, int] = (20, 30, 40),
) -> bytes:
    output = io.BytesIO()
    Image.new("RGB", size, color).save(output, format="WEBP", lossless=True)
    return output.getvalue()


def metric(value: int | float | None, at: str | None, status: str = "ready") -> dict:
    return {"value": value, "status": status, "asOf": at}


class PublicSnapshotQcTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.assets = self.root / "assets"
        self.assets.mkdir()
        raw = webp_bytes()
        self.image_hash = hashlib.sha256(raw).hexdigest()
        (self.assets / f"{self.image_hash}.webp").write_bytes(raw)
        (self.assets / f"{self.image_hash}_200.webp").write_bytes(
            webp_bytes((200, 280), (30, 40, 50))
        )
        (self.assets / f"{self.image_hash}_600.webp").write_bytes(
            webp_bytes((429, 600), (40, 50, 60))
        )
        self.run_id = "daily_20260729T010000000000Z"
        self.db_qc_receipt = {
            "schemaVersion": 1,
            "runId": self.run_id,
            "asOf": "2026-07-29T00:30:00Z",
            "status": "passed",
            "readOnly": True,
            "database": "cardz_market_cap",
            "universeCandidateSha256": "a" * 64,
            "reportSha256": "b" * 64,
            "counts": {
                "qualified": 1,
                "monitoring": 0,
                "releaseReadyQualified": 1,
                "releaseBlockedQualified": 0,
            },
            "releaseGate": {
                "eligible": True,
                "blockerCount": 0,
                "blockerCardCount": 0,
                "blockers": {},
            },
            "reviewQueueCounts": {},
        }
        self.effective = "2026-07-29T00:00:00Z"
        printing_key = qc.printing_key(
            "pokemon",
            "Set",
            "001/100",
            "unlimited",
            "standard",
            "holofoil",
            language="en",
        )
        self.card = {
            "id": "cmc_0123456789abcdef01234567",
            "rank": 1,
            "marketRank": 1,
            "viewRank": 1,
            "tcg": "pokemon",
            "cardLanguage": "en",
            "identityStatus": "confirmed",
            "collectorNumber": {"display": "001/100", "normalized": "001/100", "complete": True},
            "printingIdentity": {
                "setName": "Set",
                "collectorNumber": "001/100",
                "editionCode": "unlimited",
                "parallelCode": "standard",
                "finishCode": "holofoil",
                "cardLanguage": "en",
                "canonicalPrintingSha256": qc.printing_key_sha256(printing_key),
                "evidenceSha256": "e" * 64,
            },
            "names": {"en": "Card", "zhTW": "卡", "zhCN": "卡", "ja": "カード"},
            "sets": {"en": "Set", "zhTW": "系列", "zhCN": "系列", "ja": "セット"},
            "stories": {"en": None, "zhTW": None, "zhCN": None, "ja": None},
            "image": {
                "src": f"/market-assets/{self.image_hash}.webp",
                "sha256": self.image_hash,
                "kind": "raw_front",
                "width": 429,
                "height": 600,
                "qcAt": "2026-07-28T00:00:00Z",
                "alt": {"en": "Card", "zhTW": "卡", "zhCN": "卡", "ja": "カード"},
                "variants": {
                    "200": f"/market-assets/{self.image_hash}_200.webp",
                    "600": f"/market-assets/{self.image_hash}_600.webp",
                },
            },
            "pricePsa10": metric(100, "2026-07-28T00:00:00Z"),
            "populationPsa10": {
                **metric(1000, "2026-07-28T00:00:00Z"),
                "estimated": False,
            },
            "marketCap": metric(100000, "2026-07-28T00:00:00Z"),
            "windows": {
                window: {
                    "changePct": metric(None, None, "accumulating"),
                    "marketCapChangePct": metric(None, None, "accumulating"),
                    "trackedSalesChangePct": metric(None, None, "accumulating"),
                    "trackedSales": {
                        "valueUsd": metric(100, self.effective),
                        "count": metric(10 if window == "30d" else 1, self.effective),
                        "coverage": "partial",
                        "asOf": self.effective,
                    },
                }
                for window in ("1d", "7d", "30d")
            },
            "graderPopulations": {
                grader: {
                    "topGrade": "10",
                    "total": {**metric(None, None, "unavailable"), "estimated": False},
                    "topGradePopulation": {
                        **(
                            metric(1000, "2026-07-28T00:00:00Z")
                            if grader == "PSA"
                            else metric(None, None, "unavailable")
                        ),
                        "estimated": False,
                    },
                    "topGradePopulationChangePct": {
                        window: metric(None, None, "accumulating")
                        for window in ("1d", "7d", "30d")
                    },
                }
                for grader in ("PSA", "BGS", "CGC", "SGC", "TAG")
            },
            "historyDaily": [],
        }
        self.snapshot = {
            "schemaVersion": "2.0.0",
            "generation": {
                "id": "candidate_1",
                "generatedAt": "2026-07-29T01:00:00Z",
                "effectiveAt": self.effective,
                "contentSha256": "",
                "qcReceiptSha256": "",
                "mode": "demo",
                "productionEligible": False,
                "blockers": ["strict_qc_receipt_missing"],
            },
            "universe": {
                "populationMin": 1000,
                "grade": "PSA 10",
                "rankingMetric": "psa10_market_cap_usd",
                "windows": ["1d", "7d", "30d"],
                "salesCoverage": "partial",
            },
            "coverage": {
                "claim": "verified-top-n",
                "requestedCount": 100,
                "verifiedCount": 0,
                "top100Count": 1,
                "watchlistCount": 0,
            },
            "currencies": {
                "base": "USD",
                "supported": ["USD", "HKD", "CNY", "GBP", "TWD", "JPY", "KRW"],
                "rates": {},
                "asOf": None,
            },
            "top100": [self.card],
            "watchlist": [],
        }
        self.snapshot["generation"]["contentSha256"] = qc.snapshot_content_sha256(self.snapshot)
        self.manifest = {
            "records": [
                {
                    "publicId": self.card["id"],
                    "contentSha256": self.image_hash,
                    "imageKind": "raw_front",
                    "publicAllowed": True,
                    "semanticMatchStatus": "human_or_vision_confirmed",
                    "cardNumberMatch": True,
                    "languageMatch": True,
                    "tcgMatch": True,
                    "resolverEvidence": {
                        "sourceContentSha256": "f" * 64,
                        "collectorMatch": True,
                        "languageMetadataMatch": True,
                        "tcgMetadataMatch": True,
                    },
                }
            ]
        }

    def audit(self) -> dict:
        receipt_bytes = qc.canonical_json_bytes(self.db_qc_receipt, pretty=True)
        return qc.audit_snapshot(
            self.snapshot,
            self.manifest,
            self.assets,
            db_qc_receipt=self.db_qc_receipt,
            db_qc_receipt_bytes=receipt_bytes,
            checked_at=datetime(2026, 7, 29, 2, tzinfo=timezone.utc),
            run_id=self.run_id,
        )

    def finalize(self, audit: dict) -> tuple[dict, bytes]:
        return qc.finalize_snapshot(
            self.snapshot,
            audit,
            self.assets,
            db_qc_receipt=self.db_qc_receipt,
            db_qc_receipt_bytes=qc.canonical_json_bytes(
                self.db_qc_receipt,
                pretty=True,
            ),
        )

    def test_passed_card_finalizes_to_generation_bound_verified_top_n(self) -> None:
        audit = self.audit()
        self.assertEqual(audit["status"], "passed")
        final, receipt_bytes = self.finalize(audit)
        receipt = json.loads(receipt_bytes)
        self.assertEqual(final["coverage"]["claim"], "verified-top-n")
        self.assertEqual(final["coverage"]["verifiedCount"], 1)
        self.assertTrue(final["generation"]["productionEligible"])
        self.assertEqual(final["generation"]["qcReceiptSha256"], hashlib.sha256(receipt_bytes).hexdigest())
        self.assertEqual(receipt["cards"][0]["id"], self.card["id"])
        self.assertEqual(receipt["runId"], self.run_id)
        self.assertEqual(
            receipt["dbQcReceiptSha256"],
            final["generation"]["dbQc"]["receiptSha256"],
        )
        self.assertEqual(receipt["universeCandidateSha256"], "a" * 64)
        self.assertEqual(
            receipt["snapshotContentSha256"],
            qc.receipt_snapshot_content_sha256(final),
        )
        self.assertEqual(set(receipt["cards"][0]["media"]), {"base", "200", "600"})
        self.assertEqual(receipt["cards"][0]["media"]["200"]["width"], 200)
        self.assertEqual(receipt["cards"][0]["media"]["200"]["height"], 280)
        self.assertEqual(len(receipt["media"]["assets"]), 3)
        self.assertEqual(final["generation"]["contentSha256"], qc.snapshot_content_sha256(final))

    def test_blocked_or_wrong_run_db_qc_receipt_is_rejected(self) -> None:
        self.db_qc_receipt["status"] = "failed"
        with self.assertRaisesRegex(qc.SnapshotQcError, "status is not passed"):
            self.audit()
        self.db_qc_receipt["status"] = "passed"
        self.db_qc_receipt["runId"] = "another_run"
        with self.assertRaisesRegex(qc.SnapshotQcError, "does not match"):
            self.audit()

    def test_wrong_qc_card_id_is_rejected(self) -> None:
        self.manifest["records"][0]["publicId"] = "cmc_deadbeefdeadbeefdeadbeef"
        audit = self.audit()
        self.assertIn("image_qc_card_id_mismatch", audit["cards"][0]["blockers"])

    def test_future_metric_is_rejected(self) -> None:
        self.card["windows"]["1d"]["changePct"] = metric(1, "2026-07-30T00:00:00Z")
        self.snapshot["generation"]["contentSha256"] = qc.snapshot_content_sha256(self.snapshot)
        audit = self.audit()
        self.assertTrue(any(value.startswith("future_metric:") for value in audit["cards"][0]["blockers"]))

    def test_missing_30d_sale_is_rejected(self) -> None:
        self.card["windows"]["30d"]["trackedSales"]["count"] = metric(0, self.effective)
        self.snapshot["generation"]["contentSha256"] = qc.snapshot_content_sha256(self.snapshot)
        self.assertIn("psa10_tracked_sales_30d_missing", self.audit()["cards"][0]["blockers"])

    def test_nine_30d_sales_are_rejected_as_low_liquidity(self) -> None:
        self.card["windows"]["30d"]["trackedSales"]["count"] = metric(9, self.effective)
        self.snapshot["generation"]["contentSha256"] = qc.snapshot_content_sha256(self.snapshot)
        self.assertIn(
            "psa10_tracked_sales_30d_insufficient",
            self.audit()["cards"][0]["blockers"],
        )

    def test_incomplete_printing_tuple_is_rejected_without_defaults(self) -> None:
        self.card["printingIdentity"]["finishCode"] = ""
        self.snapshot["generation"]["contentSha256"] = qc.snapshot_content_sha256(self.snapshot)
        self.assertIn(
            "canonical_printing_identity_incomplete",
            self.audit()["cards"][0]["blockers"],
        )

    def test_printing_identity_must_match_the_public_card(self) -> None:
        self.card["printingIdentity"]["setName"] = "Another Set"
        self.snapshot["generation"]["contentSha256"] = qc.snapshot_content_sha256(self.snapshot)
        blockers = self.audit()["cards"][0]["blockers"]
        self.assertIn("canonical_printing_identity_hash_mismatch", blockers)
        self.assertIn("canonical_printing_set_mismatch", blockers)

    def test_sample_image_scanner_failure_is_a_publish_blocker(self) -> None:
        with patch.object(
            qc,
            "assert_raw_bytes_not_sample",
            side_effect=qc.SampleImageRejected("deterministic SAMPLE fixture"),
        ):
            blockers = self.audit()["cards"][0]["blockers"]
        self.assertIn("image_sample_or_placeholder", blockers)

    def test_missing_resolver_evidence_is_a_publish_blocker(self) -> None:
        del self.manifest["records"][0]["resolverEvidence"]
        blockers = self.audit()["cards"][0]["blockers"]
        self.assertIn("image_resolver_evidence_missing", blockers)

    def test_missing_or_false_manifest_language_match_is_a_publish_blocker(self) -> None:
        for value in (False, None):
            with self.subTest(languageMatch=value):
                if value is None:
                    self.manifest["records"][0].pop("languageMatch")
                else:
                    self.manifest["records"][0]["languageMatch"] = value
                blockers = self.audit()["cards"][0]["blockers"]
                self.assertIn("image_languageMatch_failed", blockers)

    def test_cross_tcg_duplicate_image_rejects_both_cards(self) -> None:
        other = json.loads(json.dumps(self.card))
        other["id"] = "cmc_fedcba9876543210fedcba98"
        other["rank"] = other["marketRank"] = other["viewRank"] = 2
        other["tcg"] = "one-piece"
        self.snapshot["top100"].append(other)
        self.manifest["records"].append(
            {**self.manifest["records"][0], "publicId": other["id"]}
        )
        self.snapshot["generation"]["contentSha256"] = qc.snapshot_content_sha256(self.snapshot)
        audit = self.audit()
        self.assertTrue(
            all("cross_tcg_duplicate_image" in row["blockers"] for row in audit["cards"])
        )

    def test_failed_audit_with_no_approved_card_cannot_finalize(self) -> None:
        self.card["windows"]["30d"]["trackedSales"]["count"] = metric(None, None, "unavailable")
        self.snapshot["generation"]["contentSha256"] = qc.snapshot_content_sha256(self.snapshot)
        with self.assertRaisesRegex(qc.SnapshotQcError, "status is not passed"):
            self.finalize(self.audit())

    def test_tampered_passed_evidence_cannot_finalize(self) -> None:
        audit = self.audit()
        audit["cards"][0]["evidenceSha256"] = "0" * 64
        with self.assertRaisesRegex(qc.SnapshotQcError, "evidence hash is invalid"):
            self.finalize(audit)

    def test_missing_audit_decision_cannot_finalize(self) -> None:
        audit = self.audit()
        audit["cards"] = []
        with self.assertRaisesRegex(qc.SnapshotQcError, "exactly cover"):
            self.finalize(audit)

    def test_verified_count_must_equal_passed_decisions(self) -> None:
        audit = self.audit()
        audit["verifiedCount"] = 0
        with self.assertRaisesRegex(qc.SnapshotQcError, "verifiedCount"):
            self.finalize(audit)

    def test_audit_envelope_must_be_passed_and_blocker_free(self) -> None:
        for field, value, message in (
            ("schemaVersion", 2, "schema"),
            ("status", "failed", "status"),
            ("checkedAt", "not-a-time", "checkedAt"),
            ("blockers", ["future_metric"], "global blockers"),
        ):
            with self.subTest(field=field):
                audit = self.audit()
                audit[field] = value
                with self.assertRaisesRegex(qc.SnapshotQcError, message):
                    self.finalize(audit)


class ImageMediaClassificationTests(unittest.TestCase):
    """A08 image-media-domain: terminal classify + policy hash binding."""

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.assets = self.root / "assets"
        self.assets.mkdir()
        raw = webp_bytes()
        self.image_hash = hashlib.sha256(raw).hexdigest()
        (self.assets / f"{self.image_hash}.webp").write_bytes(raw)
        (self.assets / f"{self.image_hash}_200.webp").write_bytes(
            webp_bytes((200, 280), (30, 40, 50))
        )
        (self.assets / f"{self.image_hash}_600.webp").write_bytes(
            webp_bytes((429, 600), (40, 50, 60))
        )
        self.policy_hash = media.image_policy_sha256()
        self.record = {
            "publicId": "cmc_0123456789abcdef01234567",
            "contentSha256": self.image_hash,
            "imageKind": "raw_front",
            "publicAllowed": True,
            "semanticMatchStatus": "human_or_vision_confirmed",
            "cardNumberMatch": True,
            "tcgMatch": True,
            "languageMatch": True,
            "qcVersion": "raw-front-v4",
            "qcAt": "2026-07-29T00:00:00Z",
            "resolverEvidence": {
                "sourceContentSha256": "f" * 64,
                "collectorMatch": True,
                "languageMetadataMatch": True,
                "tcgMetadataMatch": True,
            },
        }

    def test_image_policy_hash_is_stable_and_bound(self) -> None:
        first = media.image_policy_sha256()
        second = media.image_policy_sha256(media.APPROVED_IMAGE_POLICY)
        self.assertEqual(first, second)
        self.assertRegex(first, r"^[0-9a-f]{64}$")
        decision = media.classify_card_media(
            card_id=self.record["publicId"],
            tcg="pokemon",
            content_sha256=self.image_hash,
            manifest_record=self.record,
            assets_root=self.assets,
            scan_sample_if_public_path=False,
        )
        self.assertEqual(decision["imagePolicySha256"], first)
        self.assertRegex(decision["cardIdentitySha256"], r"^[0-9a-f]{64}$")
        self.assertRegex(decision["decisionSha256"], r"^[0-9a-f]{64}$")

    def test_public_candidate_binds_identity_and_excludes_sample_cross_card(self) -> None:
        decision = media.classify_card_media(
            card_id=self.record["publicId"],
            tcg="pokemon",
            content_sha256=self.image_hash,
            identity={"collectorNumber": "001/100"},
            manifest_record=self.record,
            assets_root=self.assets,
            scan_sample_if_public_path=False,
        )
        self.assertEqual(decision["terminalStatus"], media.PUBLIC_TERMINAL)
        self.assertTrue(decision["publicCandidate"])
        self.assertEqual(decision["imagePolicySha256"], self.policy_hash)
        self.assertEqual(decision["cardId"], self.record["publicId"])
        self.assertFalse(decision["duplicates"]["crossCard"])
        self.assertNotEqual(decision["sample"]["status"], "rejected")

    def test_cross_card_duplicate_never_public(self) -> None:
        owners = {
            self.image_hash: [
                (self.record["publicId"], "pokemon"),
                ("cmc_fedcba9876543210fedcba98", "one-piece"),
            ]
        }
        decision = media.classify_card_media(
            card_id=self.record["publicId"],
            tcg="pokemon",
            content_sha256=self.image_hash,
            manifest_record=self.record,
            image_owners=owners,
            assets_root=self.assets,
            scan_sample_if_public_path=False,
        )
        self.assertEqual(decision["terminalStatus"], "reject_cross_tcg_duplicate")
        self.assertFalse(decision["publicCandidate"])
        self.assertIn("cross_tcg_duplicate_image", decision["reasonCodes"])

    def test_sample_rejection_never_public(self) -> None:
        with patch.object(
            media,
            "_scan_sample",
            return_value=("rejected", "deterministic SAMPLE fixture"),
        ):
            decision = media.classify_card_media(
                card_id=self.record["publicId"],
                tcg="pokemon",
                content_sha256=self.image_hash,
                manifest_record=self.record,
                assets_root=self.assets,
                scan_sample_if_public_path=True,
            )
        self.assertEqual(decision["terminalStatus"], "reject_sample_or_placeholder")
        self.assertFalse(decision["publicCandidate"])
        self.assertIn("image_sample_or_placeholder", decision["reasonCodes"])

    def test_semantic_unconfirmed_enters_review_queue_not_public(self) -> None:
        record = {
            **self.record,
            "semanticMatchStatus": "metadata_exact_unreviewed",
            "publicAllowed": False,
            "resolverEvidence": {"method": "backfill_qc", "sourceRef": None},
        }
        decision = media.classify_card_media(
            card_id=self.record["publicId"],
            tcg="pokemon",
            content_sha256=self.image_hash,
            manifest_record=record,
            assets_root=self.assets,
            scan_sample_if_public_path=False,
        )
        self.assertFalse(decision["publicCandidate"])
        self.assertIn(decision["terminalStatus"], {
            "reject_semantic_unconfirmed",
            "reject_resolver_evidence_incomplete",
            "reject_not_public_allowed",
        })
        self.assertTrue(decision["reviewReasons"] or decision["rightsFlags"])

    def test_cohort_public_candidates_invariant(self) -> None:
        other_hash = hashlib.sha256(webp_bytes(color=(9, 9, 9))).hexdigest()
        cards = [
            {
                "id": self.record["publicId"],
                "tcg": "pokemon",
                "imageSha256": self.image_hash,
            },
            {
                "id": "cmc_aaaaaaaaaaaaaaaaaaaaaaaa",
                "tcg": "pokemon",
                "imageSha256": other_hash,
            },
            {
                "id": "cmc_bbbbbbbbbbbbbbbbbbbbbbbb",
                "tcg": "one-piece",
                "imageSha256": other_hash,  # cross-card / cross-tcg
            },
        ]
        result = media.classify_cohort_media(
            cards,
            manifest_records=[self.record],
            assets_root=self.assets,
            scan_sample_if_public_path=False,
        )
        self.assertEqual(result["cardCount"], 3)
        self.assertTrue(result["publicCandidatesZeroSampleAndCrossCard"])
        self.assertEqual(result["imagePolicySha256"], self.policy_hash)
        for row in result["publicCandidates"]:
            self.assertEqual(row["imagePolicySha256"], self.policy_hash)
            self.assertTrue(row["cardIdentitySha256"])
            self.assertFalse(row["duplicates"]["crossCard"])
            self.assertFalse(row["duplicates"]["crossTcg"])
            self.assertNotEqual(row["sample"]["status"], "rejected")
        # duplicate pair must not be public
        dup_public = [
            row
            for row in result["publicCandidates"]
            if row["contentSha256"] == other_hash
        ]
        self.assertEqual(dup_public, [])
        self.assertGreaterEqual(result["reviewQueueCount"], 2)

    def test_meta_unreviewed_never_evidence_promote_eligible(self) -> None:
        record = {
            **self.record,
            "semanticMatchStatus": "meta_unreviewed",
            "publicAllowed": True,
        }
        blockers = media.evidence_promote_blockers(
            record,
            hash_owners={self.image_hash: {self.record["publicId"]}},
            assets_root=self.assets,
        )
        self.assertTrue(any(b.startswith("semantic_not_evidence_grade") for b in blockers))

    def test_source_id_exact_eligible_when_flags_and_asset_ok(self) -> None:
        record = {
            **self.record,
            "semanticMatchStatus": "source_id_exact",
            "rawFrontConfirmed": True,
        }
        blockers = media.evidence_promote_blockers(
            record,
            hash_owners={self.image_hash: {self.record["publicId"]}},
            assets_root=self.assets,
        )
        self.assertEqual(blockers, [])

    def test_source_id_exact_blocked_when_tcg_match_missing(self) -> None:
        record = {
            **self.record,
            "semanticMatchStatus": "source_id_exact",
            "tcgMatch": False,
            "rawFrontConfirmed": True,
        }
        blockers = media.evidence_promote_blockers(
            record,
            hash_owners={self.image_hash: {self.record["publicId"]}},
            assets_root=self.assets,
        )
        self.assertIn("tcg_match_failed", blockers)

    def test_rebind_selects_bound_hash_when_report_hash_unbound(self) -> None:
        other_hash = hashlib.sha256(webp_bytes(color=(1, 2, 3))).hexdigest()
        (self.assets / f"{other_hash}.webp").write_bytes(webp_bytes(color=(1, 2, 3)))
        bound = {
            **self.record,
            "contentSha256": other_hash,
            "semanticMatchStatus": "snk_item_exact",
            "rawFrontConfirmed": True,
        }
        cards = [
            {
                "id": self.record["publicId"],
                "tcg": "pokemon",
                "imageSha256": self.image_hash,  # unbound in records
            }
        ]
        result = media.classify_cohort_media_with_rebind(
            cards,
            manifest_records=[bound],
            assets_root=self.assets,
            scan_sample_if_public_path=False,
        )
        self.assertEqual(result["cardCount"], 1)
        self.assertEqual(result["rebindCount"], 1)
        decision = result["decisions"][0]
        self.assertEqual(decision["contentSha256"], other_hash)
        self.assertTrue(decision["binding"]["rebound"])

    def test_cross_card_hash_blocked_from_evidence_promote(self) -> None:
        record = {
            **self.record,
            "semanticMatchStatus": "snk_item_exact",
            "rawFrontConfirmed": True,
        }
        blockers = media.evidence_promote_blockers(
            record,
            hash_owners={
                self.image_hash: {
                    self.record["publicId"],
                    "cmc_othercard00000000000001",
                }
            },
            assets_root=self.assets,
        )
        self.assertIn("cross_card_duplicate_hash", blockers)


if __name__ == "__main__":
    unittest.main()
