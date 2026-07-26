"""native_image_resolver regression tests.

Lock 住嘅行為：
1. RGBA 圖入 store_native_image 後出嚟必須仲係 RGBA（唔准再壓平做 RGB）。
2. is_native_rounded 對三種樣本判定正確：原生圓角 / RGB 無 alpha / RGBA 不透明角。
3. 儲存後嘅 image block schema 同現有 snapshot image block 相容。
"""
from __future__ import annotations

import io
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

import native_image_resolver as nir  # noqa: E402


def rounded_card_png(width: int = 100, height: int = 140) -> bytes:
    """整一張真圓角 RGBA 卡圖（圓角半徑 10% 卡寬，確保 offset-2 取樣點落喺透明區）。"""
    from PIL import ImageDraw
    image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    ImageDraw.Draw(image).rounded_rectangle(
        (0, 0, width - 1, height - 1), radius=round(width * 0.10), fill=(200, 60, 60, 255)
    )
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def opaque_rgb_jpeg() -> bytes:
    image = Image.new("RGB", (100, 140), (255, 255, 255))
    output = io.BytesIO()
    image.save(output, format="JPEG")
    return output.getvalue()


def rgba_opaque_corners_png() -> bytes:
    image = Image.new("RGBA", (100, 140), (255, 255, 255, 255))
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


class IsNativeRoundedTest(unittest.TestCase):
    def test_rounded_corners_accepted(self):
        self.assertTrue(nir.is_native_rounded(rounded_card_png()))

    def test_rgb_without_alpha_rejected(self):
        self.assertFalse(nir.is_native_rounded(opaque_rgb_jpeg()))

    def test_rgba_opaque_corners_rejected(self):
        self.assertFalse(nir.is_native_rounded(rgba_opaque_corners_png()))


class StoreNativeImageTest(unittest.TestCase):
    def setUp(self):
        self.assets_backup = nir.ASSETS
        self.temp_dir = Path(tempfile.mkdtemp(prefix="native-assets-"))
        nir.ASSETS = self.temp_dir

    def tearDown(self):
        nir.ASSETS = self.assets_backup
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_alpha_preserved_end_to_end(self):
        block = nir.store_native_image(rounded_card_png(300, 420), "Test Card")
        master = Image.open(self.temp_dir / f"{block['sha256']}.webp")
        self.assertEqual(master.mode, "RGBA", "master 必須保留 alpha — 壓平就係舊 bug")
        alpha = master.getchannel("A")
        width, height = master.size
        corners = [
            alpha.getpixel((2, 2)),
            alpha.getpixel((width - 3, 2)),
            alpha.getpixel((2, height - 3)),
            alpha.getpixel((width - 3, height - 3)),
        ]
        self.assertEqual(corners, [0, 0, 0, 0])
        for suffix in ("200", "600"):
            variant = Image.open(self.temp_dir / f"{block['sha256']}_{suffix}.webp")
            self.assertEqual(variant.mode, "RGBA", f"derivative {suffix} 都要保留 alpha")

    def test_image_block_schema_compatible(self):
        block = nir.store_native_image(rounded_card_png(), "Test Card")
        self.assertTrue(block["src"].startswith("/market-assets/"))
        self.assertTrue(block["src"].endswith(".webp"))
        self.assertEqual(len(block["sha256"]), 64)
        self.assertEqual(block["kind"], "raw_front")
        self.assertEqual(block["alt"]["en"], "Test Card")
        self.assertIn("200", block["variants"])
        self.assertIn("600", block["variants"])
        self.assertGreater(block["width"], 0)
        self.assertGreater(block["height"], 0)


