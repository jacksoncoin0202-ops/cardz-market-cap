"""Verify that every public snapshot image is local, content-addressed, and intact."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def referenced_asset_names(snapshot: dict) -> set[str]:
    """每張卡引用嘅 market-assets 檔名：master 加埋全部衍生尺寸。

    每個 image block 除咗 `src` 仲有 `variants`（`{"200": "...", "600": "..."}`，
    由 g10.DERIVATIVE_SPECS 決定）。只數 `src` 嘅話，360 張卡得 360 個檔名算「有人
    引用」，但實際落地係 1080 個——720 個生效中嘅衍生圖會被當成孤兒：verify 報
    unreferenced、quarantine 直接搬走佢哋，全站卡圖嘅細尺寸即刻爛。
    所以呢度由 snapshot 本身讀 variants，唔好 hardcode suffix 集，將來加尺寸
    唔使兩邊同步。
    """

    names: set[str] = set()
    for card in [*snapshot.get("top100", []), *snapshot.get("watchlist", [])]:
        image = card.get("image") or {}
        candidates = [image.get("src")]
        variants = image.get("variants")
        if isinstance(variants, dict):
            candidates.extend(variants.values())
        for reference in candidates:
            if not isinstance(reference, str) or not reference:
                continue
            name = Path(reference).name
            if name and name not in {".", ".."}:
                names.add(name)
    return names


def verify(
    snapshot_path: Path,
    assets_path: Path,
    manifest_path: Path | None = None,
    strict_semantic: bool = False,
    allow_unreferenced: bool = False,
) -> list[str]:
    """`allow_unreferenced` 淨係俾 quarantine 之前嗰一 pass 用。

    assets 目錄係 content-addressed 累積落嚟嘅：每次卡圖重算都會留低舊 sha 嘅檔，
    所以「未引用檔 > 0」係 quarantine 未行之前嘅正常狀態，唔應該當成 snapshot 壞。
    但每張卡本身（檔存在／hash／尺寸／QC 記錄）一定要喺搬任何檔之前驗清楚。
    """
    errors: list[str] = []
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    cards = [*snapshot.get("top100", []), *snapshot.get("watchlist", [])]
    if len(snapshot.get("top100", [])) != 100:
        errors.append("top100 does not contain exactly 100 cards")

    seen: set[str] = set()
    qc_by_hash: dict[str, dict] = {}
    if manifest_path is not None:
        if not manifest_path.is_file():
            errors.append("image QC manifest is missing")
        else:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            qc_by_hash = {
                str(record.get("contentSha256")): record
                for record in manifest.get("records", [])
                if isinstance(record, dict) and record.get("publicAllowed")
            }
    for card in cards:
        image = card.get("image", {})
        filename = Path(image.get("src", "")).name
        if not filename or filename in {".", ".."}:
            errors.append(f"{card.get('id')}: invalid image path")
            continue
        if filename in seen:
            continue
        seen.add(filename)
        path = assets_path / filename
        if not path.is_file():
            errors.append(f"{card.get('id')}: image is missing")
            continue
        actual_sha = sha256_file(path)
        if actual_sha != image.get("sha256"):
            errors.append(f"{card.get('id')}: image hash mismatch")
        if not filename.startswith(actual_sha):
            errors.append(f"{card.get('id')}: image is not content-addressed")
        if manifest_path is not None:
            qc = qc_by_hash.get(actual_sha)
            if qc is None:
                errors.append(f"{card.get('id')}: image has no public-allowed QC record")
            elif strict_semantic and qc.get("semanticMatchStatus") != "human_or_vision_confirmed":
                errors.append(f"{card.get('id')}: image semantic QC is not human_or_vision_confirmed")
        try:
            with Image.open(path) as opened:
                if list(opened.size) != [image.get("width"), image.get("height")]:
                    errors.append(f"{card.get('id')}: image dimensions do not match")
        except Exception:
            errors.append(f"{card.get('id')}: image cannot be decoded")

    # `seen` 淨係用嚟避免同一張 master 驗兩次，唔可以攞嚟判斷「有冇人引用」——
    # 佢淨係收 src，唔包 variants。
    if not allow_unreferenced:
        referenced = referenced_asset_names(snapshot)
        extras = sorted(path.name for path in assets_path.glob("*") if path.is_file() and path.name not in referenced)
        if extras:
            errors.append(f"public image directory contains {len(extras)} unreferenced files")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot", type=Path, default=ROOT / "data" / "public" / "seed-snapshot.json")
    parser.add_argument("--assets", type=Path, default=ROOT / "data" / "public" / "market-assets")
    parser.add_argument("--manifest", type=Path, default=ROOT / "manifests" / "image-qc.json")
    parser.add_argument("--strict-semantic", action="store_true")
    parser.add_argument(
        "--allow-unreferenced",
        action="store_true",
        help="quarantine 之前嗰一 pass 用：只驗卡圖本身，唔理目錄有幾多舊 sha 檔",
    )
    args = parser.parse_args()
    errors = verify(args.snapshot, args.assets, args.manifest, args.strict_semantic, args.allow_unreferenced)
    print(json.dumps({"imagesVerified": len(list(args.assets.glob('*'))), "errors": errors}, sort_keys=True))
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
