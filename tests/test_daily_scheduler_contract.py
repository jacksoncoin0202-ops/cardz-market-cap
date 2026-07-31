from __future__ import annotations

import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]

# 2026-07-25 事故：排程係 06:30 JST，而 run_daily.py 用 **UTC** 日期砌
# market_run_id。06:30 JST = 前一日 21:30 UTC，所以 run_id 落返舊日期，collector
# 見到同名 run_id 就 replay 舊輸出，effective_date 保持舊值，market_alerts 嗰個
# INSERT IGNORE 變 no-op —— 全鏈 exit 0，零新數據，零 alert。
#
# 09:30 JST = 00:30 UTC，本地日同 UTC 日對齊，係修正嘅核心。
#
# 呢個檔以前反而斷言緊 06:30、7200s、同埋 `exec ... daily`（即係冇 outcome
# gate），亦即測試套件親自釘死咗出事嗰組設定：任何人跑測試見紅都會好自然咁
# 「修返測試」＝倒返轉個修正。下面所有斷言都係反方向——釘住修正，令倒退變紅燈。
CORRECT_LOCAL_TIME = "09:30"
CORRECT_UTC_TIME = "00:30"
INCIDENT_LOCAL_TIME = "06:30"


class DailySchedulerContractTests(unittest.TestCase):
    def test_windows_task_is_safe_by_default_and_runs_backend_daily_then_verify(self) -> None:
        installer = (ROOT / "deploy" / "windows" / "install_daily_task.ps1").read_text(encoding="utf-8")
        runner = (ROOT / "deploy" / "windows" / "run-cardz-daily.ps1").read_text(encoding="utf-8")
        self.assertIn("[string]$Action = 'dry-run'", installer)
        self.assertIn("ValidateSet('install', 'status', 'uninstall', 'dry-run')", installer)
        self.assertIn(f"$At = '{CORRECT_LOCAL_TIME}'", installer)
        self.assertNotIn(f"$At = '{INCIDENT_LOCAL_TIME}'", installer)
        self.assertIn("Tokyo Standard Time", installer)
        self.assertIn("-MultipleInstances IgnoreNew", installer)
        # 實測全鏈要 2–2.5 鐘；2 鐘上限會喺 GemRate 爬到一半殺成個 process tree，
        # 令尾段嘅 verify gate 永遠冇機會跑 → 靜默死亡、零 alert。
        self.assertIn("-ExecutionTimeLimit (New-TimeSpan -Hours 6)", installer)
        self.assertIn("-RestartCount 3", installer)
        self.assertIn("-RestartInterval (New-TimeSpan -Minutes 10)", installer)
        self.assertIn("Assert-PrivateEnvironmentFile", installer)
        self.assertIn("scripts\\backend.py", runner)
        # Local docker-db mode: backend.py resolves credentials from
        # data/runtime/config/backend.env; --external-db is reserved for a
        # managed RDS with an SSL CA (production).
        self.assertIn("daily --mode $Mode", runner)
        self.assertNotIn(" daily --external-db", runner)
        self.assertNotIn("run_daily.py", runner)
        self.assertNotIn("R2Bucket", installer)
        self.assertNotIn("CanaryOrigin", installer)
        # Outcome gate 必須喺 daily 之後跑，而且 daily 失敗都要跑。
        self.assertIn("verify_daily_run.py", runner)

    def test_windows_watchdog_is_registered_separately(self) -> None:
        installer = (ROOT / "deploy" / "windows" / "install_daily_task.ps1").read_text(encoding="utf-8")
        self.assertIn("$WatchdogAt = '14:07'", installer)
        self.assertIn("run-cardz-watchdog.ps1", installer)
        self.assertTrue((ROOT / "deploy" / "windows" / "run-cardz-watchdog.ps1").exists())

    def test_linux_units_use_utc_schedule_singleton_timeout_and_outcome_gate(self) -> None:
        service = (ROOT / "deploy" / "systemd" / "cardz-market-cap-daily.service").read_text(encoding="utf-8")
        timer = (ROOT / "deploy" / "systemd" / "cardz-market-cap-daily.timer").read_text(encoding="utf-8")
        runner = (ROOT / "deploy" / "systemd" / "run-cardz-daily.sh").read_text(encoding="utf-8")
        self.assertIn("ExecStart=/usr/bin/env bash", service)
        self.assertIn("TimeoutStartSec=21600", service)
        self.assertNotIn("TimeoutStartSec=7200", service)
        self.assertIn("Restart=on-failure", service)
        self.assertIn("RestartSec=10min", service)
        self.assertIn("EnvironmentFile=/etc/cardz-market-cap/backend.env", service)
        self.assertIn("EnvironmentFile=-/etc/cardz-market-cap/gemrate.env", service)
        self.assertIn(f"OnCalendar=*-*-* {CORRECT_UTC_TIME}:00 UTC", timer)
        self.assertNotIn(f"{INCIDENT_LOCAL_TIME}:00 Asia/Tokyo", timer)
        self.assertIn("Persistent=true", timer)
        # 呢個斷言以前係 assertNotIn —— 即係反過嚟釘死咗「daily 冇 jitter」。
        # daily 係唯一一條直接向外爬嘅每日排程，準時到秒就係一個指紋
        # （docs/HANDOFF.md §8 第 2 條，G10 stealth）。jitter 而家係必需品。
        self.assertIn("RandomizedDelaySec=1800", timer)
        self.assertIn('daily --external-db --mode "$mode"', runner)
        # 唔准用 exec 收尾：exec 會將 shell 換成 python，verify gate 永遠冇機會跑。
        self.assertNotIn('exec "$python_bin"', runner)
        self.assertIn("verify_daily_run.py", runner)
        self.assertIn("root-owned and not group/world-readable", runner)

    def test_linux_schedule_actually_reaches_the_publish_step(self) -> None:
        # 一條「跑完 collect 就收工」嘅排程同一條「跑到 publish」嘅排程，
        # 喺 systemctl 眼中一樣係 exit 0。呢條鏈由 unit 嘅 env var 一路傳到
        # run_daily.py 嘅 --backend-only 早退，中間三個檔任何一個斷咗都係靜默。
        service = (ROOT / "deploy" / "systemd" / "cardz-market-cap-daily.service").read_text(encoding="utf-8")
        runner = (ROOT / "deploy" / "systemd" / "run-cardz-daily.sh").read_text(encoding="utf-8")
        backend = (ROOT / "scripts" / "backend.py").read_text(encoding="utf-8")

        self.assertIn("Environment=CARDZ_DAILY_PUBLISH=local", service)
        self.assertIn('publish_mode="${CARDZ_DAILY_PUBLISH:-local}"', runner)
        self.assertIn("daily_args+=(--publish)", runner)
        self.assertIn("daily_args+=(--local-only)", runner)
        # --backend-only 係 run_daily.py 入面嗰個 `return 0` 嘅開關；
        # backend.py 收到 --publish 就要拎走佢，否則成個 publish 段跳過。
        self.assertIn('command.remove("--backend-only")', backend)

    def test_outbound_timers_are_jittered_and_not_a_fixed_offset_apart(self) -> None:
        # 爬取時序唔准照抄任何一方嘅固定日程（docs/HANDOFF.md §8 第 2 條）。
        # 兩條定死秒數嘅 timer 相隔恆定 47 分鐘，同照抄一份日程係同一件事 ——
        # 對面睇到嘅一樣係一個可預測嘅節奏。所以呢度唔淨止斷言「有 jitter」，
        # 而係斷言兩者之間嘅間距真係會浮動，而且浮動幅度大到有意義。
        daily_start, daily_jitter = self._timer_window("cardz-market-cap-daily.timer")
        disco_start, disco_jitter = self._timer_window("cardz-grade10-discovery.timer")
        watchdog_start, _ = self._timer_window("cardz-market-cap-watchdog.timer")

        self.assertGreater(daily_jitter, 0, "daily 係唯一直接向外爬嘅每日排程，唔可以準時到秒")
        self.assertGreater(disco_jitter, 0)
        self.assertGreaterEqual(
            daily_jitter + disco_jitter,
            1800,
            "兩條 timer 之間嘅間距至少要浮動 30 分鐘，否則個 offset 一樣係指紋",
        )

        # Jitter 上限有兩條硬邊界，唔可以「越大越隱蔽」咁加落去：
        # (1) 唔准跨 UTC 日 —— market_run_id 由 UTC 日期砌（2026-07-25 事故）。
        self.assertLess(daily_start + daily_jitter, 86400, "daily 最遲開跑時間唔可以跨 UTC 日")
        # (2) 全鏈實測 2–2.5 鐘，最遲完成時間要喺 watchdog 之前，
        #     否則專捉靜默失敗嗰個 watchdog 會對住條仲跑緊嘅鏈報假警。
        self.assertLessEqual(
            daily_start + daily_jitter + int(2.5 * 3600),
            watchdog_start,
            "daily 最壞情況完成時間撞正 watchdog",
        )

        # discovery 係 pre-warm（backend.py daily 自己會再叫一次 discovery），
        # 但佢嘅整個窗連 service timeout 都要喺 daily 最早開跑之前收乾淨。
        disco_service = (ROOT / "deploy" / "systemd" / "cardz-grade10-discovery.service").read_text(encoding="utf-8")
        disco_timeout = int(
            next(line for line in disco_service.splitlines() if line.startswith("TimeoutStartSec=")).split("=", 1)[1]
        )
        # discovery 喺 UTC 日 N 夜晚跑，daily 喺日 N+1 朝早跑，所以要跨日比較。
        self.assertLess(
            disco_start + disco_jitter + disco_timeout,
            daily_start + 86400,
            "discovery 最壞情況收工時間要早過 daily 最早開跑",
        )
        self.assertGreater(disco_start, daily_start, "discovery 應該喺 daily 之前一晚跑，唔係同日")

    def _timer_window(self, unit_name: str) -> tuple[int, int]:
        """(OnCalendar 秒數, RandomizedDelaySec) —— 兩者都由 unit 檔讀返出嚟。"""
        text = (ROOT / "deploy" / "systemd" / unit_name).read_text(encoding="utf-8")
        on_calendar = next(
            line for line in text.splitlines() if line.startswith("OnCalendar=") and line.rstrip().endswith("UTC")
        )
        clock = on_calendar.split()[1]
        hours, minutes, seconds = (int(part) for part in clock.split(":"))
        jitter_lines = [line for line in text.splitlines() if line.startswith("RandomizedDelaySec=")]
        jitter = int(jitter_lines[0].split("=", 1)[1]) if jitter_lines else 0
        return hours * 3600 + minutes * 60 + seconds, jitter

    def test_linux_watchdog_units_exist_and_run_verify(self) -> None:
        timer = (ROOT / "deploy" / "systemd" / "cardz-market-cap-watchdog.timer").read_text(encoding="utf-8")
        service = (ROOT / "deploy" / "systemd" / "cardz-market-cap-watchdog.service").read_text(encoding="utf-8")
        runner = (ROOT / "deploy" / "systemd" / "run-cardz-watchdog.sh").read_text(encoding="utf-8")
        self.assertIn("OnCalendar=*-*-* 05:07:00 UTC", timer)
        self.assertIn("Persistent=true", timer)
        self.assertIn("TimeoutStartSec=1200", service)
        # Watchdog 失敗本身就係要保留嘅訊號，重試只會沖淡佢。
        self.assertNotIn("Restart=", service)
        self.assertIn("--tag watchdog", runner)

    def test_linux_installer_covers_verify_gate_and_watchdog(self) -> None:
        installer = (ROOT / "deploy" / "linux" / "cardz-daily-systemd.sh").read_text(encoding="utf-8")
        self.assertIn("install|status|uninstall|dry-run", installer)
        self.assertIn('action="dry-run"', installer)
        self.assertIn("systemctl enable cardz-market-cap-daily.timer", installer)
        self.assertIn("systemctl enable cardz-market-cap-watchdog.timer", installer)
        # run-cardz-daily.sh 冇咗 verify_daily_run.py 會即刻 exit 1，所以 installer
        # 必須喺裝之前攔住，唔好裝完先至日日 fail。
        self.assertIn("scripts/verify_daily_run.py", installer)
        self.assertNotIn(f"schedule={INCIDENT_LOCAL_TIME}", installer)
        self.assertIn("Environment file must use LF line endings", installer)

    def test_linux_installer_creates_readwrite_paths_before_enabling_units(self) -> None:
        # ProtectSystem=strict 配 ReadWritePaths= 要求每個路徑喺 systemd 起 mount
        # namespace 嗰陣實際存在，否則 unit 死於 status=226/NAMESPACE —— 而且死喺
        # ExecStart 之前，journalctl 一行 Python 輸出都冇。daily unit 列嘅四個目錄
        # 全部 gitignored，fresh clone 之後唔存在，所以 installer 必須自己開返。
        installer = (ROOT / "deploy" / "linux" / "cardz-daily-systemd.sh").read_text(encoding="utf-8")
        self.assertIn("readwrite_paths()", installer)
        # 路徑由 unit 檔讀返出嚟，唔好喺 installer 另抄一份會走音嘅清單。
        self.assertIn("sed -n 's/^ReadWritePaths=//p'", installer)
        self.assertIn('install -d -m 0750 -o "$service_user" -g "$service_user"', installer)
        self.assertLess(
            installer.index('install -d -m 0750 -o "$service_user"'),
            installer.index("systemctl enable cardz-market-cap-daily.timer"),
            "目錄必須喺 enable/start unit 之前開好",
        )
        # dry-run 要睇得到邊個路徑仲未存在，唔使裝完先發現。
        self.assertIn("readWritePathsMissing=", installer)

    def test_linux_status_script_reads_systemctl_safely(self) -> None:
        path = ROOT / "deploy" / "linux" / "cardz-status.sh"
        self.assertTrue(path.exists(), "Linux 側要有同 deploy/windows/cardz-status.ps1 對應嘅狀態腳本")
        script = path.read_text(encoding="utf-8")
        # 逐個屬性 --value 讀（eval 禁令由 test_deploy_shell_scripts_never_eval_systemctl_output 守）。
        self.assertIn("--value", script)
        # systemctl show 對唔存在嘅 unit 一樣 exit 0，所以一定要另外睇 LoadState。
        self.assertIn("LoadState", script)
        self.assertIn("not-found", script)
        self.assertIn("cardz-market-cap-daily.timer", script)
        self.assertIn("cardz-market-cap-watchdog.timer", script)
        self.assertIn("ExecMainStatus", script)
        self.assertIn("data/runtime/alerts", script)
        self.assertIn("[verify]", script)

    def test_deploy_shell_scripts_never_eval_systemctl_output(self) -> None:
        # systemctl 嘅時間戳屬性（ExecMainExitTimestamp、NextElapseUSecRealtime）
        # 個值有空格："Sun 2026-07-26 09:30:00 JST"。eval 落去會當第二個 token 係
        # 命令執行 → "2026-07-26: command not found"、exit 127，仲要死喺 verify 之前，
        # 即係專捉靜默失敗嗰個 watchdog 自己靜默失敗。一律用 --value 逐個屬性讀。
        for script in sorted((ROOT / "deploy").rglob("*.sh")):
            # 只睇可執行嘅行——註解引用呢個 pattern 去解釋點解唔准用，係應該容許嘅。
            code = "\n".join(
                line for line in script.read_text(encoding="utf-8").splitlines() if not line.lstrip().startswith("#")
            )
            self.assertNotIn(
                'eval "$(systemctl',
                code,
                f"{script.relative_to(ROOT)} 用咗 eval 食 systemctl 輸出，改用 systemctl show --value",
            )

    def test_deploy_shell_and_unit_files_are_lf_only(self) -> None:
        # CRLF 嘅 .sh 喺 Linux 係 "bad interpreter: /usr/bin/env bash^M" 即死；
        # systemd unit 嘅 ExecStart 值尾巴帶 \r 會變成揾唔到嘅路徑。
        deploy = ROOT / "deploy"
        targets = sorted([*deploy.rglob("*.sh"), *deploy.rglob("*.service"), *deploy.rglob("*.timer")])
        self.assertTrue(targets, "deploy/ 應該有 shell 同 unit 檔")
        for path in targets:
            self.assertNotIn(b"\r\n", path.read_bytes(), f"{path.relative_to(ROOT)} 有 CRLF 行尾")

    def test_machine_readable_scheduler_contract_matches_units(self) -> None:
        document = json.loads((ROOT / "pipelines" / "daily-scheduler.example.json").read_text(encoding="utf-8"))
        self.assertEqual(document["localTime"], CORRECT_LOCAL_TIME)
        self.assertEqual(document["utcTime"], CORRECT_UTC_TIME)
        self.assertEqual(document["timezone"], "Asia/Tokyo")
        self.assertEqual(document["entrypoint"][1:3], ["scripts/backend.py", "daily"])
        self.assertEqual(document["singleton"]["scheduler"], "ignore-new")
        self.assertEqual(document["timeoutSeconds"], 21600)
        self.assertEqual(document["retry"], {"maxAttempts": 3, "backoffSeconds": 600})
        self.assertTrue(document["outcomeGate"]["runsEvenWhenDailyFails"])
        self.assertIn("scripts/verify_daily_run.py", document["outcomeGate"]["entrypoint"])
        self.assertEqual(document["watchdog"]["localTime"], "14:07")


if __name__ == "__main__":
    unittest.main()
