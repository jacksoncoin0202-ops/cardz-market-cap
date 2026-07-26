"""Native rounded-corner card image resolver.

搵返來源本來就有嘅 RGBA 圓角卡圖，**保留 alpha 直接入庫**，唔做去背、
唔做裁角、唔做 trim。存在嘅意義：舊 ingest 喺落圖後 ``.convert("RGB")``
壓平咗 SNK 原生圓角圖，先至要 trim 白邊 + CSS border-radius 遮醜。

來源 priority chain（實測 2026-07-24）:
1. SNK harvest cache / SNK get_master — RGBA WebP 1000x730，100% 原生圓角
2. Kado dump RGBA WebP — 約 16% set 有原生圓角，做冷門備用
3. TCGdex EN PNG — 英文卡專用（ja 同 webp 版冇 alpha，唔好用）

每張候選圖落完必過 corner-alpha gate：4 角 pixel alpha 全部 < 10 先收貨。
"""
from __future__ import annotations

import hashlib
import io
import json
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

import g10_public_snapshot as g10  # noqa: E402

ASSETS = ROOT / "data" / "public" / "market-assets"
SNK_HARVEST = ROOT / "data" / "private" / "snkrdunk_brute" / "snkrdunk_all.jsonl"
KADO_DUMP = ROOT.parent / "kado-dump"

CORNER_ALPHA_MAX = 10  # 4 角 pixel alpha 全部低過呢個值先算原生圓角
CORNER_OFFSET = 2      # 由邊退 2px 取樣，避開 AA 邊緣
MASTER_MAX = (1200, 1680)
DOWNLOAD_DELAY_S = 0.3

# 統一卡圖規格（2026-07-24 用戶規矩）：全站每張裸卡圖都係同一塊畫布，
# 梵高比卡超（rank #1，438x610 緊身圓角）做基準。SNK 原圖係 1000x730 大畫布，
# 卡只佔中間 ~36%，唔統一就會忽大忽小。呢度做 alpha-bbox crop 再置中落
# 固定畫布 —— 係「裁走多餘透明位」，唔係後製去背/裁角。
CANVAS_W = 429
CANVAS_H = 600
CANVAS_FILL = 0.985    # 卡佔畫布高度比例（留 ~0.75% 呼吸位，同梵高原圖觀感一致）
NORMALIZED_MARKER = f"std-{CANVAS_W}x{CANVAS_H}"


@dataclass(frozen=True)
class NativeCandidate:
    source: str          # "snk_cache" | "snk_master" | "kado" | "tcgdex_en"
    url: str | None      # 要下載嘅遠端 URL
    path: Path | None    # 本地已有嘅圖檔


@dataclass(frozen=True)
class NativeImageResult:
    image_block: dict[str, Any]
    source: str


def is_native_rounded(raw: bytes) -> bool:
    """品質 gate：4 角 pixel alpha 全部 < CORNER_ALPHA_MAX 先算原生圓角。"""

    with Image.open(io.BytesIO(raw)) as opened:
        if "A" not in opened.getbands():
            return False
        image = opened.convert("RGBA")
    width, height = image.size
    alpha = image.getchannel("A")
    points = (
        (CORNER_OFFSET, CORNER_OFFSET),
        (width - 1 - CORNER_OFFSET, CORNER_OFFSET),
        (CORNER_OFFSET, height - 1 - CORNER_OFFSET),
        (width - 1 - CORNER_OFFSET, height - 1 - CORNER_OFFSET),
    )
    return all(alpha.getpixel(point) < CORNER_ALPHA_MAX for point in points)


