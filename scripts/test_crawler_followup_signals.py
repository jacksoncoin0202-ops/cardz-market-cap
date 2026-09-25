#!/usr/bin/env python3
"""Crawler follow-up signals stay honest and classifiable. No network/DB.

Reproduces three quiet failures read from the V2 journal and receipts on
2026-09-25:
  * snk_en_image selected nothing from 08-28 on (every registry row reads
    modeNeeded=stock) and still reported slaOk=True with due=0;
  * 09-18 SNK harvest died on "Temporary failure in name resolution" for every
    item and the task climbed SOURCE_FAILED: DNS was not transient, and the
    stderr that said so sat behind 6000 characters of stdout progress;
  * the bare "cdp" substring read file names and the success line
    "CDP_IDENTITY_OK port=9333" as a dead 9333.
"""
from __future__ import annotations

import contextlib
import json
import shutil
import sys
import tempfile
import types
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pipelines"))

import collect_control as cc  # noqa: E402
import daily_chain_v2_worker as v2worker  # noqa: E402
from daily_chain_v2_contract import (  # noqa: E402
    adapter_stderr_excerpt,
    classify_error,
    redact_secrets,
)

# Shapes copied from the journal / runtime receipts (read-only, 2026-09-25).
DNS_LINE = (
    "  FAIL active item {item}: ConnectionError:HTTPSConnectionPool(host='snkrdunk.com',"
    " port=443): Max retries exceeded with url: /v1/apparels/{item} (Caused by"
    " NameResolutionError(\"<urllib3.connection.HTTPSConnection object at 0x706503361ac0>:"
    " Failed to resolve 'snkrdunk.com' ([Errno -3] Temporary failure in name resolution)\"))"
)
CDP_SINGLETON = (
    'RuntimeError:errorCode=CDP_9333_UNAVAILABLE the singleton CARDZ CDP session could not be'
    ' started; no PC adapter ran: {"errorClass": "cdp_unreachable", "exit": 1,'
    ' "identityOnly": true, "needed": true, "ok": false, "requested": true,'
    ' "retryAfterSeconds": 300, "retryable": true, "stderrTail": "",'
    ' "stdoutTail": "CDP_IDENTITY_REJECT port=9333 reason=no-listener\\n"}'
)
SECRET = "Sup3rS3cretValue"
TELEGRAM = "bot123456789:AAAbbbCCCdddEEEfffGGGhhhIIIjjjKKKlll"


def progress_stdout(chars: int) -> str:
    lines, n = [], 0
    while sum(len(line) + 1 for line in lines) < chars:
        n += 1
        lines.append(f"  [{n}/1172] pkmn-tcg-SV7-{n:03d} — 0 kline, 0 trades")
    # A credential at the very end of stdout lands inside the detail tail.
    lines.append(f"[snk_market_data] db mysql://cardz:{SECRET}@db:3306/cardz")
    return "\n".join(lines) + "\n"


def snk_dns_report(stdout_chars: int = 7000) -> dict[str, Any]:
    stderr = "\n".join(DNS_LINE.format(item=item) for item in (128106, 466509, 727322))
    stderr += f"\nMYSQL_PASSWORD={SECRET} https://api.telegram.org/{TELEGRAM}/sendMessage\n"
    results = [
        {
            "adapter": adapter,
            "ok": False,
            "error": "snk_harvest_failed",
            "harvest": {"exit": 1, "stderrTail": stderr, "stdoutTail": progress_stdout(stdout_chars)},
        }
        for adapter in ("snk_trades", "snk_price")
    ]
    results.append({"adapter": "snk_en_image", "ok": True, "due": 0})
    return {
        "ok": False, "processed": 0, "inserted": 0, "checkpointed": 0, "failed": 0,
        "quarantined": 0, "asOf": "2026-09-18T02:09:32Z",
        "failedAdapters": ["snk_trades", "snk_price"], "truncatedAdapters": [],
        "results": results,
    }


def fake_collect(report: dict[str, Any]) -> types.ModuleType:
    module = types.ModuleType("collect_control")
    module.REGISTRY_PATH = Path("fixture-registry.jsonl")
    module._jsonl_rows = lambda path: []
    module.cmd_incr = lambda **kwargs: json.loads(json.dumps(report))
    module.cmd_stock = lambda **kwargs: json.loads(json.dumps(report))
    return module


@contextlib.contextmanager
def collect_module(module: types.ModuleType):
    previous = sys.modules.get("collect_control")
    sys.modules["collect_control"] = module
    try:
        yield
    finally:
        if previous is None:
            sys.modules.pop("collect_control", None)
        else:
            sys.modules["collect_control"] = previous


class WorkerFixture(unittest.TestCase):
    def setUp(self) -> None:
        self.workspace = Path(tempfile.mkdtemp(prefix="cardz-crawler-followup-"))
        self.receipt = self.workspace / "receipt.json"
        self.task = {"source_code": "snkrdunk", "run_id": "cardz-v2:2026-09-18"}
        self.payload = {
            "shard": "all",
            "worker": {"adapters": ["snk_trades", "snk_price", "snk_en_image"]},
        }

    def tearDown(self) -> None:
        shutil.rmtree(self.workspace, ignore_errors=True)

    def run_worker(self, report: dict[str, Any]) -> Any:
        with collect_module(fake_collect(report)):
            return v2worker.run_collect(self.task, self.payload, self.receipt)


