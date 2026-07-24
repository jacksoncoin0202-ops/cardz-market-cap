from __future__ import annotations

import importlib.util
import hashlib
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
PIPELINES_ROOT = REPO_ROOT / "pipelines"
sys.path.insert(0, str(PIPELINES_ROOT))
SPEC = importlib.util.spec_from_file_location("cardz_run_daily", PIPELINES_ROOT / "run_daily.py")
assert SPEC and SPEC.loader
run_daily = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(run_daily)


class TagFallbackTests(unittest.TestCase):
    def write_catalog(
        self,
        runs_root: Path,
        run_name: str,
        *,
        observed_date: str,
        captured_at: datetime,
    ) -> Path:
        run_root = runs_root / run_name
        run_root.mkdir(parents=True)
        catalog = run_root / "tag-catalog.jsonl"
        catalog.write_text('{"setId":"base1"}\n', encoding="utf-8")
        (run_root / "tag-populations.manifest.json").write_text(
            json.dumps(
                {
                    "observedDate": observed_date,
                    "capturedAt": captured_at.isoformat().replace("+00:00", "Z"),
                    "catalogSha256": hashlib.sha256(catalog.read_bytes()).hexdigest(),
                }
            ),
            encoding="utf-8",
        )
        timestamp = captured_at.timestamp()
        os.utime(catalog, (timestamp, timestamp))
        return catalog

    def test_selects_last_good_catalog_within_72_hours(self) -> None:
        as_of = datetime(2026, 7, 23, 0, 0, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as temporary:
            runs_root = Path(temporary)
            current_run = runs_root / "sources_current"
            current_run.mkdir()
            expected = self.write_catalog(
                runs_root,
                "sources_previous",
                observed_date="2026-07-20",
                captured_at=as_of - timedelta(hours=72),
            )

            selected = run_daily.latest_tag_catalog(
                runs_root,
                current_run=current_run,
                as_of=as_of,
            )

            self.assertIsNotNone(selected)
            assert selected is not None
            catalog, observed, captured_at = selected
            self.assertEqual(catalog, expected)
            self.assertEqual(observed.isoformat(), "2026-07-20")
            self.assertEqual(captured_at, as_of - timedelta(hours=72))

    def test_rejects_stale_or_future_catalog(self) -> None:
        as_of = datetime(2026, 7, 23, 0, 0, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as temporary:
            runs_root = Path(temporary)
            current_run = runs_root / "sources_current"
            current_run.mkdir()
            self.write_catalog(
                runs_root,
                "sources_stale",
                observed_date="2026-07-19",
                captured_at=as_of - timedelta(hours=72, seconds=1),
            )
            self.write_catalog(
                runs_root,
                "sources_future",
                observed_date="2026-07-23",
                captured_at=as_of + timedelta(seconds=1),
            )

            self.assertIsNone(
                run_daily.latest_tag_catalog(
                    runs_root,
                    current_run=current_run,
                    as_of=as_of,
                )
            )

    def test_rejects_future_observed_date_with_valid_capture_time(self) -> None:
        as_of = datetime(2026, 7, 23, 0, 0, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as temporary:
            runs_root = Path(temporary)
            current_run = runs_root / "sources_current"
            current_run.mkdir()
            self.write_catalog(
                runs_root,
                "sources_future_observation",
                observed_date="2026-07-24",
                captured_at=as_of,
            )

            self.assertIsNone(
                run_daily.latest_tag_catalog(
                    runs_root,
                    current_run=current_run,
                    as_of=as_of,
                )
            )

    def test_rejects_manifest_without_persisted_capture_time(self) -> None:
        as_of = datetime(2026, 7, 23, 0, 0, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as temporary:
            runs_root = Path(temporary)
            current_run = runs_root / "sources_current"
            current_run.mkdir()
            run_root = runs_root / "sources_legacy"
            run_root.mkdir()
            catalog = run_root / "tag-catalog.jsonl"
            catalog.write_text('{"setId":"base1"}\n', encoding="utf-8")
            (run_root / "tag-populations.manifest.json").write_text(
                json.dumps({"observedDate": "2026-07-22"}),
                encoding="utf-8",
            )

            self.assertIsNone(
                run_daily.latest_tag_catalog(
                    runs_root,
                    current_run=current_run,
                    as_of=as_of,
                )
            )

    def test_rejects_malformed_manifest(self) -> None:
        as_of = datetime(2026, 7, 23, 0, 0, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as temporary:
            runs_root = Path(temporary)
            current_run = runs_root / "sources_current"
            current_run.mkdir()
            run_root = runs_root / "sources_malformed"
            run_root.mkdir()
            (run_root / "tag-catalog.jsonl").write_text("{}\n", encoding="utf-8")
            (run_root / "tag-populations.manifest.json").write_text("[]", encoding="utf-8")

            self.assertIsNone(
                run_daily.latest_tag_catalog(
                    runs_root,
                    current_run=current_run,
                    as_of=as_of,
                )
            )

    def test_never_reuses_partial_current_run(self) -> None:
        as_of = datetime(2026, 7, 23, 0, 0, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as temporary:
            runs_root = Path(temporary)
            current_run = runs_root / "sources_current"
            self.write_catalog(
                runs_root,
                current_run.name,
                observed_date="2026-07-23",
                captured_at=as_of,
            )

            self.assertIsNone(
                run_daily.latest_tag_catalog(
                    runs_root,
                    current_run=current_run,
                    as_of=as_of,
                )
            )


if __name__ == "__main__":
    unittest.main()
