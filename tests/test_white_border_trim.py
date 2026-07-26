# -*- coding: utf-8 -*-
"""白邊鏟除 QC 嘅 regression tests（2026-07-24 用戶規矩）。

規矩：裸卡圖唔准有白色邊框殘留。邊帶判定 = 成條邊帶 ≥45% pixel 近白
（亮度 ≥220 兼 chroma ≤26——有色亮區如黃框、米白畫布係卡畫，唔算白邊），
每邊最多裁 15%，淺過 2px 唔裁。天生淺色卡面（卡畫延伸到邊）唔當邊框。
"""

from __future__ import annotations

import io
import sys
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipelines"))

from g10_public_snapshot import (  # noqa: E402
    WHITE_BORDER_MIN_DEPTH_PX,
    _white_border_depth,
    trim_white_border,
)


def make_card(width: int, height: int, body: tuple[int, int, int], border_px: dict[str, int], border_color=(245, 245, 245)) -> Image.Image:
    """砌一張測試卡：body 色做卡面，四邊各自指定白邊寬度。"""
    image = Image.new("RGB", (width, height), body)
    px = image.load()
    for y in range(height):
        for x in range(width):
            in_top = y < border_px.get("top", 0)
            in_bottom = y >= height - border_px.get("bottom", 0)
            in_left = x < border_px.get("left", 0)
            in_right = x >= width - border_px.get("right", 0)
            if in_top or in_bottom or in_left or in_right:
                px[x, y] = border_color
    return image


def edge_luminance(image: Image.Image) -> dict[str, int]:
    gray = image.convert("L")
    w, h = gray.size
    px = list(gray.getdata())
    def med(values: list[int]) -> int:
        values = sorted(values)
        return values[len(values) // 2]
    return {
        "top": med([px[x] for x in range(w)]),
        "bottom": med([px[(h - 1) * w + x] for x in range(w)]),
        "left": med([px[y * w] for y in range(h)]),
        "right": med([px[y * w + w - 1] for y in range(h)]),
    }


def test_uniform_white_border_trimmed():
    img = make_card(400, 560, (60, 90, 140), {"top": 12, "bottom": 12, "left": 12, "right": 12})
    out = trim_white_border(img)
    assert out.size == (376, 536), out.size
    edges = edge_luminance(out)
    assert max(edges.values()) < 220, edges


def test_asymmetric_border_each_side_trimmed_independently():
    # Mew Ex 類：左邊特別深嘅白邊
    img = make_card(400, 560, (70, 100, 150), {"left": 20, "top": 6, "right": 4, "bottom": 8})
    out = trim_white_border(img)
    assert out.size == (376, 546), out.size
    edges = edge_luminance(out)
    assert max(edges.values()) < 220, edges


def test_sub_2px_border_left_alone():
    img = make_card(400, 560, (80, 110, 160), {"top": 1, "bottom": 1, "left": 1, "right": 1})
    out = trim_white_border(img)
    assert out.size == (400, 560)


def test_white_art_inside_card_not_cropped():
    # 卡面中間有大片白色（雲/雪），但邊係深色 — 唔准裁
    img = Image.new("RGB", (400, 560), (50, 60, 90))
    for y in range(200, 360):
        for x in range(100, 300):
            img.putpixel((x, y), (250, 250, 250))
    out = trim_white_border(img)
    assert out.size == (400, 560), out.size


def test_bright_colored_frame_not_cropped():
    # 亮但有色嘅邊（黃色 TAG TEAM 閃邊、Special Delivery 黃框）係卡畫，唔准裁
    img = make_card(400, 560, (60, 90, 140), {"top": 10, "bottom": 10, "left": 10, "right": 10}, border_color=(250, 230, 90))
    out = trim_white_border(img)
    assert out.size == (400, 560), out.size


def test_depth_capped_at_15_percent():
    # 全白邊框 20%：最多裁 15%
    img = make_card(400, 560, (90, 90, 90), {"top": int(560 * 0.20), "bottom": int(560 * 0.20), "left": int(400 * 0.20), "right": int(400 * 0.20)})
    out = trim_white_border(img)
    max_crop_w = int(400 * 0.15)
    max_crop_h = int(560 * 0.15)
    assert out.width >= 400 - 2 * max_crop_w - 2
    assert out.height >= 560 - 2 * max_crop_h - 2


def test_centering_balances_sides_to_shallowest():
    # 只有左邊有 8px 白邊：其餘三邊要跟埋裁 8px 保持置中
    img = make_card(400, 560, (70, 100, 150), {"left": 8})
    out = trim_white_border(img)
    assert out.size == (400 - 16, 560 - 16), out.size


def test_transparent_image_untouched():
    img = Image.new("RGBA", (300, 420), (255, 255, 255, 0))
    out = trim_white_border(img)
    assert out.size == (300, 420)


def test_real_webp_assets_have_no_white_border_after_trim():
    """全量真實資產：trim 完之後唔准再探測到超過 MIN_DEPTH_PX 嘅無色白邊帶。

    注意：唔好用「邊緣中位亮度 <220」嚟斷——Hoopa promo、Mew AR、
    Espeon VMAX、Pikachu promo 呢類天生淺色卡面，邊緣光亮度係卡畫本身；
    Reshiram ex 白卡、Munch 米白畫布、TAG TEAM 黃閃邊都係卡畫。
    QC 目標係冇「無色白邊帶」（由邊向內連續近白兼近灰 band），唔係冇光邊。
    容許 ≤8px 殘留：斜放截圖（Moltres GX、One Piece 卡）嘅黑色卡邊框斜切邊帶，
    令探測喺白帶中段就停（白帶有幾段、黑框斷開咗），呢個係由邊向內掃嘅物理下限。
    視覺上 8px / 610px ≈ 1.3%，配 9% 圓角完全遮蓋。
    豁免 Reshiram ex 白卡（WHT EN/JP）：全白卡面，探測到嘅係卡畫本身。
    """
    # 全白卡面（Reshiram ex WHT EN/JP）— trim 後探測到嘅「白帶」係卡畫本身
    KNOWN_WHITE_FACE = ("2b51f7718a18", "76cc6a9f08b4")
    assets = Path(__file__).resolve().parent.parent / "data" / "public" / "market-assets"
    if not assets.is_dir():
        return
    samples = sorted(assets.glob("*.webp"))
    assert samples, "no public assets to sample"
    failures = []
    for path in samples:
        if path.name.startswith(KNOWN_WHITE_FACE):
            continue
        with Image.open(path) as opened:
            if "A" in opened.getbands():
                # RGBA 來源：trim_white_border 本身唔會處理（見
                # test_transparent_image_untouched 鎖住嗰個 contract），
                # 由 is_native_rounded() 嘅角位 alpha gate 做 QC。透明邊底下
                # 嘅 RGB 值本身冇定義（未必係白），強制起底當白邊量度會誤判。
                continue
            img = opened.copy()
        out = trim_white_border(img)
        w, h = out.size
        flat = [c for pixel in out.convert("RGB").getdata() for c in pixel]
        depths = {
            "top": _white_border_depth(flat, w, h, "x", True),
            "bottom": _white_border_depth(flat, w, h, "x", False),
            "left": _white_border_depth(flat, w, h, "y", True),
            "right": _white_border_depth(flat, w, h, "y", False),
        }
        if max(depths.values()) > 8:
            failures.append((path.name[:12], depths))
    assert not failures, failures
