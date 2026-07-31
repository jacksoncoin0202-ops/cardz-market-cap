from __future__ import annotations

import contextlib
import hashlib
import io
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import bake_publish_pack as bake  # noqa: E402
import db_fill_until_green as diagnostics  # noqa: E402


def webp_bytes(width: int, height: int) -> bytes:
    output = io.BytesIO()
    Image.new("RGBA", (width, height), (20, 40, 60, 255)).save(
        output,
        format="WEBP",
        lossless=True,
    )
    return output.getvalue()


class PublicationContainmentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.temp, ignore_errors=True)
        self.assets = self.temp / "assets"
        self.assets.mkdir()
        base = webp_bytes(40, 56)
        self.image_hash = hashlib.sha256(base).hexdigest()
        (self.assets / f"{self.image_hash}.webp").write_bytes(base)
        (self.assets / f"{self.image_hash}_200.webp").write_bytes(webp_bytes(20, 28))
        (self.assets / f"{self.image_hash}_600.webp").write_bytes(webp_bytes(60, 84))
        self.snapshot_path = self.temp / "snapshot.json"
        self.receipt_path = self.temp / "qc-receipt.json"
        self.snapshot, self.receipt = self.write_valid_inputs()

    def write_valid_inputs(self) -> tuple[dict, dict]:
        card_id = "cmc_0123456789abcdef01234567"
        checked_at = "2026-07-29T01:00:00Z"
        printing_key = "|".join(
            ("pokemon", "set", "001/100", "unlimited", "standard", "holofoil")
        )
        receipt = {
            "schemaVersion": 1,
            "generationId": "candidate_20260729",
            "checkedAt": checked_at,
            "status": "passed",
            "claim": "verified-top-n",
            "requestedCount": 100,
            "verifiedCount": 1,
            "cards": [
                {
                    "id": card_id,
                    "imageSha256": self.image_hash,
                    "decision": "passed",
                    "evidenceSha256": "e" * 64,
                }
            ],
            "blockers": [],
        }
        receipt_bytes = (
            json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        ).encode("utf-8")
        self.receipt_path.write_bytes(receipt_bytes)
        snapshot = {
            "schemaVersion": "2.0.0",
            "generation": {
                "id": "candidate_20260729",
                "generatedAt": "2026-07-29T00:30:00Z",
                "effectiveAt": "2026-07-29T00:00:00Z",
                "contentSha256": "",
                "qcReceiptSha256": hashlib.sha256(receipt_bytes).hexdigest(),
                "mode": "production",
                "productionEligible": True,
                "blockers": [],
            },
            "coverage": {
                "claim": "verified-top-n",
                "requestedCount": 100,
                "verifiedCount": 1,
                "top100Count": 1,
                "watchlistCount": 0,
            },
            "top100": [
                {
                    "id": card_id,
                    "rank": 1,
                    "marketRank": 1,
                    "viewRank": 1,
                    "tcg": "pokemon",
                    "sets": {"en": "Set", "zhTW": "系列", "zhCN": "系列", "ja": "セット"},
                    "collectorNumber": {
                        "display": "001/100",
                        "normalized": "001/100",
                        "complete": True,
                    },
                    "printingIdentity": {
                        "setName": "Set",
                        "collectorNumber": "001/100",
                        "editionCode": "unlimited",
                        "parallelCode": "standard",
                        "finishCode": "holofoil",
                        "canonicalPrintingSha256": hashlib.sha256(
                            printing_key.encode("utf-8")
                        ).hexdigest(),
                        "evidenceSha256": "f" * 64,
                    },
                    "populationPsa10": {
                        "value": 1000,
                        "status": "ready",
                        "asOf": "2026-07-29T00:00:00Z",
                        "estimated": False,
                    },
                    "pricePsa10": {
                        "value": 100,
                        "status": "ready",
                        "asOf": "2026-07-29T00:00:00Z",
                    },
                    "marketCap": {
                        "value": 100000,
                        "status": "ready",
                        "asOf": "2026-07-29T00:00:00Z",
                    },
                    "windows": {
                        "1d": {},
                        "7d": {},
                        "30d": {
                            "trackedSales": {
                                "coverage": "partial",
                                "count": {
                                    "value": 1,
                                    "status": "ready",
                                    "asOf": "2026-07-29T00:00:00Z",
                                },
                                "valueUsd": {
                                    "value": 100,
                                    "status": "ready",
                                    "asOf": "2026-07-29T00:00:00Z",
                                },
                            }
                        },
                    },
                    "image": {
                        "kind": "raw_front",
                        "sha256": self.image_hash,
                        "src": f"/market-assets/{self.image_hash}.webp",
                        "variants": {
                            "200": f"/market-assets/{self.image_hash}_200.webp",
                            "600": f"/market-assets/{self.image_hash}_600.webp",
                        },
                    },
                }
            ],
            "watchlist": [],
        }
        snapshot["generation"]["contentSha256"] = bake.canonical_snapshot_sha256(snapshot)
        self.snapshot_path.write_text(
            json.dumps(snapshot, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return snapshot, receipt

    def invoke_bake(self, out: Path) -> tuple[int, dict]:
        stream = io.StringIO()
        with contextlib.redirect_stdout(stream):
            code = bake.main(
                [
                    "--snapshot",
                    str(self.snapshot_path),
                    "--qc-receipt",
                    str(self.receipt_path),
                    "--assets-root",
                    str(self.assets),
                    "--out-root",
                    str(out),
                ]
            )
        return code, json.loads(stream.getvalue())

    def test_bake_builds_only_an_immutable_candidate(self) -> None:
        out = self.temp / "out"
        code, result = self.invoke_bake(out)
        self.assertEqual(code, 0)
        self.assertTrue(result["built"])
        self.assertTrue(result["productionEligible"])
        self.assertFalse(result["pointerPromoted"])
        self.assertNotIn("feSetComplete", json.dumps(result))
        candidate = out / "candidate_20260729"
        self.assertEqual((candidate / "snapshot.json").read_bytes(), self.snapshot_path.read_bytes())
        self.assertEqual((candidate / "qc-receipt.json").read_bytes(), self.receipt_path.read_bytes())
        self.assertEqual(len(list((candidate / "assets").glob("*.webp"))), 3)
        self.assertFalse((out / "latest.json").exists())
        self.assertFalse((candidate / "latest.json").exists())

        before = {
            path.relative_to(candidate).as_posix(): path.read_bytes()
            for path in candidate.rglob("*")
            if path.is_file()
        }
        second_code, second_result = self.invoke_bake(out)
        after = {
            path.relative_to(candidate).as_posix(): path.read_bytes()
            for path in candidate.rglob("*")
            if path.is_file()
        }
        self.assertEqual(second_code, 0)
        self.assertEqual(second_result, result)
        self.assertEqual(after, before)

    def test_blocked_bake_preserves_an_existing_pointer_byte_for_byte(self) -> None:
        snapshot = json.loads(self.snapshot_path.read_text(encoding="utf-8"))
        snapshot["generation"]["qcReceiptSha256"] = "0" * 64
        snapshot["generation"]["contentSha256"] = bake.canonical_snapshot_sha256(snapshot)
        self.snapshot_path.write_text(json.dumps(snapshot), encoding="utf-8")
        out = self.temp / "blocked"
        out.mkdir()
        sentinel = b'{"generationId":"last-good"}\n'
        (out / "latest.json").write_bytes(sentinel)

        code, result = self.invoke_bake(out)
        self.assertEqual(code, 2)
        self.assertFalse(result["built"])
        self.assertFalse(result["productionEligible"])
        self.assertIn("generation_qc_receipt_sha256_mismatch", result["blockers"])
        self.assertEqual((out / "latest.json").read_bytes(), sentinel)
        self.assertFalse((out / "candidate_20260729").exists())
        self.assertEqual(len(list((out / "failed").rglob("candidate-report.json"))), 1)

    def test_bake_rejects_missing_canonical_printing_identity(self) -> None:
        snapshot = json.loads(self.snapshot_path.read_text(encoding="utf-8"))
        del snapshot["top100"][0]["printingIdentity"]
        snapshot["generation"]["contentSha256"] = bake.canonical_snapshot_sha256(snapshot)
        self.snapshot_path.write_text(json.dumps(snapshot), encoding="utf-8")

        code, result = self.invoke_bake(self.temp / "identity-blocked")
        self.assertEqual(code, 2)
        self.assertIn(
            "public_cards[0].printing_identity_missing",
            result["blockers"],
        )

    def test_diagnostics_is_read_only_and_green_requires_the_real_gate(self) -> None:
        before = {
            path.relative_to(self.temp).as_posix(): path.read_bytes()
            for path in self.temp.rglob("*")
            if path.is_file()
        }
        stream = io.StringIO()
        with contextlib.redirect_stdout(stream):
            code = diagnostics.main(
                [
                    "--snapshot",
                    str(self.snapshot_path),
                    "--qc-receipt",
                    str(self.receipt_path),
                    "--assets-root",
                    str(self.assets),
                ]
            )
        report = json.loads(stream.getvalue())
        after = {
            path.relative_to(self.temp).as_posix(): path.read_bytes()
            for path in self.temp.rglob("*")
            if path.is_file()
        }
        self.assertEqual(code, 0)
        self.assertEqual(report["status"], "green")
        self.assertTrue(report["readOnly"])
        self.assertEqual(report["publication"]["pointerAction"], "none")
        self.assertEqual(after, before)

        snapshot = json.loads(self.snapshot_path.read_text(encoding="utf-8"))
        snapshot["top100"][0]["windows"]["30d"]["trackedSales"]["count"]["value"] = 0
        snapshot["generation"]["contentSha256"] = bake.canonical_snapshot_sha256(snapshot)
        self.snapshot_path.write_text(json.dumps(snapshot), encoding="utf-8")
        blocked = io.StringIO()
        with contextlib.redirect_stdout(blocked):
            blocked_code = diagnostics.main(
                [
                    "--snapshot",
                    str(self.snapshot_path),
                    "--qc-receipt",
                    str(self.receipt_path),
                    "--assets-root",
                    str(self.assets),
                ]
            )
        blocked_report = json.loads(blocked.getvalue())
        self.assertEqual(blocked_code, 2)
        self.assertEqual(blocked_report["status"], "blocked")
        self.assertIn("top100[0].tracked_sales_30d_unqualified", blocked_report["qc"]["blockers"])

    def test_diagnostics_source_contains_no_mutating_pipeline(self) -> None:
        source = (SCRIPTS / "db_fill_until_green.py").read_text(encoding="utf-8")
        for forbidden in (
            "subprocess",
            "--write",
            "latest.json",
            "seed-snapshot",
            "local-serve",
            "write_text",
            "write_bytes",
        ):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
