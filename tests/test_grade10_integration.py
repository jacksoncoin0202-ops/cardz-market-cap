from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "integrations" / "grade10" / "run_service.py"
SPEC = importlib.util.spec_from_file_location("cardz_grade10_service", MODULE_PATH)
assert SPEC and SPEC.loader
service = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(service)


class Grade10IntegrationTests(unittest.TestCase):
    def test_vendored_dependency_manifest_is_exact(self) -> None:
        report = service.self_check()
        self.assertEqual(report["status"], "ready")
        self.assertIn("OPERATOR_GUIDE.md", report["verifiedFiles"])
        self.assertIn("run_daily.bat", report["verifiedFiles"])

    def test_manifest_mismatch_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "dependency.py"
            source.write_text("value = 1\n", encoding="utf-8")
            manifest = root / "UPSTREAM_MANIFEST.json"
            manifest.write_text(
                json.dumps(
                    {
                        "files": [
                            {
                                "path": source.name,
                                "bytes": source.stat().st_size,
                                "sha256": "0" * 64,
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            with mock.patch.object(service, "ROOT", root), mock.patch.object(service, "MANIFEST_PATH", manifest):
                with self.assertRaisesRegex(RuntimeError, "hash mismatch"):
                    service.self_check()

    def test_collection_gate_requires_current_complete_indexes_and_details(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            data = root / "data"
            now = datetime.now(timezone.utc)
            cards = [
                ("ptcg", "snkrdunk", "1"),
                ("ptcg100", "ebay", "opaque"),
                ("opcg", "snkrdunk", "2"),
            ]
            for index_name, source, external_id in cards:
                path = data / "index" / index_name / "constituents.json"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(
                    json.dumps(
                        {
                            "rows": [
                                {
                                    "url": f"https://private.invalid/research/card/{source}/{external_id}"
                                }
                            ]
                        }
                    ),
                    encoding="utf-8",
                )
                storage = "altxyz" if source == "ebay" else source
                detail = data / "cards" / storage / external_id
                detail.mkdir(parents=True)
                (detail / "asset_info.json").write_text("{}", encoding="utf-8")
                (detail / "populations.json").write_text("{}", encoding="utf-8")
            state = data / "_state" / "last_run.json"
            state.parent.mkdir(parents=True)
            state.write_text(
                json.dumps({"cardCount": 3, "lastRun": now.isoformat()}),
                encoding="utf-8",
            )
            with mock.patch.object(service, "ROOT", root), mock.patch.object(service, "DATA_ROOT", data):
                result = service.validate_collection(
                    3,
                    scope="full",
                    not_before=now - timedelta(seconds=1),
                )
                self.assertEqual(result["indexedCards"], 3)
                self.assertEqual(result["detailCards"], 3)
                (data / "cards" / "snkrdunk" / "2" / "populations.json").unlink()
                with self.assertRaisesRegex(RuntimeError, "detail coverage failed"):
                    service.validate_collection(
                        3,
                        scope="full",
                        not_before=now - timedelta(seconds=1),
                    )


if __name__ == "__main__":
    unittest.main()
