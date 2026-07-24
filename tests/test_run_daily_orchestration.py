from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

from run_daily import gemrate_daily_command, post_derive_audit_command  # noqa: E402


class RunDailyOrchestrationTests(unittest.TestCase):
    def test_post_derive_audit_uses_only_the_current_snk_run(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "SNK PSA 10"):
            post_derive_audit_command(None, production=False)
        command = post_derive_audit_command(
            ROOT / "data/runtime/private-source-runs/current/snk-psa10.jsonl",
            required_presentation_view="top300",
        )
        self.assertIn("--snk-run", command)
        self.assertEqual(command[-2:], ["--require-presentation-view", "top300"])

    def test_gemrate_daily_refresh_does_not_depend_on_a_trial_key(self) -> None:
        ids = ROOT / "data/runtime/private-source-map/tracked-gemrate-ids.txt"
        universe = ROOT / "data/runtime/private-source-map/tracked-universe.json"
        mirror = ROOT / "integrations/grade10/data"
        command = gemrate_daily_command(ids, universe, mirror)
        self.assertEqual(command[2], "daily")
        self.assertIn(str(ids), command)
        self.assertIn(str(universe), command)
        self.assertIn(str(mirror), command)
        self.assertNotIn("GEMRATE_API_KEY", " ".join(command))


if __name__ == "__main__":
    unittest.main()
