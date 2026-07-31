# -*- coding: utf-8 -*-
"""卡圖衍生尺寸引用 regression（2026-07-26）。

每個 image block 有 `src`（master）加 `variants`（`{"200": ..., "600": ...}`）。
以前 `verify_images.verify()` 同 `g10_public_snapshot.quarantine_unreferenced_assets()`
兩邊都只收 `src` 就當「有人引用」，於是每張卡兩個生效中嘅衍生圖被判為孤兒：
verify 報 unreferenced、quarantine 直接搬走，全站細尺寸卡圖即刻爛。

實測落地嘅比例：360 張卡應該係 1080 個檔有人引用，舊邏輯只數到 360 個。

呢度同時釘住反方向——修完之後，真正嘅陳年孤兒檔仍然要照捉。為咗收 false
positive 而閹咗成個檢查，係比原本個 bug 更差嘅結果。
"""

from __future__ import annotations

import io
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "pipelines"))

from verify_images import referenced_asset_names, verify  # noqa: E402
from g10_public_snapshot import quarantine_unreferenced_assets  # noqa: E402


def webp_bytes(width: int, height: int) -> bytes:
    output = io.BytesIO()
    Image.new("RGBA", (width, height), (10, 20, 30, 255)).save(output, format="WEBP", lossless=True)
    return output.getvalue()


def card(sha: str, width: int = 40, height: int = 56) -> dict:
    return {
        "id": f"card-{sha[:8]}",
        "image": {
            "src": f"/market-assets/{sha}.webp",
            "sha256": sha,
            "width": width,
            "height": height,
            "kind": "raw_front",
            "variants": {
                "200": f"/market-assets/{sha}_200.webp",
                "600": f"/market-assets/{sha}_600.webp",
            },
        },
    }


class ReferencedAssetNamesTests(unittest.TestCase):
    def test_collects_master_and_every_variant(self) -> None:
        sha = "a" * 64
        names = referenced_asset_names({"top100": [card(sha)], "watchlist": []})
        self.assertEqual(
            names,
            {f"{sha}.webp", f"{sha}_200.webp", f"{sha}_600.webp"},
        )

    def test_covers_watchlist_not_only_top100(self) -> None:
        top, watch = "b" * 64, "c" * 64
        names = referenced_asset_names({"top100": [card(top)], "watchlist": [card(watch)]})
        self.assertIn(f"{watch}_600.webp", names)
        self.assertIn(f"{top}_600.webp", names)

    def test_survives_missing_or_malformed_image_blocks(self) -> None:
        sha = "d" * 64
        snapshot = {
            "top100": [
                card(sha),
                {"id": "no-image"},
                {"id": "null-image", "image": None},
                {"id": "no-variants", "image": {"src": f"/market-assets/{'e' * 64}.webp"}},
                {"id": "variants-not-dict", "image": {"src": "", "variants": ["nope"]}},
            ],
            "watchlist": [],
        }
        names = referenced_asset_names(snapshot)
        self.assertIn(f"{sha}_200.webp", names)
        self.assertIn(f"{'e' * 64}.webp", names)
        self.assertNotIn("", names)

    def test_variant_suffixes_are_read_from_the_snapshot_not_hardcoded(self) -> None:
        # 將來加尺寸（例如 900）唔應該要改呢個函數。
        sha = "f" * 64
        block = card(sha)
        block["image"]["variants"]["900"] = f"/market-assets/{sha}_900.webp"
        self.assertIn(f"{sha}_900.webp", referenced_asset_names({"top100": [block], "watchlist": []}))


class VerifyExtrasTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.temp, ignore_errors=True)
        self.assets = self.temp / "market-assets"
        self.assets.mkdir()
        master = webp_bytes(40, 56)
        import hashlib

        self.sha = hashlib.sha256(master).hexdigest()
        (self.assets / f"{self.sha}.webp").write_bytes(master)
        for suffix, width in (("200", 20), ("600", 30)):
            (self.assets / f"{self.sha}_{suffix}.webp").write_bytes(webp_bytes(width, width))
        self.snapshot_path = self.temp / "snapshot.json"
        self.snapshot_path.write_text(
            json.dumps({"top100": [card(self.sha)], "watchlist": []}), encoding="utf-8"
        )

    def unreferenced_errors(self) -> list[str]:
        return [error for error in verify(self.snapshot_path, self.assets) if "unreferenced" in error]

    def test_live_variants_are_not_reported_unreferenced(self) -> None:
        self.assertEqual(self.unreferenced_errors(), [])

    def test_genuinely_stale_files_are_still_reported(self) -> None:
        # 修正唔可以順手閹咗個檢查本身。
        (self.assets / f"{'9' * 64}_600.webp").write_bytes(webp_bytes(30, 30))
        errors = self.unreferenced_errors()
        self.assertEqual(len(errors), 1)
        self.assertIn("1 unreferenced", errors[0])

    def test_verified_top_n_accepts_one_to_one_hundred_cards(self) -> None:
        errors = verify(self.snapshot_path, self.assets, allow_unreferenced=True)
        self.assertFalse(any("top100 does not contain" in error for error in errors), errors)

    def test_strict_semantic_qc_is_bound_to_the_snapshot_card_id(self) -> None:
        manifest = self.temp / "image-qc.json"
        manifest.write_text(
            json.dumps(
                {
                    "records": [
                        {
                            "publicId": "wrong-card-id",
                            "contentSha256": self.sha,
                            "publicAllowed": True,
                            "semanticMatchStatus": "human_or_vision_confirmed",
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        errors = verify(
            self.snapshot_path,
            self.assets,
            manifest,
            strict_semantic=True,
            allow_unreferenced=True,
        )
        self.assertTrue(any("card-bound" in error for error in errors), errors)
        self.assertTrue(any("canvas geometry invalid" in error for error in errors), errors)


class QuarantineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.temp, ignore_errors=True)
        # quarantine_unreferenced_assets 只肯處理 .../public/market-assets。
        self.assets = self.temp / "public" / "market-assets"
        self.assets.mkdir(parents=True)
        self.quarantine = self.temp / "quarantine"
        self.sha = "1" * 64
        for name in (f"{self.sha}.webp", f"{self.sha}_200.webp", f"{self.sha}_600.webp"):
            (self.assets / name).write_bytes(b"live")
        self.stale = self.assets / f"{'8' * 64}_200.webp"
        self.stale.write_bytes(b"stale")
        self.snapshot = {"top100": [card(self.sha)], "watchlist": []}

    def test_live_variants_survive_and_only_stale_moves(self) -> None:
        moved = quarantine_unreferenced_assets(self.assets, self.snapshot, self.quarantine)
        self.assertEqual(moved, 1)
        self.assertFalse(self.stale.exists())
        self.assertTrue((self.quarantine / self.stale.name).is_file())
        for suffix in ("", "_200", "_600"):
            self.assertTrue(
                (self.assets / f"{self.sha}{suffix}.webp").is_file(),
                f"生效中嘅 {suffix or 'master'} 被錯誤 quarantine",
            )

    def test_nothing_moves_when_every_file_is_referenced(self) -> None:
        self.stale.unlink()
        self.assertEqual(quarantine_unreferenced_assets(self.assets, self.snapshot, self.quarantine), 0)
        self.assertFalse(self.quarantine.exists())


if __name__ == "__main__":
    unittest.main()