class ClassifierDnsAndCdp(unittest.TestCase):
    def test_dns_failures_are_transient(self) -> None:
        for text in (
            DNS_LINE.format(item=128106),
            "curl: (6) Could not resolve host: www.pricecharting.com",
        ):
            decision = classify_error(text)
            self.assertEqual(decision.error_code, "TRANSIENT_SOURCE", text)
            self.assertFalse(decision.terminal, text)

    def test_mysql_dns_stays_mysql(self) -> None:
        text = (
            "OperationalError(2003, \"Can't connect to MySQL server on 'db'"
            " ([Errno -3] Temporary failure in name resolution)\")"
        )
        self.assertEqual(classify_error(text).error_code, "MYSQL_UNAVAILABLE")

    def test_real_cdp_failures_stay_cdp(self) -> None:
        for text in (
            CDP_SINGLETON,
            "RuntimeError:the singleton CARDZ CDP session could not be started; no PC adapter ran",
            '"stdoutTail": "CDP_IDENTITY_REJECT port=9333 reason=no-listener\\n"',
            "CDP lock timeout: /tmp/cardz/cdp.lock",
            "fetch: CDP unavailable after 3 tries",
            "  CDP_FAIL streak=3/3 status=0",
            "CDP_DOWN port=9333",
            "CDP_REVIVE_FAILED profile=cardz",
            '{"adapter": "pc_ebay_sales", "error": "pc_cdp_refresh_failed"}',
            "DevToolsActivePort file doesn't exist",
            "chrome not reachable",
            "connect ECONNREFUSED 127.0.0.1:9333",
            "CARDZ CDP 9333 preflight failed",
            # A success line next to a real failure never hides the failure.
            "CDP_IDENTITY_OK port=9333\nCDP_FAIL streak=3/3 status=0",
        ):
            self.assertEqual(classify_error(text).error_code, "CDP_9333_UNAVAILABLE", text)

    def test_file_names_and_success_lines_are_not_cdp(self) -> None:
        for text, expected in (
            (
                "pc child blocked by test-fixture-poisoned resume report"
                " (pc_cdp_refresh_report.json written 20:08 JST)",
                "SOURCE_FAILED",
            ),
            ("powershell scripts/ensure_chrome_cdp.ps1 exit=1: ValueError bad row", "SOURCE_FAILED"),
            (
                'errorCode=COLLECT_ADAPTER_FAILED detail=[{"adapter": "en_price_ref", "commands":'
                ' {"run": {"exit": 1, "stdoutTail": "CDP_IDENTITY_OK port=9333\\n"}},'
                ' "error": "pc_map:ValueError:bad row"}]',
                "SOURCE_FAILED",
            ),
            (
                "fetch cdp port=9333 ok url=https://www.pricecharting.com/game/x\nHTTP 503 upstream",
                "TRANSIENT_SOURCE",
            ),
            (
                'detail=[{"stdoutTail": "CDP_IDENTITY_OK port=9333\\n"}] can\'t connect to mysql',
                "MYSQL_UNAVAILABLE",
            ),
        ):
            self.assertEqual(classify_error(text).error_code, expected, text)


class Redaction(unittest.TestCase):
    def test_secrets_are_masked_and_prose_survives(self) -> None:
        text = (
            f"MYSQL_PASSWORD={SECRET} mysql://cardz:{SECRET}@db:3306/cardz"
            f" Authorization: Bearer {SECRET} x-api-key: '{SECRET}' token={SECRET}"
            f" https://api.telegram.org/{TELEGRAM}/sendMessage\n"
            f"Cookie: sid={SECRET}; cf_clearance={SECRET}\n"
            "Failed to resolve 'snkrdunk.com' ([Errno -3] Temporary failure in name resolution)"
        )
        masked = redact_secrets(text)
        self.assertNotIn(SECRET, masked)
        self.assertNotIn(TELEGRAM, masked)
        self.assertIn("[REDACTED]", masked)
        self.assertIn("Temporary failure in name resolution", masked)
        self.assertIn("snkrdunk.com", masked)

    def test_json_escaped_secret_is_masked(self) -> None:
        blob = json.dumps({"stderrTail": f'{{"password": "{SECRET}", "api_key": "{SECRET}"}}'})
        self.assertNotIn(SECRET, redact_secrets(blob))

    def test_excerpt_is_bounded_and_valid_json(self) -> None:
        # Quotes double under JSON escaping, so three 400-char tails overflow
        # the 1500-char budget and the excerpt must shed whole tails.
        detail = [
            {"adapter": f"a{i}", "commands": {"harvest": {"stderrTail": ('"' * 900 + "\n") * 5 + f"tail {i}"}}}
            for i in range(6)
        ]
        excerpt = adapter_stderr_excerpt(detail)
        self.assertLessEqual(len(excerpt), 1500)
        tails = json.loads(excerpt)
        self.assertIsInstance(tails, list)
        self.assertTrue(tails and all(len(tail) <= 400 for tail in tails), [len(t) for t in tails])
        self.assertEqual(adapter_stderr_excerpt([{"adapter": "a", "commands": {}}]), "")


