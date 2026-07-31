from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from pipelines import quarantine_wrong_pricecharting_bindings as quarantine


class WrongPriceChartingBindingQuarantineTests(unittest.TestCase):
    def test_proven_bad_allowlist_is_closed(self) -> None:
        self.assertEqual(len(quarantine.PROVEN_BAD), 16)
        self.assertEqual(quarantine.PROVEN_BAD[906][0], "762776")
        self.assertEqual(quarantine.PROVEN_BAD[1253][0], "8508378")
        rows = [
            {"variantId": variant_id, "pcProductId": product}
            for variant_id, (product, _reason) in quarantine.PROVEN_BAD.items()
        ]
        quarantine.validate_allowed(rows)
        with self.assertRaises(ValueError):
            quarantine.validate_allowed(rows[:-1])

    def test_plan_hash_rejects_tampering(self) -> None:
        rows = []
        for variant_id, (product, reason) in quarantine.PROVEN_BAD.items():
            claim = {
                "variantId": variant_id,
                "pcProductId": product,
                "reasonCode": reason,
            }
            rows.append(
                {
                    "variantId": variant_id,
                    "pcProductId": product,
                    "conflictClaim": claim,
                    "conflictEvidenceSha256": quarantine.sha256(claim),
                }
            )
        document = {
            "contract": quarantine.CONTRACT,
            "readOnly": True,
            "rows": rows,
        }
        document["planSha256"] = quarantine.plan_sha256(document)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "plan.json"
            path.write_text(json.dumps(document), encoding="utf-8")
            quarantine.read_plan(path, document["planSha256"])
            document["rows"][0]["conflictClaim"]["reasonCode"] = "tampered"
            path.write_text(json.dumps(document), encoding="utf-8")
            with self.assertRaises(ValueError):
                quarantine.read_plan(path, document["planSha256"])


if __name__ == "__main__":
    unittest.main()
