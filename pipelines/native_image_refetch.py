"""Backfill Top 350 卡圖做原生圓角 RGBA 版本（一次性遷移 CLI）。

預設 --dry-run：逐張卡行 native_image_resolver priority chain，出覆蓋報告
（邊張會換、來源係邊、邊張搵唔到原生圓角）俾用戶過目，唔寫任何嘢。

正式跑（--execute）：覆寫 market-assets、更新 snapshot image block、
append image-qc.json records（nativeRgba: true）。

Delta 模式：已經係原生 RGBA 圓角嘅現有圖會 skip（唔重複落）。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

import native_image_resolver as nir  # noqa: E402

LATEST_POINTER = ROOT / "data" / "public" / "publish-staging" / "latest.json"
UNIVERSE = ROOT / "data" / "runtime" / "private-source-map" / "active-universe.json"
QC = ROOT / "manifests" / "image-qc.json"
REPORT = ROOT / "manifests" / "native-image-backfill-report.json"

# 沿用 backfill_pack_images.py 嘅人工 SNK mapping（get_master 驗證過嘅）
MANUAL_SNK_IDS = {
    "GG44": 486959, "GG69": 486984, "SV49": 492506, "SV107": 490342,
    "ST01-012": 135440, "OP13-118": 676009, "OP11-118": 531958,
    "OP09-119": 744321, "OP09-118": 349471, "EB01-006": 176565,
    "P-001": 157931, "OP09-050": 371712, "OP05-119": 349476,
    "OP06-118": 159664, "EB02-061": 503450, "OP01-016": 135442,
    "OP01-003": 142586, "OP07-051": 349478, "OP01-078": 126178,
    "OP02-013": 102434, "OP06-119": 520533, "ST21-014": 478777,
    "OP07-109": 348126, "GG70": 105530, "TG20": 93017, "SM191": 494757,
}


def load_latest_snapshot() -> tuple[Path, dict[str, Any]]:
    pointer = json.loads(LATEST_POINTER.read_text(encoding="utf-8"))
    snapshot_path = LATEST_POINTER.parent / pointer["snapshotKey"]
    return snapshot_path, json.loads(snapshot_path.read_text(encoding="utf-8"))


def current_image_is_native(card: dict[str, Any]) -> bool:
    """現有圖已經係原生 RGBA 圓角 → skip（delta 模式核心）。"""
    image = card.get("image") or {}
    sha = str(image.get("sha256") or "")
    if not sha:
        return False
    path = nir.ASSETS / f"{sha}.webp"
    if not path.is_file():
        return False
    return nir.is_native_rounded(path.read_bytes())


def append_qc_records(manifest: dict[str, Any], additions: list[tuple[str, dict, str]]) -> int:
    records = manifest.setdefault("records", [])
    have = {r["contentSha256"] for r in records}
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    added = 0
    for public_id, image, method in additions:
        if image["sha256"] in have:
            continue
        records.append({
            "cardNumberMatch": True,
            "contentSha256": image["sha256"],
            "height": image["height"],
            "imageKind": "raw_front",
            "languageMatch": True,
            "nativeRgba": True,
            "publicAllowed": True,
            "publicId": public_id,
            "qcAt": now,
            "qcVersion": "raw-front-v3",
            "resolverEvidence": {
                "collectorMatch": True,
                "languageMetadataMatch": True,
                "method": method,
                "tcgMetadataMatch": True,
            },
            "semanticMatchStatus": "metadata_exact_unreviewed",
            "tcgMatch": True,
            "width": image["width"],
        })
        have.add(image["sha256"])
        added += 1
    records.sort(key=lambda r: r["publicId"])
    return added


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true", help="正式寫入（預設 dry-run）")
    parser.add_argument("--limit", type=int, default=0, help="只處理頭 N 張（試水用）")
    args = parser.parse_args()

    snapshot_path, snapshot = load_latest_snapshot()
    cards = snapshot["top100"] + snapshot["watchlist"]
    if args.limit:
        cards = cards[: args.limit]
    print(f"snapshot: {snapshot_path.name} | cards: {len(cards)}")

    universe = json.loads(UNIVERSE.read_text(encoding="utf-8"))
    uni_cards = universe["cards"] if isinstance(universe, dict) else universe
    snk_by_id = {c["pokedexId"]: c.get("snkItemId") for c in uni_cards}

    harvest = nir.load_snk_harvest_urls()
    print(f"snk harvest cache: {len(harvest)} urls")

    report: dict[str, Any] = {
        "generatedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "mode": "execute" if args.execute else "dry-run",
        "cards": len(cards),
        "alreadyNative": 0,
        "resolved": [],
        "unresolved": [],
    }
    additions: list[tuple[str, dict, str]] = []

    for index, card in enumerate(cards):
        if index and index % 10 == 0:
            print(f"...progress {index}/{len(cards)}")
        name = str((card.get("names") or {}).get("en") or card.get("id"))
        collector = str((card.get("collectorNumber") or {}).get("display") or "")

        if current_image_is_native(card):
            report["alreadyNative"] += 1
            continue

        snk_item_id = snk_by_id.get(card.get("id"))
        snk_item_id = int(snk_item_id) if snk_item_id else None
        manual_id = MANUAL_SNK_IDS.get(collector.split("/", 1)[0])

        result = nir.resolve_native_image(
            card,
            snk_item_id=snk_item_id,
            manual_snk_id=manual_id,
            snk_harvest=harvest,
        )
        if result is None:
            report["unresolved"].append({"id": card.get("id"), "name": name, "collector": collector,
                                          "language": card.get("language"), "tcg": card.get("tcg")})
            continue
        report["resolved"].append({"id": card.get("id"), "name": name, "collector": collector,
                                    "source": result.source, "sha256": result.image_block["sha256"]})
        if args.execute:
            card["image"] = result.image_block
            additions.append((card["id"], result.image_block, f"native_rgba_{result.source}"))

    print(f"\n=== {'EXECUTE' if args.execute else 'DRY-RUN'} 結果 ===")
    print(f"already native (skip): {report['alreadyNative']}")
    print(f"resolved: {len(report['resolved'])}")
    by_source: dict[str, int] = {}
    for row in report["resolved"]:
        by_source[row["source"]] = by_source.get(row["source"], 0) + 1
    print(f"  by source: {by_source}")
    print(f"unresolved (保留現圖): {len(report['unresolved'])}")
    for row in report["unresolved"][:20]:
        print(f"  - {row['name']} [{row['collector']}] {row['language']} {row['tcg']}")

    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"report: {REPORT}")

    if args.execute and additions:
        manifest = json.loads(QC.read_text(encoding="utf-8"))
        added = append_qc_records(manifest, additions)
        QC.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        snapshot_path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(f"wrote snapshot + {added} qc records")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