class NormalizeCanvasTest(unittest.TestCase):
    """統一畫布規格（梵高比卡超標準 429x600）。"""

    def setUp(self):
        self.assets_backup = nir.ASSETS
        self.temp_dir = Path(tempfile.mkdtemp(prefix="normalize-assets-"))
        nir.ASSETS = self.temp_dir

    def tearDown(self):
        nir.ASSETS = self.assets_backup
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_loose_canvas_tightened_to_std(self):
        """SNK 1000x730 大畫布、卡只佔中間 → normalize 後必須係 429x600、卡填滿。"""
        loose = Image.new("RGBA", (1000, 730), (0, 0, 0, 0))
        for x in range(320, 680):
            for y in range(100, 630):
                loose.putpixel((x, y), (60, 120, 200, 255))
        output = io.BytesIO()
        loose.save(output, format="PNG")
        normalized = nir.normalize_card_canvas(Image.open(io.BytesIO(output.getvalue())))
        self.assertEqual(normalized.size, (nir.CANVAS_W, nir.CANVAS_H))
        bbox = normalized.getchannel("A").getbbox()
        bw, bh = bbox[2] - bbox[0], bbox[3] - bbox[1]
        # 卡等比縮放：一邊頂到 fill 線、另一邊按比例（呢張樣本係直向，高度頂滿）
        self.assertGreaterEqual(max(bw / (nir.CANVAS_W * nir.CANVAS_FILL), bh / (nir.CANVAS_H * nir.CANVAS_FILL)),
                                0.98, "normalize 後卡必須填滿畫布，唔准再細張")

    def test_tight_image_unchanged_content(self):
        """緊身圓角圖 normalize 後 4 角仍透明、規格正確。"""
        normalized = nir.normalize_card_canvas(Image.open(io.BytesIO(rounded_card_png(438, 610))))
        self.assertEqual(normalized.size, (nir.CANVAS_W, nir.CANVAS_H))
        alpha = normalized.getchannel("A")
        self.assertEqual(alpha.getpixel((2, 2)), 0)
        self.assertEqual(alpha.getpixel((nir.CANVAS_W - 3, nir.CANVAS_H - 3)), 0)

    def test_store_normalized_idempotent(self):
        """已係標準規格 → store_normalized_image 回 None（每日 delta skip）。"""
        block = nir.store_native_image(rounded_card_png(438, 610), "Std Card")
        raw = (self.temp_dir / f"{block['sha256']}.webp").read_bytes()
        self.assertIsNone(nir.store_normalized_image(raw, "Std Card"))

    def test_rgb_old_image_normalized_centered(self):
        """RGB 舊圖（無 alpha）→ 置中落透明畫布，唔准去背、唔准壓平。"""
        rgb = Image.new("RGB", (632, 895), (255, 255, 255))
        output = io.BytesIO()
        rgb.save(output, format="JPEG")
        block = nir.store_normalized_image(output.getvalue(), "Old RGB Card")
        self.assertIsNotNone(block)
        self.assertEqual((block["width"], block["height"]), (nir.CANVAS_W, nir.CANVAS_H))
        master = Image.open(self.temp_dir / f"{block['sha256']}.webp")
        self.assertEqual(master.mode, "RGBA")


class RoundedCornerTest(unittest.TestCase):
    """方角卡補圓角（4 張 JA promo 長期須知，2026-07-25）。"""

    def setUp(self):
        self.assets_backup = nir.ASSETS
        self.temp_dir = Path(tempfile.mkdtemp(prefix="rounded-assets-"))
        nir.ASSETS = self.temp_dir

    def tearDown(self):
        nir.ASSETS = self.assets_backup
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_square_corners_detected_and_fixed(self):
        """std 畫布但卡身方角 → has_rounded_corners False；apply 後卡身 4 角透明、卡面內容唔郁。"""
        square = Image.new("RGBA", (nir.CANVAS_W, nir.CANVAS_H), (0, 0, 0, 0))
        for x in range(6, 423):
            for y in range(4, 595):
                square.putpixel((x, y), (180, 90, 30, 255))
        self.assertFalse(nir.has_rounded_corners(square))
        fixed = nir.apply_rounded_corners(square)
        self.assertTrue(nir.has_rounded_corners(fixed))
        alpha = fixed.getchannel("A")
        bbox = alpha.getbbox()
        left, top, right, bottom = bbox
        # 卡身 4 角透明
        self.assertLess(alpha.getpixel((left + 2, top + 2)), nir.CORNER_ALPHA_MAX)
        # 卡面中心內容保持
        self.assertEqual(fixed.getpixel(((left + right) // 2, (top + bottom) // 2)), (180, 90, 30, 255))

    def test_already_rounded_is_noop(self):
        """原生圓角卡 → has_rounded_corners True、store_rounded_image 回 None（唔准重複處理）。"""
        block = nir.store_native_image(rounded_card_png(438, 610), "Round Card")
        master = Image.open(self.temp_dir / f"{block['sha256']}.webp")
        self.assertTrue(nir.has_rounded_corners(master))
        raw = (self.temp_dir / f"{block['sha256']}.webp").read_bytes()
        self.assertIsNone(nir.store_rounded_image(raw, "Round Card"))

    def test_corner_radius_matches_van_gogh_ratio(self):
        """圓角半徑 = 6% 卡寬（梵高實測 25px / 423px）。"""
        square = Image.new("RGBA", (nir.CANVAS_W, nir.CANVAS_H), (0, 0, 0, 0))
        for x in range(6, 423):
            for y in range(4, 595):
                square.putpixel((x, y), (180, 90, 30, 255))
        fixed = nir.apply_rounded_corners(square)
        alpha = fixed.getchannel("A")
        bbox = alpha.getbbox()
        left, top = bbox[0], bbox[1]
        card_w = bbox[2] - bbox[0]
        # 頂邊中間係實色；離角 6% 卡寬以內嘅頂邊位置已透明
        mid_x = (bbox[0] + bbox[2]) // 2
        self.assertEqual(alpha.getpixel((mid_x, top + 2)), 255)
        radius = card_w * nir.CORNER_RADIUS_RATIO
        self.assertLess(alpha.getpixel((left + 2, top + 2)), nir.CORNER_ALPHA_MAX)
        # 半徑外（沿頂邊行入去 > radius）必須實色
        self.assertEqual(alpha.getpixel((left + int(radius) + 4, top + 2)), 255)


if __name__ == "__main__":
    unittest.main()
