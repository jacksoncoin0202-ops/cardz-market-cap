from __future__ import annotations

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from render_project_state import render  # noqa: E402


class ProjectStateRendererTests(unittest.TestCase):
    def test_render_uses_machine_status_and_never_claims_blocked_release(self) -> None:
        document = render(
            {
                "status": "blocked",
                "readOnly": True,
                "elapsedMs": 140,
                "targetMs": 5000,
                "database": {
                    "authority": "canonical_mysql",
                    "name": "cardz_market_cap",
                    "connected": True,
                },
                "universeIntegrity": {
                    "status": "blocked",
                    "candidateQualified": 7,
                },
                "qc": {"status": "available", "imageChecksRejected": 3},
                "pending": {"identityReviews": 9},
                "generation": {
                    "generationId": "candidate_1",
                    "productionEligible": False,
                },
                "releaseGate": {
                    "eligible": False,
                    "blockers": ["printing_identity_incomplete"],
                },
            },
            as_of=datetime(2026, 7, 29, 8, 0, tzinfo=timezone.utc),
        )
        self.assertIn("asOf: **2026-07-29T08:00:00Z**", document)
        self.assertIn("Status: **BLOCKED**", document)
        self.assertIn("`printing_identity_incomplete`", document)
        self.assertIn("| `candidateQualified` | 7 |", document)
        self.assertNotIn("FE Live 100%", document)


if __name__ == "__main__":
    unittest.main()
