"""補生成 market-assets 嘅 responsive derivative（_200 / _600）。

點解要有呢個檔：`card-image.tsx` 嘅 <img> 用 `w` descriptor + `sizes` 砌 srcSet，
即係 `src` 唔喺候選集入面 —— derivative 唔存在，瀏覽器唔會 fallback 落 base 圖，
直接爛。`sync-snapshot.mjs` 以前見唔到 derivative 就靜靜 skip，所以成站爛咗都
綠燈。呢個腳本負責令每張 master 都有齊 derivative。

master 一個 byte 都唔會改 —— snapshot 入面 sha256 係內容定址，改 master 即刻
撞爛 sync-snapshot.mjs 嘅 hash 驗證。只寫 <sha>_200.webp / <sha>_600.webp。

規格沿用 native_image_resolver / g10_public_snapshot 嘅 canonical 路徑，同已經
出街嗰 266 張 derivative 完全一致：
  normalize_card_canvas  → 429x600 透明畫布，等比縮放置中（唔變形、唔裁切）
  apply_rounded_corners  → 方角舊圖補原生 RGBA 圓角（6% 卡寬，只改 alpha）
  encode_derivatives     → _200 = 200x280、_600 = 429x600

預設 dry-run 出報告；--write 先真係落檔。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

from PIL import Image  # noqa: E402

import g10_public_snapshot as g10  # noqa: E402
import native_image_resolver as nir  # noqa: E402

BASE_PATTERN = re.compile(r"^[a-f0-9]{64}$")
SUFFIXES = tuple(spec[0] for spec in g10.DERIVATIVE_SPECS)


def snapshot_shas(path: Path) -> set[str]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    cards = [*(payload.get("top100") or []), *(payload.get("watchlist") or [])]
    return {str(c["image"]["sha256"]) for c in cards if c.get("image", {}).get("sha256")}


def missing_derivatives(assets: Path, wanted: set[str] | None) -> list[str]:
    pending = []
    for path in sorted(assets.glob("*.webp")):
        sha = path.stem
        if not BASE_PATTERN.match(sha):
            continue
        if wanted is not None and sha not in wanted:
            continue
        if all((assets / f"{sha}_{suffix}.webp").is_file() for suffix in SUFFIXES):
            continue
        pending.append(sha)
    return pending


def build(assets: Path, sha: str, write: bool) -> dict[str, object]:
    master = assets / f"{sha}.webp"
    with Image.open(master) as opened:
        source_size, source_mode = opened.size, opened.mode
        image = nir.normalize_card_canvas(opened.copy())
    rounded = nir.has_rounded_corners(image)
    if not rounded:
        image = nir.apply_rounded_corners(image)
    written = {}
    for suffix, blob in g10.encode_derivatives(image).items():
        target = assets / f"{sha}_{suffix}.webp"
        if write:
            target.write_bytes(blob)
        written[suffix] = len(blob)
    return {
        "sha": sha,
        "source": f"{source_size[0]}x{source_size[1]}",
        "mode": source_mode,
        "cornersAdded": not rounded,
        "bytes": written,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--assets", type=Path, default=ROOT / "data" / "public" / "market-assets")
    parser.add_argument("--snapshot", type=Path, action="append", default=[],
                        help="只處理呢份 snapshot 引用嘅 sha（可重複）。唔指定就補齊全部。")
    parser.add_argument("--write", action="store_true", help="正式落檔（預設 dry-run）")
    args = parser.parse_args()

    assets = args.assets.resolve()
    if not assets.is_dir():
        raise SystemExit(f"assets directory not found: {assets}")

    wanted: set[str] | None = None
    if args.snapshot:
        wanted = set()
        for path in args.snapshot:
            wanted |= snapshot_shas(path.resolve())

    pending = missing_derivatives(assets, wanted)
    print(f"assets={assets}")
    print(f"scope={'snapshot(' + str(len(wanted)) + ' sha)' if wanted is not None else 'all base assets'}")
    print(f"missing derivatives: {len(pending)}")
    if not pending:
        return 0

    added_corners = 0
    for index, sha in enumerate(pending, start=1):
        result = build(assets, sha, args.write)
        added_corners += 1 if result["cornersAdded"] else 0
        if index % 50 == 0 or index == len(pending):
            print(f"  [{index}/{len(pending)}] {sha[:12]} {result['source']} {result['mode']}")

    verb = "wrote" if args.write else "would write"
    print(f"{verb} {len(pending) * len(SUFFIXES)} derivative files for {len(pending)} masters")
    print(f"rounded corners applied to {added_corners} square-corner masters")
    if not args.write:
        print("dry-run: nothing written. re-run with --write")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
