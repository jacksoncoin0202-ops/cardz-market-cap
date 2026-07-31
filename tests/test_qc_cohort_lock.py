from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

from qc_cohort_lock import load_staging_plan  # noqa: E402


class QcCohortLockTests(unittest.TestCase):
    def test_receipt_bound_plan_is_staging_only_and_idempotent(self) -> None:
        candidate = "a" * 64
        report = {
            "runId": "qc-fixture",
            "universe": {"schemaVersion": "qc-discovery-v1", "candidateSha256": candidate, "qualified": 2},
            "cards": [
                {"variantId": 9, "marketRank": 2, "evidenceSha256": "e" * 64, "decision": "failed", "blockers": ["image"]},
                {"variantId": 3, "marketRank": None, "evidenceSha256": "f" * 64, "decision": "failed", "blockers": ["price"]},
            ],
        }
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "report.json"
            raw = json.dumps(report, sort_keys=True).encode("utf-8")
            path.write_bytes(raw)
            path.with_name("receipt.json").write_text(json.dumps({"universeCandidateSha256": candidate, "reportSha256": hashlib.sha256(raw).hexdigest()}), encoding="utf-8")
            first = load_staging_plan(path, candidate)
            second = load_staging_plan(path, candidate)
        self.assertEqual(first["lockSha256"], second["lockSha256"])
        self.assertEqual(first["policy"]["mode"], "qc_discovery_staging")
        self.assertFalse(first["policy"]["releaseEligible"])
        self.assertEqual([row["variantId"] for row in first["members"]], [3, 9])


if __name__ == "__main__":
    unittest.main()
