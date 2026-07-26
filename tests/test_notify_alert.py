from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from notify_alert import (  # noqa: E402
    DEFAULT_REPEAT_DAYS,
    build_payload,
    clear,
    error_lines,
    failure_stage,
    failure_status,
    latest_log,
    notify,
    read_state,
    redact,
    should_notify,
)

NOW = datetime(2026, 7, 26, 5, 7, 0, tzinfo=timezone.utc)
EXPECTED = "2026-07-26"


class Recorder:
    """Stand-in for post_webhook. Its existence is the point: the real
    transport is never reachable from a test, so no test can hit the network."""

    def __init__(self, delivered: bool = True, detail: str = "HTTP 200") -> None:
        self.calls: list = []
        self.delivered = delivered
        self.detail = detail

    def __call__(self, url, payload, timeout=None):
        self.calls.append((url, payload))
        return self.delivered, self.detail


def failing_transport(url, payload, timeout=None):
    return False, "URLError: <urlopen error connection refused>"


class ThrottleTests(unittest.TestCase):
    def test_first_occurrence_notifies(self) -> None:
        send, _ = should_notify(None, "exit1|price_freshness", NOW)
        self.assertTrue(send)

    def test_same_status_inside_window_is_suppressed(self) -> None:
        entry = {
            "status": "exit1|price_freshness",
            "lastNotifiedAt": (NOW - timedelta(days=1)).isoformat(),
        }
        send, reason = should_notify(entry, "exit1|price_freshness", NOW)
        self.assertFalse(send)
        self.assertIn("unchanged", reason)

    def test_same_status_reminds_after_repeat_window(self) -> None:
        # 連續失敗唔應該日日出同一句，但亦唔可以永遠靜音。
        entry = {
            "status": "exit1|price_freshness",
            "lastNotifiedAt": (NOW - timedelta(days=DEFAULT_REPEAT_DAYS)).isoformat(),
        }
        send, reason = should_notify(entry, "exit1|price_freshness", NOW)
        self.assertTrue(send)
        self.assertIn("still failing", reason)

    def test_changed_status_notifies_once_the_quiet_window_passed(self) -> None:
        entry = {
            "status": "exit1|price_freshness",
            "lastNotifiedAt": (NOW - timedelta(hours=6)).isoformat(),
        }
        send, reason = should_notify(entry, "exit2|no-checks", NOW)
        self.assertTrue(send)
        self.assertEqual(reason, "status changed")

    def test_second_facet_of_the_same_incident_is_suppressed(self) -> None:
        # verify 判 fail (exit1) 之後，unit 會因為同一個 exit code 變 failed，
        # OnFailure handler 幾秒後帶住另一個 status 再叫一次。唔想收兩條。
        entry = {
            "status": "exit1|price_freshness",
            "lastNotifiedAt": (NOW - timedelta(minutes=2)).isoformat(),
        }
        send, reason = should_notify(entry, "unit_failed:exit-code:1", NOW)
        self.assertFalse(send)
        self.assertIn("same incident", reason)


class NotifyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.state = Path(self.tmp.name) / "state.json"
        self.addCleanup(self.tmp.cleanup)

    def send(self, *, status="exit1|price_freshness", webhook="https://example.invalid/hook",
             transport=None, now=NOW, **kwargs):
        # webhook / transport / repeat_after_days 全部顯式傳入：測試唔可以受
        # 環境入面嘅 CARDZ_ALERT_* 影響，亦唔可以有機會掂到真 transport。
        return notify(
            key="daily",
            status=status,
            stage="verify gate: price_freshness",
            expected_date=EXPECTED,
            exit_code=1,
            webhook=webhook,
            now=now,
            state_path=self.state,
            repeat_after_days=DEFAULT_REPEAT_DAYS,
            transport=transport,
            **kwargs,
        )

    def test_without_webhook_env_nothing_is_posted_and_nothing_fails(self) -> None:
        recorder = Recorder()
        result = self.send(webhook="", transport=recorder)
        self.assertEqual(recorder.calls, [])
        self.assertFalse(result["sent"])
        self.assertFalse(result["webhookConfigured"])
        self.assertIn("CARDZ_ALERT_WEBHOOK", result["summary"])
        # 依然要記低狀態，否則配置咗 webhook 之後分唔到新舊失敗。
        self.assertEqual(read_state(self.state)["daily"]["status"], "exit1|price_freshness")

    def test_with_webhook_the_payload_is_posted_once(self) -> None:
        recorder = Recorder()
        result = self.send(transport=recorder)
        self.assertTrue(result["sent"])
        self.assertEqual(len(recorder.calls), 1)
        url, payload = recorder.calls[0]
        self.assertEqual(url, "https://example.invalid/hook")
        self.assertEqual(payload["key"], "daily")

    def test_repeat_failures_are_deduped_then_reminded(self) -> None:
        recorder = Recorder()
        self.send(transport=recorder, now=NOW)
        self.send(transport=recorder, now=NOW + timedelta(days=1))
        self.send(transport=recorder, now=NOW + timedelta(days=2))
        self.assertEqual(len(recorder.calls), 1, "連續三日同一個失敗只應該出一次聲")
        self.send(transport=recorder, now=NOW + timedelta(days=DEFAULT_REPEAT_DAYS))
        self.assertEqual(len(recorder.calls), 2, "過咗提醒窗要再提一次")
        self.assertIn("day 4 of this failure", recorder.calls[1][1]["text"])

    def test_failed_delivery_does_not_advance_the_throttle(self) -> None:
        # 送唔到 = 冇人收到，下一次 run 應該再試，唔可以當已通報。
        self.send(transport=failing_transport, now=NOW)
        self.assertIsNone(read_state(self.state)["daily"]["lastNotifiedAt"])
        recorder = Recorder()
        result = self.send(transport=recorder, now=NOW + timedelta(days=1))
        self.assertTrue(result["sent"])

    def test_clear_lets_the_next_failure_speak_again(self) -> None:
        recorder = Recorder()
        self.send(transport=recorder, now=NOW)
        self.assertTrue(clear("daily", self.state))
        self.send(transport=recorder, now=NOW + timedelta(days=1))
        self.assertEqual(len(recorder.calls), 2)

    def test_payload_is_actionable_without_ssh(self) -> None:
        log_dir = Path(self.tmp.name) / "logs"
        log_dir.mkdir()
        log_file = log_dir / "daily_production_20260726_003000.log"
        log_file.write_text(
            "\n".join(
                ["  public card pages 825/1468 ok=790"] * 50
                + [
                    "Traceback (most recent call last):",
                    "pymysql.err.OperationalError: (2003, \"Can't connect to MySQL server\")",
                ]
            ),
            encoding="utf-8",
        )
        recorder = Recorder()
        notify(
            key="daily",
            status="exit1|price_freshness,source_coverage",
            stage="verify gate: price_freshness, source_coverage",
            expected_date=EXPECTED,
            exit_code=1,
            log_path=log_file,
            unit="cardz-market-cap-daily.service",
            webhook="https://example.invalid/hook",
            now=NOW,
            state_path=self.state,
            transport=recorder,
        )
        payload = recorder.calls[0][1]
        # 1. 邊日  2. 邊個 stage 死  3. exit code  4. log 檔路徑  5. 最後幾行錯誤
        self.assertEqual(payload["date"], EXPECTED)
        self.assertIn("price_freshness", payload["stage"])
        self.assertIn("source_coverage", payload["stage"])
        self.assertEqual(payload["exitCode"], 1)
        self.assertEqual(payload["logPath"], str(log_file))
        self.assertTrue(payload["logTail"])
        joined = "\n".join(payload["logTail"])
        self.assertIn("OperationalError", joined)
        self.assertNotIn("public card pages", joined, "進度 spam 唔應該逼走真正嘅錯誤行")
        # 淨係讀 text 一行都知發生咩事。
        self.assertIn(EXPECTED, payload["text"])
        self.assertIn("price_freshness", payload["text"])

    def test_notify_failure_never_breaks_the_gate(self) -> None:
        # verify 嘅 exit code 係營運訊號。通報層爆咗只可以吞落去，
        # 唔可以將一個「數據唔啱、有得查」嘅失敗變成一個 traceback。
        import notify_alert

        with mock.patch.object(notify_alert, "notify", side_effect=RuntimeError("boom")):
            result = notify_alert.notify_failure(
                tag="daily", expected_date=EXPECTED, exit_code=1,
                checks=[{"check": "price_freshness", "pass": False}],
                reason="daily outcome checks failed",
            )
        self.assertFalse(result["sent"])
        self.assertIn("notifier error", result["summary"])


