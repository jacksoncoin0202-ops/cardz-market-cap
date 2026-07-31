from __future__ import annotations

import contextlib
import importlib.util
import io
import sys
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]


def load_module(name: str, relative_path: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


bridge = load_module("cardz_g10_kline_price_bridge", "pipelines/g10_kline_price_bridge.py")
analytics = load_module("cardz_g10_analytics_ingest", "pipelines/g10_analytics_ingest.py")
db100 = load_module("cardz_db100_exhaust_pass", "scripts/db100_exhaust_pass.py")


class G10CanonicalDbBanTests(unittest.TestCase):
    def run_main_without_connection(self, module, argv: list[str]) -> tuple[int, str, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with (
            mock.patch.object(sys, "argv", argv),
            mock.patch.object(module, "connection_from_args", side_effect=AssertionError("DB must not connect")),
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
        ):
            result = module.main()
        return result, stdout.getvalue(), stderr.getvalue()

    def test_g10_kline_dry_run_reports_ban_without_db_connection(self) -> None:
        result, stdout, stderr = self.run_main_without_connection(bridge, ["g10_kline_price_bridge.py"])
        self.assertEqual(result, 0)
        self.assertIn("BANNED DRY-RUN", stdout)
        self.assertEqual(stderr, "")

    def test_g10_kline_write_fails_closed_before_db_connection(self) -> None:
        result, stdout, stderr = self.run_main_without_connection(
            bridge, ["g10_kline_price_bridge.py", "--write"]
        )
        self.assertEqual(result, 2)
        self.assertEqual(stdout, "")
        self.assertIn("BANNED", stderr)

    def test_g10_analytics_dry_run_reports_ban_without_db_connection(self) -> None:
        result, stdout, stderr = self.run_main_without_connection(analytics, ["g10_analytics_ingest.py"])
        self.assertEqual(result, 0)
        self.assertIn("BANNED DRY-RUN", stdout)
        self.assertEqual(stderr, "")

    def test_g10_analytics_write_fails_closed_before_db_connection(self) -> None:
        result, stdout, stderr = self.run_main_without_connection(
            analytics, ["g10_analytics_ingest.py", "--write"]
        )
        self.assertEqual(result, 2)
        self.assertEqual(stdout, "")
        self.assertIn("BANNED", stderr)

    def test_imported_g10_analytics_writer_is_also_blocked(self) -> None:
        with self.assertRaisesRegex(analytics.CanonicalG10WriteBanned, "forbidden"):
            analytics.write_all(None, None, None)  # type: ignore[arg-type]

    def test_db100_orchestration_excludes_the_bridge(self) -> None:
        self.assertIn("g10_kline_price_bridge", db100.BANNED_DB_WRITE_STEPS)
        source = (ROOT / "scripts" / "db100_exhaust_pass.py").read_text(encoding="utf-8")
        self.assertNotIn('str(kline), "--write"', source)

    def test_ebay_source_code_remains_distinct_from_banned_g10_codes(self) -> None:
        ebay_ingest = (ROOT / "pipelines" / "g10_ebay_ingest.py").read_text(encoding="utf-8")
        self.assertIn('SOURCE_CODE = "ebay"', ebay_ingest)
        self.assertNotIn("ebay", analytics.CANONICAL_DB_WRITE_BANNED_SOURCE_CODES)
        self.assertNotIn("pricecharting", analytics.CANONICAL_DB_WRITE_BANNED_SOURCE_CODES)
        self.assertNotIn("snk", analytics.CANONICAL_DB_WRITE_BANNED_SOURCE_CODES)
