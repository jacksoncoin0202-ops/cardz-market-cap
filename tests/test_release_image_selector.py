from __future__ import annotations

import hashlib
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

import release_image_selector as selector  # noqa: E402


def write_card(path: Path, color: tuple[int, int, int]) -> str:
    Image.new("RGBA", (429, 600), (*color, 255)).save(path, "WEBP")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def geometry_pass(_path: Path) -> dict[str, object]:
    return {"status": "passed", "reasons": [], "widthPx": 429, "heightPx": 600}


def no_sample(_path: Path, _card_id: str) -> None:
    return None


class ReleaseImageSelectorTests(unittest.TestCase):
    def candidate(self, asset_id: int, digest: str, **overrides: object) -> dict[str, object]:
        row: dict[str, object] = {
            "asset_id": asset_id,
            "content_sha256": digest,
            "image_kind": "raw_front",
            "width_px": 429,
            "height_px": 600,
            "source_version_sha256": "a" * 64,
            "source_path": "https://example.test/card.png",
            "pointer_public_allowed": 1,
            "registered_rejection_content_sha256": None,
            "qc": {
                "semantic_match_status": "metadata_exact_unreviewed",
                "card_number_match": 1,
                "language_match": 1,
                "tcg_match": 1,
                "raw_front_confirmed": 1,
                "public_allowed": 1,
                "rejection_reason": None,
                "qc_version": "candidate-v1",
            },
        }
        row.update(overrides)
        return row

    def select(self, rows: list[dict[str, object]], assets: Path, **kwargs: object) -> dict[str, object]:
        return selector.select_release_image(
            rows,
            assets_root=assets,
            card_id="cmc_card",
            tcg_code="pokemon",
            geometry_inspector=geometry_pass,
            sample_scanner=no_sample,
            **kwargs,
        )

    def test_relaxed_prefers_confirmed_then_source_exact_and_receipt_is_stable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            assets = Path(temporary)
            low = write_card(assets / "low.webp", (1, 2, 3))
            exact = write_card(assets / "exact.webp", (4, 5, 6))
            confirmed = write_card(assets / "confirmed.webp", (7, 8, 9))
            for digest, label in ((low, "low"), (exact, "exact"), (confirmed, "confirmed")):
                (assets / f"{digest}.webp").write_bytes((assets / f"{label}.webp").read_bytes())
            rows = [
                self.candidate(30, low),
                self.candidate(20, exact, qc={**self.candidate(20, exact)["qc"], "semantic_match_status": "source_id_exact"}),
                self.candidate(10, confirmed, qc={**self.candidate(10, confirmed)["qc"], "semantic_match_status": "human_or_vision_confirmed", "qc_version": "human-review-v2"}),
            ]
            first = self.select(rows, assets)
            second = self.select(list(reversed(rows)), assets)
            self.assertEqual(first["chosenContentSha256"], confirmed)
            self.assertEqual(first["candidateSetSha256"], second["candidateSetSha256"])
            self.assertEqual(first["receiptSha256"], second["receiptSha256"])
            chosen = first["chosen"]
            assert isinstance(chosen, dict)
            self.assertEqual(chosen["score"]["humanOrVisionConfirmed"], 1)
            self.assertRegex(str(first["receiptSha256"]), r"^[0-9a-f]{64}$")

    def test_relaxed_allows_legacy_false_and_unreviewed_as_warnings(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            assets = Path(temporary)
            digest = write_card(assets / "candidate.webp", (10, 20, 30))
            (assets / f"{digest}.webp").write_bytes((assets / "candidate.webp").read_bytes())
            row = self.candidate(
                1,
                digest,
                pointer_public_allowed=0,
                qc={**self.candidate(1, digest)["qc"], "public_allowed": 0},
            )
            relaxed = self.select([row], assets)
            self.assertEqual(relaxed["chosenContentSha256"], digest)
            chosen = relaxed["chosen"]
            assert isinstance(chosen, dict)
            self.assertIn("legacy_qc_public_allowed_false", chosen["warnings"])
            self.assertIn("semantic_unreviewed", chosen["warnings"])
            strict = self.select([row], assets, profile=selector.STRICT_PROFILE)
            self.assertIsNone(strict["chosenContentSha256"])

    def test_hard_exclusions_cannot_be_selected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            assets = Path(temporary)
            digest = write_card(assets / "base.webp", (40, 50, 60))
            (assets / f"{digest}.webp").write_bytes((assets / "base.webp").read_bytes())
            rows = [
                self.candidate(1, digest, image_kind="slab_front"),
                self.candidate(2, digest, rights_public_allowed=False),
                self.candidate(3, digest, private_object_key="sample-template.webp"),
                self.candidate(4, digest, qc={**self.candidate(4, digest)["qc"], "semantic_match_status": "human_rejected"}),
                self.candidate(5, digest, registered_rejection_content_sha256=digest),
            ]
            result = self.select(rows, assets, hash_owners={digest: [("cmc_card", "pokemon"), ("cmc_other", "one-piece")]})
            self.assertIsNone(result["chosenContentSha256"])
            for row in result["candidates"]:
                self.assertTrue(row["hardBlockers"])

    def test_tie_breaks_by_asset_id_then_hash(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            assets = Path(temporary)
            first = write_card(assets / "first.webp", (70, 80, 90))
            second = write_card(assets / "second.webp", (90, 80, 70))
            for digest, label in ((first, "first"), (second, "second")):
                (assets / f"{digest}.webp").write_bytes((assets / f"{label}.webp").read_bytes())
            rows = [self.candidate(9, second), self.candidate(3, first)]
            result = self.select(rows, assets)
            self.assertEqual(result["chosenContentSha256"], first)
            same_id = self.select([self.candidate(7, second), self.candidate(7, first)], assets)
            self.assertEqual(same_id["chosenContentSha256"], min(first, second))

    def test_invalid_file_hash_and_geometry_failure_are_hard_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            assets = Path(temporary)
            digest = write_card(assets / "actual.webp", (11, 12, 13))
            (assets / f"{digest}.webp").write_bytes(b"not the hashed image")
            invalid = self.select([self.candidate(1, digest)], assets)
            self.assertIsNone(invalid["chosenContentSha256"])
            self.assertIn("image_asset_hash_mismatch", invalid["candidates"][0]["hardBlockers"])
            (assets / f"{digest}.webp").write_bytes((assets / "actual.webp").read_bytes())
            geometry_failed = selector.select_release_image(
                [self.candidate(1, digest)],
                assets_root=assets,
                card_id="cmc_card",
                tcg_code="pokemon",
                geometry_inspector=lambda _path: {"status": "failed", "reasons": ["fixture"]},
                sample_scanner=no_sample,
            )
            self.assertIsNone(geometry_failed["chosenContentSha256"])
            self.assertIn("image_canvas_geometry_invalid", geometry_failed["candidates"][0]["hardBlockers"])


if __name__ == "__main__":
    unittest.main()
