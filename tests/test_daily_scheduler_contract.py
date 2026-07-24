from __future__ import annotations

import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class DailySchedulerContractTests(unittest.TestCase):
    def test_windows_task_is_safe_by_default_and_runs_only_backend_daily(self) -> None:
        installer = (ROOT / "pipelines" / "install_daily_task.ps1").read_text(encoding="utf-8")
        runner = (ROOT / "deploy" / "windows" / "run-cardz-daily.ps1").read_text(encoding="utf-8")
        self.assertIn("[string]$Action = 'dry-run'", installer)
        self.assertIn("ValidateSet('install', 'status', 'uninstall', 'dry-run')", installer)
        self.assertIn("$At = '06:30'", installer)
        self.assertIn("Tokyo Standard Time", installer)
        self.assertIn("-MultipleInstances IgnoreNew", installer)
        self.assertIn("-ExecutionTimeLimit (New-TimeSpan -Hours 2)", installer)
        self.assertIn("-RestartCount 3", installer)
        self.assertIn("-RestartInterval (New-TimeSpan -Minutes 10)", installer)
        self.assertIn("Assert-PrivateEnvironmentFile", installer)
        self.assertIn("scripts\\backend.py", runner)
        self.assertIn("daily --external-db --mode $Mode", runner)
        self.assertNotIn("run_daily.py", runner)
        self.assertNotIn("R2Bucket", installer)
        self.assertNotIn("CanaryOrigin", installer)

    def test_linux_units_have_jst_singleton_timeout_backoff_and_direct_backend_entrypoint(self) -> None:
        service = (ROOT / "deploy" / "systemd" / "cardz-market-cap-daily.service").read_text(encoding="utf-8")
        timer = (ROOT / "deploy" / "systemd" / "cardz-market-cap-daily.timer").read_text(encoding="utf-8")
        runner = (ROOT / "deploy" / "systemd" / "run-cardz-daily.sh").read_text(encoding="utf-8")
        self.assertIn("ExecStart=/usr/bin/env bash", service)
        self.assertIn("TimeoutStartSec=7200", service)
        self.assertIn("Restart=on-failure", service)
        self.assertIn("RestartSec=10min", service)
        self.assertIn("EnvironmentFile=/etc/cardz-market-cap/backend.env", service)
        self.assertIn("OnCalendar=*-*-* 06:30:00 Asia/Tokyo", timer)
        self.assertIn("Persistent=true", timer)
        self.assertNotIn("RandomizedDelaySec", timer)
        self.assertIn('exec "$python_bin" -X utf8 "$repo_root/scripts/backend.py" daily --external-db --mode "$mode"', runner)
        self.assertIn("root-owned and not group/world-readable", runner)
        installer = (ROOT / "deploy" / "linux" / "cardz-daily-systemd.sh").read_text(encoding="utf-8")
        self.assertIn("install|status|uninstall|dry-run", installer)
        self.assertIn("action=\"dry-run\"", installer)
        self.assertIn("systemctl enable cardz-market-cap-daily.timer", installer)

    def test_machine_readable_scheduler_contract_uses_backend_daily(self) -> None:
        document = json.loads((ROOT / "pipelines" / "daily-scheduler.example.json").read_text(encoding="utf-8"))
        self.assertEqual(document["localTime"], "06:30")
        self.assertEqual(document["timezone"], "Asia/Tokyo")
        self.assertEqual(document["entrypoint"][1:3], ["scripts/backend.py", "daily"])
        self.assertEqual(document["singleton"]["scheduler"], "ignore-new")
        self.assertEqual(document["timeoutSeconds"], 7200)
        self.assertEqual(document["retry"], {"maxAttempts": 3, "backoffSeconds": 600})


if __name__ == "__main__":
    unittest.main()