class WorkerStderrExcerpt(WorkerFixture):
    def test_0918_dns_failure_is_visible_transient_and_redacted(self) -> None:
        with self.assertRaises(RuntimeError) as caught:
            self.run_worker(snk_dns_report())
        text = str(caught.exception)
        self.assertTrue(text.startswith("errorCode=COLLECT_ADAPTER_FAILED"), text[:120])
        self.assertIn(" stderr=", text)
        self.assertLess(text.index(" stderr="), text.index(" detail="))
        self.assertIn("Temporary failure in name resolution", text.split(" detail=", 1)[0])
        self.assertNotIn(SECRET, text)
        self.assertNotIn(TELEGRAM, text)
        head = text.split(" stderr=", 1)[0]
        self.assertLessEqual(len(text) - len(head), 6000 + len(" stderr=") + len(" detail="))
        # The journal keeps error_text[-8000:]; the verdict must survive it.
        self.assertTrue(text[-8000:].startswith("errorCode="), len(text))
        decision = classify_error(f"RuntimeError:{text}")
        self.assertEqual(decision.error_code, "TRANSIENT_SOURCE")
        self.assertFalse(decision.terminal)

    def test_failure_without_stderr_keeps_old_shape(self) -> None:
        report = snk_dns_report()
        for result in report["results"]:
            if "harvest" in result:
                result["harvest"].pop("stderrTail")
        with self.assertRaises(RuntimeError) as caught:
            self.run_worker(report)
        text = str(caught.exception)
        self.assertNotIn(" stderr=", text)
        self.assertTrue(text.startswith("errorCode=COLLECT_ADAPTER_FAILED"))


class SnkEnImageSla(WorkerFixture):
    def idle_report(self, newest_age_hours: float | None, *, raises: bool = False) -> dict[str, Any]:
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        newest = None if newest_age_hours is None else now - timedelta(hours=newest_age_hours)

        def window() -> tuple[Any, Any, int]:
            if raises:
                raise OSError("db down")
            oldest = None if newest is None else newest - timedelta(days=17)
            return oldest, newest, 0 if newest is None else 1187

        def no_db() -> None:
            raise AssertionError("an idle SNK EN poll must not open a writer")

        with patch.object(cc, "_snk_en_checkpoint_window", window), patch.object(cc, "db", no_db):
            return cc.run_snk_en_image(
                [], mode="incr", limit=None, dry_run=False, delay=0.0, workers=1
            )

    def test_idle_poll_past_sla_is_not_ok_but_not_a_failure(self) -> None:
        report = self.idle_report(666.4)
        self.assertTrue(report["ok"])
        self.assertEqual(report["due"], 0)
        self.assertIs(report["freshness"]["slaOk"], False)
        self.assertEqual(report["freshness"]["reason"], "nothing_due_and_last_success_past_sla")
        self.assertNotIn("error", report)

    def test_idle_poll_inside_sla_is_ok(self) -> None:
        self.assertIs(self.idle_report(2.0)["freshness"]["slaOk"], True)

    def test_unknown_last_success_is_not_ok(self) -> None:
        self.assertEqual(self.idle_report(None)["freshness"]["reason"], "no_lane_checkpoint")
        unreadable = self.idle_report(1.0, raises=True)["freshness"]
        self.assertIs(unreadable["slaOk"], False)
        self.assertEqual(unreadable["reason"], "checkpoint_unreadable:OSError")

    def worker_report(self, freshness: dict[str, Any], **extra: Any) -> dict[str, Any]:
        return {
            "ok": True, "processed": 0, "inserted": 0, "checkpointed": 0, "failed": 0,
            "quarantined": 0, "asOf": "2026-09-25T02:00:00Z",
            "failedAdapters": [], "truncatedAdapters": [],
            "results": [
                {"adapter": "snk_trades", "ok": True},
                {"adapter": "snk_en_image", "ok": True, "due": 0, "freshness": freshness, **extra},
            ],
        }

    def test_stale_lane_degrades_the_receipt_without_raising(self) -> None:
        receipt = self.run_worker(self.worker_report({"slaOk": False}))
        self.assertEqual(receipt["status"], "degraded")
        self.assertEqual(receipt["detail"]["slaStaleAdapters"], ["snk_en_image"])

    def test_fresh_or_dry_lane_keeps_the_receipt_completed(self) -> None:
        fresh = self.run_worker(self.worker_report({"slaOk": True}))
        self.assertEqual(fresh["status"], "completed")
        self.assertEqual(fresh["detail"]["slaStaleAdapters"], [])
        dry = self.run_worker(self.worker_report({"slaOk": False}, dryRun=True))
        self.assertEqual(dry["status"], "completed")


if __name__ == "__main__":
    unittest.main(verbosity=2)
