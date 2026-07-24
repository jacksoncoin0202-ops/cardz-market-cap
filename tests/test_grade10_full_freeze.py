from __future__ import annotations

import hashlib
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "pipelines" / "grade10_full_freeze.py"
SPEC = importlib.util.spec_from_file_location("cardz_grade10_full_freeze", MODULE_PATH)
assert SPEC and SPEC.loader
freeze = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(freeze)


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def row(source: str, external_id: str) -> dict[str, object]:
    return {
        "url": f"https://private.invalid/card/{source}/{external_id}",
        "priceUsd": 10,
    }


class Grade10FullFreezeTests(unittest.TestCase):
    def make_source(self, root: Path) -> None:
        write_json(
            root / "index" / "ptcg" / "constituents.json",
            {"rows": [row("snkrdunk", "101"), row("ebay", "alt-1")]},
        )
        write_json(
            root / "index" / "opcg" / "constituents.json",
            {"rows": [row("snkrdunk", "101"), row("snkrdunk", "202")]},
        )
        write_json(root / "_state" / "last_run.json", {"lastRun": "2026-07-22T21:46:34+00:00", "cardCount": 3})
        for source, external_id in (("snkrdunk", "101"), ("altxyz", "alt-1"), ("snkrdunk", "202")):
            write_json(
                root / "cards" / source / external_id / "populations.json",
                {"population": [{"gradeName": "PSA", "total": 100, "topGrade": 10}]},
            )
        write_json(root / "cards" / "snkrdunk" / "101" / "asset_info.json", {"name": "one"})
        write_json(root / "cards" / "snkrdunk" / "202" / "asset_info.json", {"name": "two"})

    def test_full_freeze_is_immutable_and_quarantines_only_missing_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            landing = root / "landing"
            manifest_path = root / "g10-full-freeze.json"
            self.make_source(source)
            write_json(manifest_path, {"retainedHistoricalAudit": {"candidateCount": 3}, "intake": {"quarantined": 0}})

            result = freeze.freeze(
                source,
                landing,
                manifest_path,
                expected_cards=3,
                expected_asset_info=2,
            )
            replay = freeze.freeze(
                source,
                landing,
                manifest_path,
                expected_cards=3,
                expected_asset_info=2,
            )

            self.assertFalse(result["replayed"])
            self.assertTrue(replay["replayed"])
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest["coverage"], {"assetInfo": 2, "cardCount": 3, "populations": 3, "quarantined": 1})
            self.assertEqual(manifest["quarantine"], [{"externalId": "alt-1", "reason": "missing_asset_info", "sourceCode": "altxyz"}])
            self.assertEqual(manifest["retainedHistoricalAudit"], {"candidateCount": 3})
            self.assertNotIn("intake", manifest)
            run_root = landing / "g10" / "full" / manifest["runId"]
            self.assertTrue((run_root / "payload" / "cards" / "snkrdunk" / "101" / "asset_info.json").is_file())
            self.assertEqual(
                hashlib.sha256((run_root / "manifest.json").read_bytes()).hexdigest(),
                manifest["landingManifestSha256"],
            )
            self.assertEqual(freeze.verify(manifest_path, landing), manifest)

    def test_freeze_fails_closed_for_missing_population(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            self.make_source(source)
            (source / "cards" / "snkrdunk" / "202" / "populations.json").unlink()

            with self.assertRaisesRegex(freeze.FreezeError, "population coverage"):
                freeze.freeze(source, root / "landing", root / "manifest.json", expected_cards=3, expected_asset_info=2)

    def test_freeze_rejects_secret_named_source_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            self.make_source(source)
            (source / ".env").write_text("SECRET=not-copied", encoding="utf-8")

            with self.assertRaisesRegex(freeze.FreezeError, "secret-like source file"):
                freeze.freeze(source, root / "landing", root / "manifest.json", expected_cards=3, expected_asset_info=2)

    def test_freeze_rejects_secret_json_key(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            self.make_source(source)
            write_json(source / "cards" / "snkrdunk" / "101" / "unexpected.json", {"api_key": "not-copied"})

            with self.assertRaisesRegex(freeze.FreezeError, "secret-like JSON key"):
                freeze.freeze(source, root / "landing", root / "manifest.json", expected_cards=3, expected_asset_info=2)


if __name__ == "__main__":
    unittest.main()
