"""統一卡圖畫布 backfill（一次性遷移 CLI）。

將 snapshot 全部卡圖統一成 429x600 透明畫布（梵高比卡超標準）：
- 已係標準規格 → skip
- RGBA 圖（緊身或 SNK 大畫布）→ alpha-bbox crop 再置中
- RGB 舊圖 → 等比縮放置中（唔去背）

預設 --dry-run 出覆蓋報告；--execute 正式寫入 market-assets + snapshot +
image-qc.json（stdCanvas: "std-429x600" 標記俾每日 delta 用）。
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

import native_image_resolver as nir  # noqa: E402
from PIL import Image  # noqa: E402

LATEST_POINTER = ROOT / "data" / "public" / "publish-staging" / "latest.json"
QC = ROOT / "manifests" / "image-qc.json"
REPORT = ROOT / "manifests" / "canvas-normalize-report.json"


def load_latest_snapshot() -> tuple[Path, dict[str, Any]]:
    pointer = json.loads(LATEST_POINTER.read_text(encoding="utf-8"))
    snapshot_path = LATEST_POINTER.parent / pointer["snapshotKey"]
    return snapshot_path, json.loads(snapshot_path.read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true", help="正式寫入（預設 dry-run）")
    args = parser.parse_args()

    snapshot_path, snapshot = load_latest_snapshot()
    cards = snapshot["top100"] + snapshot["watchlist"]
    print(f"snapshot: {snapshot_path.name} | cards: {len(cards)}")

    report: dict[str, Any] = {
        "generatedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "mode": "execute" if args.execute else "dry-run",
        "cards": len(cards),
        "alreadyStd": 0,
        "normalized": [],
        "missingAsset": [],
    }
    additions: list[tuple[str, dict, str]] = []

    for index, card in enumerate(cards):
        image = card.get("image") or {}
        sha = str(image.get("sha256") or "")
        path = nir.ASSETS / f"{sha}.webp"
        name = str((card.get("names") or {}).get("en") or card.get("id"))
        if not sha or not path.is_file():
            report["missingAsset"].append({"id": card.get("id"), "name": name})
            continue
        block = nir.store_normalized_image(path.read_bytes(), name)
        if block is None:
            report["alreadyStd"] += 1
            continue
        with Image.open(path) as probe:
            was_rgba = "A" in probe.getbands()
        report["normalized"].append({
            "id": card.get("id"), "name": name,
            "from": f"{image.get('width')}x{image.get('height')}{' RGBA' if was_rgba else ' RGB'}",
            "sha256": block["sha256"],
        })
        if index and index % 50 == 0:
            print(f"...progress {index}/{len(cards)}")
        if args.execute:
            card["image"] = block
            additions.append((card["id"], block, "std_canvas_429x600"))

    print(f"\n=== {'EXECUTE' if args.execute else 'DRY-RUN'} 結果 ===")
    print(f"already std (skip): {report['alreadyStd']}")
    print(f"normalized: {len(report['normalized'])}")
    print(f"missing asset: {len(report['missingAsset'])}")
    for row in report["missingAsset"][:10]:
        print(f"  - {row['name']}")

    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"report: {REPORT}")

    if args.execute and additions:
        manifest = json.loads(QC.read_text(encoding="utf-8"))
        records = manifest.setdefault("records", [])
        have = {r["contentSha256"] for r in records}
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        added = 0
        for public_id, block, method in additions:
            if block["sha256"] in have:
                continue
            records.append({
                "cardNumberMatch": True,
                "contentSha256": block["sha256"],
                "height": block["height"],
                "imageKind": "raw_front",
                "languageMatch": True,
                "nativeRgba": True,
                "stdCanvas": nir.NORMALIZED_MARKER,
                "publicAllowed": True,
                "publicId": public_id,
                "qcAt": now,
                "qcVersion": "raw-front-v4",
                "resolverEvidence": {
                    "collectorMatch": True,
                    "languageMetadataMatch": True,
                    "method": method,
                    "tcgMetadataMatch": True,
                },
                "semanticMatchStatus": "metadata_exact_unreviewed",
                "tcgMatch": True,
                "width": block["width"],
            })
            have.add(block["sha256"])
            added += 1
        records.sort(key=lambda r: r["publicId"])
        QC.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        snapshot_path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(f"wrote snapshot + {added} qc records")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
