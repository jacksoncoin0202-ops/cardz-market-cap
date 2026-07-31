"""每日卡圖自愈（self-heal）：snapshot 入面每張卡都要係梵高標準。

規格（2026-07-25 用戶規矩）：
- 429x600 透明畫布（std-429x600）
- RGBA 原生圓角（角弧 ~6% 卡寬）
- 方角 / 非標準尺寸 → 就地用現有 asset 修復，唔重下載

用法：
    python -X utf8 pipelines/ensure_std_card_images.py <snapshot.json> [--write]

預設 dry-run 出報告；--write 先正式覆寫 snapshot + 指定嘅 QC candidate。
每日 pipeline（canonical_public_snapshot 出完貨）叫一次 --write，
新入列嘅卡即日自動統一，唔駛等人手追。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

import native_image_resolver as nir  # noqa: E402
from canonical_public_snapshot import snapshot_content_sha256  # noqa: E402
from PIL import Image  # noqa: E402

QC = ROOT / "manifests" / "image-qc.json"
LATEST_POINTERS = (
    ROOT / "data" / "runtime" / "publish-staging" / "latest.json",
    ROOT / "data" / "public" / "publish-staging" / "latest.json",
)


def assert_not_pointed_generation(
    snapshot_path: Path,
    pointer_path: Path = LATEST_POINTERS[0],
) -> None:
    """Refuse to mutate the snapshot currently selected by the local pointer."""

    pointer_path = pointer_path.resolve()
    if not pointer_path.is_file():
        return
    try:
        pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"cannot validate active publish pointer: {pointer_path}") from exc
    snapshot_key = pointer.get("snapshotKey") if isinstance(pointer, dict) else None
    if not isinstance(snapshot_key, str) or not snapshot_key.strip():
        raise RuntimeError(f"active publish pointer has no safe snapshotKey: {pointer_path}")
    publish_root = pointer_path.parent
    pointed_path = (publish_root / snapshot_key).resolve()
    try:
        pointed_path.relative_to(publish_root)
    except ValueError as exc:
        raise RuntimeError(f"active publish pointer snapshotKey escapes publish root: {snapshot_key}") from exc
    if snapshot_path.resolve() == pointed_path:
        raise RuntimeError(
            f"refusing to mutate active pointed generation snapshot: {snapshot_path.resolve()}"
        )


def ensure_cards(cards: list[dict], *, write: bool) -> tuple[dict[str, int], dict[str, str]]:
    """統計（同 --write 時順便修正）每張卡圖偏離梵高標準幾多。

    除咗 stats，仲會回埋 `{card id: 未正規化前嗰份原始 bytes 嘅 sha256}`——
    QC record 嘅 `resolverEvidence.sourceContentSha256` 要嗰個值，
    唔可以事後推返（`contentSha256` 係轉換之後嗰張圖，兩者唔同）。

    dry-run 唔准落盤到 market-assets：`nir.store_*` 一入去就會寫 master + 兩個
    衍生尺寸，而嗰啲檔冇任何 snapshot 引用，即刻變成 verify_images 報嘅孤兒、
    亦即 quarantine 下一轉要搬走嘅嘢——「淨係想睇下狀況」會令條 publish 鏈見紅。

    但都唔可以喺 dry-run 另寫一套 in-memory 判斷：`store_rounded_image` 收嘅係
    master 經 quality 92 WebP 編碼再解返出嚟嗰張圖，唔係 normalize 完嗰個 PIL
    object，兩者角位 alpha 唔同，圓角判斷會漂（實測 dry-run 漏報 rounded）。
    所以 dry-run 行足同一條 store 路徑，只係將落盤導去用完即棄嘅沙盒，
    統計保證同 --write 一模一樣。
    """

    if write:
        return _classify(cards, source_root=nir.ASSETS)
    with tempfile.TemporaryDirectory(prefix="ensure-std-dryrun-") as sandbox:
        source_root, nir.ASSETS = nir.ASSETS, Path(sandbox)
        try:
            return _classify(cards, source_root=source_root)
        finally:
            nir.ASSETS = source_root


def _classify(cards: list[dict], *, source_root: Path) -> tuple[dict[str, int], dict[str, str]]:
    """原圖永遠由 `source_root` 讀；新圖寫去 `nir.ASSETS`（dry-run 時係沙盒）。"""

    stats = {"ok": 0, "resized": 0, "rounded": 0, "missingAsset": 0}
    origins: dict[str, str] = {}
    for card in cards:
        image = card.get("image") or {}
        sha = str(image.get("sha256") or "")
        path = source_root / f"{sha}.webp"
        name = str((card.get("names") or {}).get("en") or card.get("id"))
        if not sha or not path.is_file():
            stats["missingAsset"] += 1
            continue
        raw = path.read_bytes()
        origins[str(card.get("id"))] = hashlib.sha256(raw).hexdigest()
        with Image.open(path) as probe:
            std = nir.is_normalized(probe)
        if not std:
            block = nir.store_normalized_image(raw, name)
            if block:
                card["image"] = block
                stats["resized"] += 1
                raw = (nir.ASSETS / f"{block['sha256']}.webp").read_bytes()
        block = nir.store_rounded_image(raw, name)
        if block:
            card["image"] = block
            stats["rounded"] += 1
        if not std or block:
            continue
        stats["ok"] += 1
    return stats, origins


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot", type=Path)
    parser.add_argument("--write", action="store_true", help="正式覆寫 snapshot + QC（預設 dry-run）")
    parser.add_argument(
        "--manifest",
        type=Path,
        default=QC,
        help="candidate image-QC manifest; daily publication must not overwrite the canonical input",
    )
    parser.add_argument(
        "--pointer",
        action="append",
        type=Path,
        default=[],
        help="additional local latest pointer whose selected generation is immutable",
    )
    args = parser.parse_args()
    snapshot_path = args.snapshot.resolve()
    if args.write:
        for pointer_path in dict.fromkeys([*LATEST_POINTERS, *args.pointer]):
            assert_not_pointed_generation(snapshot_path, pointer_path)
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    cards = snapshot["top100"] + snapshot["watchlist"]
    stats, origins = ensure_cards(cards, write=args.write)
    print(f"{snapshot_path.name}: {stats['ok']} ok | {stats['resized']} resized | "
          f"{stats['rounded']} rounded | {stats['missingAsset']} missing asset")

    if not args.write or not (stats["resized"] or stats["rounded"]):
        return 0

    manifest_path = args.manifest.resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    records = manifest.setdefault("records", [])
    have = {r["contentSha256"] for r in records}
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    added = 0
    for card in cards:
        block = card.get("image") or {}
        sha = block.get("sha256")
        if not sha or sha in have:
            continue
        source_sha = origins.get(str(card.get("id")))
        if not source_sha:
            # 冇原始 asset 就冇 resolver evidence 可言，寧願唔出 record：
            # 補條冇 sourceContentSha256 嘅 publicAllowed 記錄，
            # tests/data/privacy-and-images 會即刻紅。
            continue
        records.append({
            "cardNumberMatch": True,
            "contentSha256": sha,
            "height": block["height"],
            "imageKind": "raw_front",
            "languageMatch": True,
            "nativeRgba": True,
            "stdCanvas": nir.NORMALIZED_MARKER,
            "publicAllowed": False,
            "publicId": card["id"],
            "qcAt": now,
            "qcVersion": "raw-front-v4",
            "resolverEvidence": {
                "collectorMatch": True,
                "languageMetadataMatch": True,
                "method": "daily_self_heal",
                "sourceContentSha256": source_sha,
                "tcgMetadataMatch": True,
            },
            "semanticMatchStatus": "metadata_exact_unreviewed",
            "tcgMatch": True,
            "width": block["width"],
        })
        have.add(sha)
        added += 1
    records.sort(key=lambda r: r["publicId"])
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    # 換完卡圖 block（sha256 / 尺寸 / src / variants 全部變咗）就一定要重算
    # generation.contentSha256，否則 packages/market-data 個 validator 重算出嚟
    # 唔啱，成份 snapshot 會被 reject —— 而呢個腳本淨係喺「有新卡要修」嗰陣
    # 先寫檔，即係專門喺新卡入列嗰日整爛條 publish 鏈。
    snapshot["generation"]["contentSha256"] = snapshot_content_sha256(snapshot)
    snapshot_path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"wrote snapshot + {added} qc records")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
