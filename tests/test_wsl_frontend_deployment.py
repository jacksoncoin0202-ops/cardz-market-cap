from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SYSTEMD = ROOT / "deploy" / "systemd"


class WslFrontendDeploymentContractTests(unittest.TestCase):
    def test_web_reads_one_versioned_generation_and_daily_verifies_restart(self) -> None:
        web = (SYSTEMD / "cardz-market-cap-web.service").read_text(encoding="utf-8")
        web_refresh = (SYSTEMD / "cardz-market-cap-web-refresh.service").read_text(encoding="utf-8")
        daily = (SYSTEMD / "cardz-market-cap-daily.service").read_text(encoding="utf-8")
        refresh = (SYSTEMD / "run-cardz-web-refresh.sh").read_text(encoding="utf-8")

        self.assertIn("MARKET_DATA_POINTER_PATH=/opt/cardz-market-cap/data/runtime/publish-staging/latest.json", web)
        self.assertIn("CARDZ_RUNTIME=node", web)
        self.assertIn("OnSuccess=cardz-market-cap-web-refresh.service", daily)
        self.assertIn("systemctl restart \"$web_unit\"", refresh)
        self.assertIn("verify-runtime-health.mjs", refresh)
        self.assertIn("Environment=PORT=3900", web)
        self.assertIn(
            "Environment=CARDZ_WEB_HEALTH_URL=http://127.0.0.1:3900/api/health",
            web_refresh,
        )
        self.assertIn("Environment=CARDZ_BUILD_TARGET=node", daily)
        self.assertIn("Environment=CARDZ_RUNTIME=node", daily)
        self.assertIn("/opt/cardz-market-cap/apps/web/public", daily)
        self.assertIn("/opt/cardz-market-cap/apps/web/.next", daily)

        package = (ROOT / "apps" / "web" / "package.json").read_text(encoding="utf-8")
        prepare = (ROOT / "apps" / "web" / "scripts" / "prepare-standalone.mjs").read_text(encoding="utf-8")
        self.assertIn("scripts/prepare-standalone.mjs", package)
        self.assertIn('resolve(standaloneApp, ".next", "static")', prepare)
        self.assertIn('resolve(standaloneApp, "public")', prepare)
        self.assertIn('rmSync(resolve(standaloneApp, "public", "market-assets")', prepare)

    def test_candidate_and_retention_timers_ship_disabled(self) -> None:
        candidate = (SYSTEMD / "cardz-market-cap-candidate-refresh.service").read_text(encoding="utf-8")
        candidate_timer = (SYSTEMD / "cardz-market-cap-candidate-refresh.timer").read_text(encoding="utf-8")
        retention = (SYSTEMD / "cardz-market-cap-retention.service").read_text(encoding="utf-8")
        retention_timer = (SYSTEMD / "cardz-market-cap-retention.timer").read_text(encoding="utf-8")

        self.assertIn(
            "scripts/backend.py full-backfill --external-db --mode production "
            "--collect-public-candidates "
            "--require-gemrate-refresh --promote-universe --presentation-view top300_boards",
            candidate,
        )
        self.assertNotIn("[Install]", candidate_timer)
        self.assertIn("db_retention.py --limit 25000 --plan-json", retention)
        self.assertNotIn("--apply", retention)
        self.assertNotIn("--backfill-effective-pointers", retention)
        self.assertNotIn("[Install]", retention_timer)

    def test_installer_requires_explicit_single_writer_cutover(self) -> None:
        installer = (ROOT / "deploy" / "linux" / "cardz-daily-systemd.sh").read_text(encoding="utf-8")
        daily_runner = (SYSTEMD / "run-cardz-daily.sh").read_text(encoding="utf-8")
        candidate = (SYSTEMD / "cardz-market-cap-candidate-refresh.service").read_text(encoding="utf-8")

        self.assertIn('enable_units=0', installer)
        self.assertIn('start_units=0', installer)
        self.assertIn("--confirm-single-writer", installer)
        self.assertIn("if ((enable_units)); then", installer)
        self.assertIn("if ((start_units)); then", installer)
        self.assertNotIn("systemctl enable cardz-market-cap-candidate-refresh.timer", installer)
        self.assertNotIn("systemctl enable cardz-market-cap-retention.timer", installer)
        self.assertIn("data/runtime/cardz-writer.lock", daily_runner)
        self.assertIn("data/runtime/cardz-writer.lock", candidate)
        self.assertIn('daily_args+=(--require-gemrate-refresh)', daily_runner)
        self.assertIn(
            "EnvironmentFile=-/etc/cardz-market-cap/gemrate.env",
            (SYSTEMD / "cardz-market-cap-daily.service").read_text(encoding="utf-8"),
        )
        self.assertIn("EnvironmentFile=-/etc/cardz-market-cap/gemrate.env", candidate)


if __name__ == "__main__":
    unittest.main()
