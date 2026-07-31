from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from contextlib import nullcontext
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
                self.assertEqual(result["populationCards"], 3)
                (data / "cards" / "snkrdunk" / "2" / "populations.json").unlink()
                with self.assertRaisesRegex(RuntimeError, "detail coverage failed"):
                    service.validate_collection(
                        3,
                        scope="full",
                        not_before=now - timedelta(seconds=1),
                    )

    def test_missing_detail_cards_includes_absent_and_invalid_documents(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            data = Path(temporary)
            complete = data / "cards" / "snkrdunk" / "1"
            complete.mkdir(parents=True)
            (complete / "asset_info.json").write_text("{}", encoding="utf-8")
            (complete / "populations.json").write_text("{}", encoding="utf-8")
            invalid = data / "cards" / "altxyz" / "opaque"
            invalid.mkdir(parents=True)
            (invalid / "asset_info.json").write_text("{", encoding="utf-8")
            (invalid / "populations.json").write_text("{}", encoding="utf-8")
            cards = {("snkrdunk", "1"), ("snkrdunk", "2"), ("altxyz", "opaque")}

            with mock.patch.object(service, "DATA_ROOT", data):
                self.assertEqual(
                    service.missing_detail_cards(cards),
                    [("altxyz", "opaque"), ("snkrdunk", "2")],
                )

    def test_collection_allows_explicit_asset_null_when_population_is_complete(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            data = root / "data"
            now = datetime.now(timezone.utc)
            rows = []
            for external_id in ("1", "2", "3"):
                rows.append(
                    {
                        "url": f"https://private.invalid/research/card/snkrdunk/{external_id}"
                    }
                )
                detail = data / "cards" / "snkrdunk" / external_id
                detail.mkdir(parents=True)
                (detail / "populations.json").write_text("{}", encoding="utf-8")
                if external_id != "3":
                    (detail / "asset_info.json").write_text("{}", encoding="utf-8")
            for index_name, index_rows in (
                ("ptcg", rows),
                ("ptcg100", []),
                ("opcg", []),
            ):
                path = data / "index" / index_name / "constituents.json"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps({"rows": index_rows}), encoding="utf-8")
            state = data / "_state" / "last_run.json"
            state.parent.mkdir(parents=True)
            state.write_text(
                json.dumps({"cardCount": 3, "lastRun": now.isoformat()}),
                encoding="utf-8",
            )

            with mock.patch.object(service, "DATA_ROOT", data):
                result = service.validate_collection(
                    3,
                    scope="full",
                    not_before=now - timedelta(seconds=1),
                    minimum_asset_info=2,
                )

            self.assertEqual(result["assetInfoCards"], 2)
            self.assertEqual(result["populationCards"], 3)
            self.assertEqual(
                result["missingAssetInfo"],
                [{"source": "snkrdunk", "externalId": "3"}],
            )

    def test_collection_repairs_only_missing_details_before_strict_validation(self) -> None:
        result = {"status": "collected", "detailCards": 600}
        missing = [("altxyz", "opaque"), ("snkrdunk", "123")]
        with (
            mock.patch.object(service, "singleton_lock", return_value=nullcontext()),
            mock.patch.object(service, "run_script") as run_script,
            mock.patch.object(service, "missing_detail_cards", return_value=missing),
            mock.patch.object(service, "repair_missing_details", return_value=2) as repair,
            mock.patch.object(service, "validate_collection", return_value=result) as validate,
        ):
            self.assertEqual(
                service.collect("full", skip_images=True, expected_cards=600, timeout=900),
                result,
            )

        run_script.assert_called_once_with(service.SCRAPER, ["--skip-images"], 900)
        repair.assert_called_once_with(missing, skip_images=True)
        validate.assert_called_once()

    def test_targeted_repair_preserves_index_identity_types(self) -> None:
        numeric = {"source": "snkrdunk", "id": 123}
        opaque = {"source": "altxyz", "id": "opaque"}
        scraper = mock.Mock()
        scraper.enumerate_cards.return_value = [numeric, opaque]
        with mock.patch.object(service, "load_scraper", return_value=scraper):
            repaired = service.repair_missing_details(
                [("snkrdunk", "123"), ("altxyz", "opaque")],
                skip_images=False,
            )

        self.assertEqual(repaired, 2)
        scraper.scrape_card.assert_has_calls(
            [
                mock.call(numeric, skip_images=False),
                mock.call(opaque, skip_images=False),
            ]
        )


if __name__ == "__main__":
    unittest.main()
