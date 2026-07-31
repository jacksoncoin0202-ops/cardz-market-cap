from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class LiveDataPolicyTests(unittest.TestCase):
    def test_handoff_contains_only_the_current_wsl_incremental_route(self) -> None:
        handoff = (ROOT / "docs/AGENT_HANDOFF_INCREMENTAL.md").read_text(
            encoding="utf-8"
        )
        for obsolete in (
            "g10_kline_price_bridge",
            "g10_sales_cache_ingest --write",
            "唔用 WSL Python production",
            "主入口 | **Windows**",
        ):
            self.assertNotIn(obsolete, handoff)
        self.assertIn("/home/jackson0202/cardz-market-cap/.venv-backend", handoff)
        self.assertIn("qc_failure_sync.py --write", handoff)

    def test_policy_bans_g10_index_and_requires_blocked_qc_retry_projection(self) -> None:
        policy = (ROOT / "docs/DATA_CONTRACT.md").read_text(encoding="utf-8")
        self.assertIn("only business database is MySQL `cardz_market_cap`", policy)
        self.assertIn("G10 index", policy)
        self.assertIn("forbidden in the", policy)
        self.assertIn("including a blocked run", policy)
        self.assertIn("deterministic retry worklists", policy)


if __name__ == "__main__":
    unittest.main()
