from __future__ import annotations

import hashlib
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))
import qc_public_asset_materialize as subject


def card_bytes() -> bytes:
    image = Image.new("RGBA", (429, 600), (0, 0, 0, 0))
    ImageDraw.Draw(image).rounded_rectangle((3, 4, 425, 594), radius=25, fill=(25, 50, 75, 255))
    import io
    data = io.BytesIO(); image.save(data, format="WEBP", lossless=True); return data.getvalue()


class MaterializerTests(unittest.TestCase):
    def report(self) -> dict:
        digest = "a" * 64
        printing = hashlib.sha256(b"pokemon|ja|set|1|base|normal|foil").hexdigest()
        return {"schemaVersion": 1, "readOnly": True, "database": {"authority": "canonical_mysql", "name": "cardz_market_cap"}, "runId": "qc_ok", "counts": {"catalog": 1, "releaseReadyQualified": 1}, "cards": [{"decision": "passed", "segment": "qualified", "variantId": 7, "facts": {"image": {"assetId": 9, "contentSha256": digest}, "identity": {"variantId": 7, "printingSha256": printing, "cardLanguage": "ja"}}}]}

    def row(self, digest: str, source: Path) -> dict:
        return {"variant_id": 7, "asset_id": 9, "tcg_code": "pokemon", "card_language": "ja", "set_name": "set", "collector_number": "1", "printing_tcg_code": "pokemon", "printing_card_language": "ja", "printing_set_name": "set", "printing_collector_number": "1", "edition_code": "base", "parallel_code": "normal", "finish_code": "foil", "canonical_printing_sha256": hashlib.sha256(b"pokemon|ja|set|1|base|normal|foil").hexdigest(), "printing_identity_status": "canonical", "printing_hash_count": 1, "printing_tuple_count": 1, "image_kind": "raw_front", "content_sha256": digest, "private_object_key": str(source), "width_px": 429, "height_px": 600, "source_version_sha256": "x", "semantic_match_status": "human_or_vision_confirmed", "card_number_match": 1, "language_match": 1, "tcg_match": 1, "raw_front_confirmed": 1, "public_allowed": 1, "pointer_public_allowed": 1, "qc_version": "human-review-v2", "source_path": "https://snkrdunk.com/card.webp"}

    def test_plan_accepts_only_passing_report_and_complete_db_row(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            source = Path(raw) / "source.webp"; data = card_bytes(); source.write_bytes(data)
            row = self.row(hashlib.sha256(data).hexdigest(), source)
            report = self.report(); report["cards"][0]["facts"]["image"]["contentSha256"] = row["content_sha256"]
            with patch.object(subject, "_fetch_rows", return_value=[row]):
                plan = subject.build_plan(object(), report, Path(raw))
            self.assertEqual(plan["items"][0]["assetId"], 9)

    def test_plan_fails_closed_on_language_or_public_pointer(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            source = Path(raw) / "source.webp"; data = card_bytes(); source.write_bytes(data)
            row = self.row(hashlib.sha256(data).hexdigest(), source); row["language_match"] = 0
            report = self.report(); report["cards"][0]["facts"]["image"]["contentSha256"] = row["content_sha256"]
            with patch.object(subject, "_fetch_rows", return_value=[row]):
                plan = subject.build_plan(object(), report, Path(raw))
            self.assertEqual(plan["items"][0]["status"], "blocked")
            self.assertIn("db_qc_or_pointer_not_public", plan["items"][0]["reasons"])

    def test_staging_accepts_legacy_qc_before_current_review_upgrade(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            source = Path(raw) / "source.webp"; data = card_bytes(); source.write_bytes(data)
            row = self.row(hashlib.sha256(data).hexdigest(), source)
            row["qc_version"] = "human-review-v1"
            report = self.report()
            report["cards"][0]["facts"]["image"]["contentSha256"] = row["content_sha256"]
            with patch.object(subject, "_fetch_rows", return_value=[row]):
                plan = subject.build_plan(object(), report, Path(raw))
            self.assertEqual(plan["items"][0]["assetId"], 9)

    def test_only_exact_report_passes_are_selected(self) -> None:
        self.assertEqual(list(subject.passed_targets(self.report())), [7])

    def test_invalid_report_raises(self) -> None:
        report = self.report(); report["database"]["name"] = "other"
        with self.assertRaisesRegex(ValueError, "canonical_db_qc_report_invalid"):
            subject.passed_targets(report)

    def test_wrong_cas_target_is_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            source = Path(raw) / "source.webp"; data = card_bytes(); source.write_bytes(data)
            row = self.row(hashlib.sha256(data).hexdigest(), source)
            for key, bad in (("assetId", 10), ("contentSha256", "b" * 64), ("printingSha256", "c" * 64), ("cardLanguage", "en")):
                with self.subTest(key=key):
                    report = self.report(); report["cards"][0]["facts"]["image"]["contentSha256"] = row["content_sha256"]
                    facts = report["cards"][0]["facts"]
                    facts["image" if key in {"assetId", "contentSha256"} else "identity"][key] = bad
                    with patch.object(subject, "_fetch_rows", return_value=[row]):
                        plan = subject.build_plan(object(), report, Path(raw))
                    self.assertEqual(plan["items"][0]["status"], "blocked")

    def test_write_is_content_addressed_and_second_run_noop(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw); source = root / "source.webp"; data = card_bytes(); source.write_bytes(data)
            digest = hashlib.sha256(data).hexdigest()
            plan = {"items": [{"variantId": 7, "assetId": 9, "contentSha256": digest, "source": str(source)}]}
            assets = root / "public" / "market-assets"
            self.assertEqual(subject.materialize(plan, assets), {"written": 1, "noOp": 0, "blocked": 0})
            self.assertEqual(subject.materialize(plan, assets), {"written": 0, "noOp": 1, "blocked": 0})
            with Image.open(assets / f"{digest}_200.webp") as image:
                self.assertEqual(image.size, (200, 280))
            with Image.open(assets / f"{digest}_600.webp") as image:
                self.assertEqual(image.size, (429, 600))

    def test_existing_conflicting_master_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw); source = root / "source.webp"; data = card_bytes(); source.write_bytes(data)
            digest = hashlib.sha256(data).hexdigest(); assets = root / "assets"; assets.mkdir()
            (assets / f"{digest}.webp").write_bytes(b"wrong")
            with self.assertRaisesRegex(ValueError, "existing_master_conflict"):
                subject.materialize({"items": [{"contentSha256": digest, "source": str(source)}]}, assets)

    def test_existing_master_backfills_missing_derivatives(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw); source = root / "source.webp"; data = card_bytes(); source.write_bytes(data)
            digest = hashlib.sha256(data).hexdigest(); assets = root / "assets"; assets.mkdir()
            (assets / f"{digest}.webp").write_bytes(data)
            result = subject.materialize({"items": [{"contentSha256": digest, "source": str(source)}]}, assets)
            self.assertEqual(result, {"written": 1, "noOp": 0, "blocked": 0})
            self.assertTrue((assets / f"{digest}_200.webp").is_file())
            self.assertTrue((assets / f"{digest}_600.webp").is_file())

    def test_existing_conflicting_derivative_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw); source = root / "source.webp"; data = card_bytes(); source.write_bytes(data)
            digest = hashlib.sha256(data).hexdigest(); assets = root / "assets"; assets.mkdir()
            (assets / f"{digest}.webp").write_bytes(data)
            (assets / f"{digest}_200.webp").write_bytes(b"wrong")
            with self.assertRaisesRegex(ValueError, "existing_asset_conflict"):
                subject.materialize({"items": [{"contentSha256": digest, "source": str(source)}]}, assets)

    def test_near_identical_existing_derivative_encoding_is_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw); source = root / "source.webp"; data = card_bytes(); source.write_bytes(data)
            digest = hashlib.sha256(data).hexdigest(); assets = root / "assets"; assets.mkdir()
            (assets / f"{digest}.webp").write_bytes(data)
            with Image.open(source) as image:
                alternate = image.copy()
                alternate.thumbnail((600, 1200), Image.Resampling.LANCZOS)
                alternate.save(
                    assets / f"{digest}_600.webp",
                    "WEBP",
                    quality=68,
                    method=4,
                )
            result = subject.materialize(
                {"items": [{"contentSha256": digest, "source": str(source)}]},
                assets,
            )
            self.assertEqual(result, {"written": 1, "noOp": 0, "blocked": 0})
            self.assertTrue((assets / f"{digest}_200.webp").is_file())