def normalize_card_canvas(image: Image.Image) -> Image.Image:
    """將裸卡圖統一成 CANVAS_W x CANVAS_H 透明畫布（梵高比卡超標準）。

    有 alpha：crop 到 alpha bbox（裁走 SNK 大畫布嘅透明邊），再按比例放大
    置中落固定畫布。冇 alpha（舊 RGB 圖）：直接等比縮放置中，唔做去背。
    """
    rgba = image.convert("RGBA")
    if "A" in image.getbands():
        bbox = rgba.getchannel("A").getbbox()
        if bbox:
            rgba = rgba.crop(bbox)
    inner_w = round(CANVAS_W * CANVAS_FILL)
    inner_h = round(CANVAS_H * CANVAS_FILL)
    scale = min(inner_w / rgba.width, inner_h / rgba.height)
    new_size = (max(1, round(rgba.width * scale)), max(1, round(rgba.height * scale)))
    # 統一用 copy + resize（thumbnail 只能縮細，呢度連細圖放大都包）
    card = rgba.copy()
    if new_size != card.size:
        card = card.resize(new_size, Image.Resampling.LANCZOS)
    canvas = Image.new("RGBA", (CANVAS_W, CANVAS_H), (0, 0, 0, 0))
    canvas.paste(card, ((CANVAS_W - card.width) // 2, (CANVAS_H - card.height) // 2), card)
    return canvas


def is_normalized(image: Image.Image) -> bool:
    """已經係統一畫布規格 → delta skip。"""
    return image.size == (CANVAS_W, CANVAS_H)


# 圓角規格（梵高比卡超實測 2026-07-25）：423x589 卡身角弧 ~25px，
# 即 ~6% 卡寬。SNK 原生圓角同真卡都係呢個比例。
CORNER_RADIUS_RATIO = 0.059


def has_rounded_corners(image: Image.Image) -> bool:
    """卡身（alpha bbox）四角落係咪已經圓角。方角卡（舊 RGB 圖 normalize 完）會 False。"""
    rgba = image.convert("RGBA")
    alpha = rgba.getchannel("A")
    bbox = alpha.getbbox()
    if not bbox:
        return False
    left, top, right, bottom = bbox
    # 卡身角位（唔係畫布角位）：原生圓角卡喺 (left+2, top+2) 係透明
    points = (
        (left + 2, top + 2),
        (right - 3, top + 2),
        (left + 2, bottom - 3),
        (right - 3, bottom - 3),
    )
    return all(alpha.getpixel(point) < CORNER_ALPHA_MAX for point in points)


def apply_rounded_corners(image: Image.Image) -> Image.Image:
    """幫方角卡補返原生比例圓角（~6% 卡寬，同梵高比卡超一致）。

    只改 alpha channel：角弧以外嘅 pixel alpha→0，卡面內容一個 pixel 都唔郁。
    用 4x supersample 落 mask，邊緣 AA 先唔會鋸齒。
    """
    from PIL import ImageChops, ImageDraw

    rgba = image.convert("RGBA")
    alpha = rgba.getchannel("A")
    bbox = alpha.getbbox()
    if not bbox:
        return rgba
    left, top, right, bottom = bbox
    width, height = right - left, bottom - top
    radius = max(2, round(width * CORNER_RADIUS_RATIO))
    ss = 4
    mask = Image.new("L", (width * ss, height * ss), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, width * ss - 1, height * ss - 1), radius=radius * ss, fill=255
    )
    mask = mask.resize((width, height), Image.Resampling.LANCZOS)
    new_alpha = Image.new("L", rgba.size, 0)
    new_alpha.paste(ImageChops.multiply(alpha.crop(bbox), mask), (left, top))
    rgba.putalpha(new_alpha)
    return rgba


def _write_image_block(image: Image.Image, alt: str) -> dict[str, Any]:
    """將 PIL image 編碼成 master + derivatives，砌 image block。"""
    master = g10._save_webp(image, 92)
    sha = hashlib.sha256(master).hexdigest()
    (ASSETS / f"{sha}.webp").write_bytes(master)
    variants: dict[str, str] = {}
    for suffix, blob in g10.encode_derivatives(image).items():
        (ASSETS / f"{sha}_{suffix}.webp").write_bytes(blob)
        variants[suffix] = f"/market-assets/{sha}_{suffix}.webp"
    return {
        "src": f"/market-assets/{sha}.webp",
        "sha256": sha,
        "width": image.width,
        "height": image.height,
        "kind": "raw_front",
        "alt": {"en": alt, "ja": None, "zhCN": None, "zhTW": None},
        "qcAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "variants": variants,
    }


def store_native_image(raw: bytes, alt: str) -> dict[str, Any]:
    """保留 RGBA 入庫，統一畫布規格。絕對唔准 .convert("RGB") —— 壓平 alpha 係舊 bug 根源。"""

    with Image.open(io.BytesIO(raw)) as opened:
        image = opened.copy()
    return _write_image_block(normalize_card_canvas(image), alt)


def store_normalized_image(raw: bytes, alt: str) -> dict[str, Any] | None:
    """已有本地圖（任何來源）→ 統一畫布後入庫。已係標準規格就回 None（delta skip）。"""

    with Image.open(io.BytesIO(raw)) as opened:
        if is_normalized(opened):
            return None
        image = opened.copy()
    return _write_image_block(normalize_card_canvas(image), alt)


def store_rounded_image(raw: bytes, alt: str) -> dict[str, Any] | None:
    """方角卡（已 std 畫布但角位係直角）→ 補圓角再入庫。已圓角就回 None。"""

    with Image.open(io.BytesIO(raw)) as opened:
        image = opened.copy()
    if has_rounded_corners(image):
        return None
    return _write_image_block(apply_rounded_corners(image), alt)


def _download(url: str) -> bytes:
    from curl_cffi import requests as curl_requests

    response = curl_requests.get(url, impersonate="chrome", timeout=30)
    response.raise_for_status()
    return response.content


def load_snk_harvest_urls(harvest_path: Path = SNK_HARVEST) -> dict[int, str]:
    """SNK brute harvest cache：item_id -> image_url。"""

    urls: dict[int, str] = {}
    if not harvest_path.is_file():
        return urls
    with harvest_path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            item_id = row.get("item_id") or row.get("itemId") or row.get("id")
            url = row.get("image_url") or row.get("imageUrl")
            if item_id and url:
                urls[int(item_id)] = str(url)
    return urls


def snk_master_url(item_id: int) -> str | None:
    """SNK get_master 直取（cache 冇嘅時候）。"""

    from snkrdunk_bulk import SnkrdunkApi

    try:
        master = SnkrdunkApi().get_master(item_id)
    except Exception:
        return None
    url = (master.get("primaryMedia") or {}).get("imageUrl")
    return str(url) if url else None


def kado_native_path(name: str, number: str, set_name: str, language: str) -> Path | None:
    """Kado dump 搵本地 RGBA WebP（只有約 16% set 有原生圓角）。"""

    kado_root = KADO_DUMP if KADO_DUMP.is_dir() else ROOT / "data" / "private" / "kado"
    resolver = g10.KadoRawResolver(kado_root)
    match = resolver.resolve(name, number, set_name, language)
    if match is None:
        return None
    return match[0].path


def resolve_native_image(
    card: Mapping[str, Any],
    *,
    snk_item_id: int | None = None,
    manual_snk_id: int | None = None,
    snk_harvest: Mapping[int, str] | None = None,
    delay: float = DOWNLOAD_DELAY_S,
) -> NativeImageResult | None:
    """行 priority chain 搵原生圓角圖，逐個候選過 corner-alpha gate。

    card 需要有 names.en / collectorNumber.display / language / tcg 呢啲欄
    （canonical snapshot card schema）。搵唔到合格圖就回 None，由 caller
    決定保留現圖，唔准硬切放空。
    """

    names = card.get("names") or {}
    alt = str(names.get("en") or card.get("id") or "card")
    collector = str((card.get("collectorNumber") or {}).get("display") or "")
    language = str(card.get("language") or "")
    set_name = ""
    sets = card.get("sets")
    if isinstance(sets, Mapping):
        set_name = str(sets.get("en") or sets.get("name") or "")
    number = collector.split("/", 1)[0]

    candidates: list[NativeCandidate] = []
    harvest = snk_harvest if snk_harvest is not None else load_snk_harvest_urls()

    item_id = snk_item_id or manual_snk_id
    if item_id and item_id in harvest:
        candidates.append(NativeCandidate("snk_cache", harvest[item_id], None))
    if item_id:
        url = snk_master_url(item_id)
        if url:
            candidates.append(NativeCandidate("snk_master", url, None))

    kado_path = kado_native_path(alt, number, set_name, language)
    if kado_path is not None:
        candidates.append(NativeCandidate("kado", None, kado_path))

    for candidate in candidates:
        try:
            raw = candidate.path.read_bytes() if candidate.path else _download(str(candidate.url))
        except Exception as error:  # noqa: BLE001
            print(f"[native-fetch-fail] {alt} {collector} {candidate.source}: {error}")
            continue
        if not is_native_rounded(raw):
            print(f"[native-reject] {alt} {collector} {candidate.source}: corners not transparent")
            continue
        block = store_native_image(raw, alt)
        print(f"[native-ok] {alt} {collector} <- {candidate.source} sha={block['sha256'][:12]}")
        time.sleep(delay)
        return NativeImageResult(block, candidate.source)
    return None