class ContentHelperTests(unittest.TestCase):
    def test_stage_and_status_come_from_the_failed_checks(self) -> None:
        checks = [
            {"check": "price_freshness", "pass": False},
            {"check": "snapshot_freshness", "pass": True},
            {"check": "source_coverage", "pass": False},
        ]
        self.assertEqual(failure_stage(checks, "x"), "verify gate: price_freshness, source_coverage")
        self.assertEqual(failure_status(1, checks), "exit1|price_freshness,source_coverage")
        # exit 2 = 驗唔到（DB 連唔到），冇 check 結果可言。
        self.assertEqual(failure_status(2, []), "exit2|no-checks")
        self.assertEqual(failure_stage([], "verify infra failure: OperationalError"),
                         "verify infra failure: OperationalError")

    def test_credentials_are_stripped_before_leaving_the_box(self) -> None:
        self.assertNotIn("hunter2", redact("CARDZ_DB_PASSWORD=hunter2"))
        self.assertNotIn("s3cr3t", redact("mysql://cardz:s3cr3t@db.internal:3306/x"))
        self.assertNotIn("abc123", redact("Authorization: Bearer abc123"))

    def test_missing_log_yields_no_tail_instead_of_raising(self) -> None:
        self.assertEqual(error_lines(None), [])
        self.assertEqual(error_lines(Path("nope") / "missing.log"), [])

    def test_latest_log_picks_the_newest_match(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            logs = Path(tmp)
            (logs / "daily_a.log").write_text("a", encoding="utf-8")
            (logs / "watchdog_b.log").write_text("b", encoding="utf-8")
            self.assertEqual(latest_log("daily_*.log", logs).name, "daily_a.log")
            self.assertIsNone(latest_log("nothing_*.log", logs))

    def test_payload_is_json_serialisable(self) -> None:
        payload = build_payload(
            key="daily", status="exit1|x", stage="verify gate: x",
            expected_date=EXPECTED, exit_code=1, now=NOW,
        )
        self.assertEqual(json.loads(json.dumps(payload))["date"], EXPECTED)


class SystemdWiringTests(unittest.TestCase):
    """OnFailure= 係 unit 層面死法（timeout kill / 226 NAMESPACE）唯一嘅出聲途徑。
    冇咗佢就返返去靜默失敗，所以要釘死。"""

    def test_units_declare_onfailure_and_the_template_exists(self) -> None:
        systemd = ROOT / "deploy" / "systemd"
        for name in ("cardz-market-cap-daily.service", "cardz-market-cap-watchdog.service"):
            unit = (systemd / name).read_text(encoding="utf-8")
            self.assertIn("OnFailure=cardz-market-cap-alert@%n.service", unit, name)
        template = systemd / "cardz-market-cap-alert@.service"
        self.assertTrue(template.exists())
        body = template.read_text(encoding="utf-8")
        self.assertIn("run-cardz-alert.sh %i", body)
        # webhook 由 root-owned 0600 env 檔提供，唔可以寫死喺 unit 入面。
        self.assertIn("EnvironmentFile=/etc/cardz-market-cap/backend.env", body)
        self.assertNotIn("CARDZ_ALERT_WEBHOOK=", body)

    def test_handler_reuses_the_verify_dedupe_key(self) -> None:
        handler = (ROOT / "deploy" / "systemd" / "run-cardz-alert.sh").read_text(encoding="utf-8")
        self.assertIn('key="${key#cardz-market-cap-}"', handler)
        self.assertIn("notify_alert.py", handler)
        self.assertIn("--write-alert", handler)

    def test_installer_refuses_to_ship_units_without_the_alert_template(self) -> None:
        installer = (ROOT / "deploy" / "linux" / "cardz-daily-systemd.sh").read_text(encoding="utf-8")
        self.assertIn("cardz-market-cap-alert@.service", installer)
        self.assertIn("scripts/notify_alert.py", installer)

    def test_verify_gate_notifies_in_process(self) -> None:
        # OnFailure= 捉唔到「chain exit 0 但數據唔啱」——嗰個判決淨係 gate 自己知。
        gate = (ROOT / "scripts" / "verify_daily_run.py").read_text(encoding="utf-8")
        self.assertIn("notify_failure", gate)
        self.assertIn("clear_notify_state", gate)


if __name__ == "__main__":
    unittest.main()
