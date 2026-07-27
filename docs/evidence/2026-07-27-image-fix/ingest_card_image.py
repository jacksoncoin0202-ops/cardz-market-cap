"""單卡卡面入庫／換圖工具（人手確認過嘅圖走呢條路）。

點解要有呢個檔：`ensure_std_card_images.py` 係 snapshot 驅動嘅全量自癒，冇單卡模式；
`g10_asset_ingest.py` 硬綁 G10 檔案樹同 ebay/snkrdunk 身份，入唔到外部 CDN 圖。
換一張圖要同時動四個地方，順序錯就會出現「檔案喺但 snapshot 唔認」或者反過來：

  1. `data/public/market-assets/`      —— producer 讀嘅正本（nir.ASSETS）
  2. `apps/web/public/market-assets/`  —— Next.js 靜態目錄
  3. `manifests/image-qc.json`         —— canonical_public_snapshot 唯一認嘅 image 來源
  4. `data/runtime/local-serve/snapshot.json` —— 而家出街嗰份，要順手改 pointer

幾何規格一個 byte 都唔自己發明，全部叫 `native_image_resolver`：
  normalize_card_canvas → 429x600 透明畫布，等比置中（唔變形、唔裁切）
  apply_rounded_corners → 方角圖補原生 RGBA 圓角（6% 卡寬，只改 alpha）
  _write_image_block    → 寫 <sha>.webp + <sha>_200.webp + <sha>_600.webp

**舊圖一律唔刪**：sha 係內容定址，歷史 snapshot 仲指住舊 sha，刪咗即刻爛。

預設 dry-run，`--write` 先真係落檔。
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "pipelines"))

from PIL import Image  # noqa: E402

import native_image_resolver as nir  # noqa: E402
from canonical_public_snapshot import atomic_json, snapshot_content_sha256  # noqa: E402

WEB_ASSETS = ROOT / "apps" / "web" / "public" / "market-assets"
QC_PATH = ROOT / "manifests" / "image-qc.json"
LIVE_SNAPSHOT = ROOT / "data" / "runtime" / "local-serve" / "snapshot.json"
DERIVATIVE_SUFFIXES = ("200", "600")


def load_source(source: str) -> bytes:
    if source.startswith(("http://", "https://")):
        return nir._download(source)
    return Path(source).read_bytes()


def render(raw: bytes) -> tuple[Image.Image, dict[str, object]]:
    """raw bytes → 標準畫布 + 圓角。回埋量度到嘅前置事實，寫入證據用。

    白底橫向圖（SNK 嘅 1000x730）唔需要特別處理：嗰類圖帶 alpha，
    `normalize_card_canvas` 自己會裁到 alpha bbox。實測 snkrdunk_459741.webp
    加唔加自訂白底裁切，輸出 byte-identical。
    """

    with Image.open(io.BytesIO(raw)) as opened:
        source_size, source_mode = opened.size, opened.mode
        image = nir.normalize_card_canvas(opened.copy())
    already_rounded = nir.has_rounded_corners(image)
    if not already_rounded:
        image = nir.apply_rounded_corners(image)
    facts = {
        "sourceSize": f"{source_size[0]}x{source_size[1]}",
        "sourceMode": source_mode,
        "sourceRatio": round(source_size[0] / source_size[1], 4),
        "cornersAdded": not already_rounded,
    }
    return image, facts


def corner_alpha(image: Image.Image) -> list[int]:
    alpha = image.convert("RGBA").getchannel("A")
    off = nir.CORNER_OFFSET
    w, h = image.size
    return [
        alpha.getpixel((off, off)),
        alpha.getpixel((w - 1 - off, off)),
        alpha.getpixel((off, h - 1 - off)),
        alpha.getpixel((w - 1 - off, h - 1 - off)),
    ]


def mirror_to_web(sha: str) -> list[str]:
    copied = []
    for name in (f"{sha}.webp", *(f"{sha}_{s}.webp" for s in DERIVATIVE_SUFFIXES)):
        src = nir.ASSETS / name
        if not src.is_file():
            continue
        shutil.copy2(src, WEB_ASSETS / name)
        copied.append(name)
    return copied


def upsert_qc(public_id: str, block: dict, source_sha: str, source_ref: str,
              method: str, semantic: str, now: str) -> dict[str, object]:
    document = json.loads(QC_PATH.read_text(encoding="utf-8"))
    records = document["records"]
    record = {
        "cardNumberMatch": True,
        "contentSha256": block["sha256"],
        "height": block["height"],
        "imageKind": "raw_front",
        "languageMatch": True,
        "nativeRgba": True,
        "publicAllowed": True,
        "publicId": public_id,
        "qcAt": now,
        "qcVersion": "raw-front-v4",
        "resolverEvidence": {
            "collectorMatch": True,
            "languageMetadataMatch": True,
            "method": method,
            "sourceContentSha256": source_sha,
            "sourceRef": source_ref,
            "tcgMetadataMatch": True,
        },
        "semanticMatchStatus": semantic,
        "stdCanvas": nir.NORMALIZED_MARKER,
        "tcgMatch": True,
        "width": block["width"],
    }
    previous = None
    for index, existing in enumerate(records):
        if existing.get("publicId") == public_id:
            previous = existing.get("contentSha256")
            records[index] = record
            break
    else:
        records.append(record)
    # 寫法必須同 ensure_std_card_images.py 一致，唔係成份檔會出一個假 diff
    QC_PATH.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"replaced": previous, "action": "replaced" if previous else "appended"}


def patch_live_snapshot(public_id: str, block: dict, now: str) -> dict[str, object]:
    snapshot = json.loads(LIVE_SNAPSHOT.read_text(encoding="utf-8"))
    touched = []
    for key, value in snapshot.items():
        if not isinstance(value, list):
            continue
        for card in value:
            if not isinstance(card, dict) or card.get("id") != public_id:
                continue
            image = card.get("image") or {}
            alt = image.get("alt")
            card["image"] = {
                "alt": alt,
                "height": block["height"],
                "kind": "raw_front",
                # 用 QC 記錄同一個時間戳：producer 重生 snapshot 時 qcAt 係讀 QC
                # 記錄嗰個，兩邊各自 now() 就會出現一個假漂移
                "qcAt": now,
                "sha256": block["sha256"],
                "src": block["src"],
                "variants": block["variants"],
                "width": block["width"],
            }
            touched.append(key)
    if not touched:
        return {"boards": [], "contentSha256": snapshot["generation"]["contentSha256"]}
    snapshot["generation"]["contentSha256"] = snapshot_content_sha256(snapshot)
    # atomic_json 係 producer 自己嘅寫檔器（compact + sort_keys + LF），
    # 同 snapshot_content_sha256 用同一份數值正規化，hash 同磁碟文字唔會分家
    atomic_json(LIVE_SNAPSHOT, snapshot)
    return {"boards": touched, "contentSha256": snapshot["generation"]["contentSha256"]}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--public-id", required=True, help="catalog_variant.opaque_id（QC publicId）")
    parser.add_argument("--source", required=True, help="圖片 URL 或本機路徑")
    parser.add_argument("--alt-en", required=True)
    parser.add_argument("--method", default="human_confirmed_swap",
                        help="resolverEvidence.method")
    parser.add_argument("--semantic", default="human_or_vision_confirmed",
                        choices=("human_or_vision_confirmed", "metadata_exact_unreviewed"))
    parser.add_argument("--write", action="store_true", help="正式落檔（預設 dry-run）")
    args = parser.parse_args()

    raw = load_source(args.source)
    source_sha = hashlib.sha256(raw).hexdigest()
    image, facts = render(raw)

    report: dict[str, object] = {
        "publicId": args.public_id,
        "source": args.source,
        "sourceBytes": len(raw),
        "sourceContentSha256": source_sha,
        **facts,
        "outputSize": f"{image.width}x{image.height}",
        "outputMode": image.mode,
        "cornerAlpha": corner_alpha(image),
        "write": args.write,
    }
    report["cornerAlphaPass"] = all(v < nir.CORNER_ALPHA_MAX for v in report["cornerAlpha"])
    report["stdCanvasPass"] = nir.is_normalized(image)

    if not args.write:
        report["note"] = "dry-run：冇寫任何檔。加 --write 正式落檔。"
        print(json.dumps(report, ensure_ascii=False, indent=1))
        return 0

    if not report["cornerAlphaPass"] or not report["stdCanvasPass"]:
        report["error"] = "幾何 gate 唔過，拒絕落檔"
        print(json.dumps(report, ensure_ascii=False, indent=1))
        return 1

    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    block = nir._write_image_block(image, args.alt_en)
    report["newSha256"] = block["sha256"]
    report["qcAt"] = now
    report["mirroredToWeb"] = mirror_to_web(block["sha256"])
    report["qc"] = upsert_qc(args.public_id, block, source_sha, args.source,
                             args.method, args.semantic, now)
    report["liveSnapshot"] = patch_live_snapshot(args.public_id, block, now)
    print(json.dumps(report, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
