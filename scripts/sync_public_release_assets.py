#!/usr/bin/env python3
"""Mirror exactly the snapshot-referenced public assets into a release tree."""

from __future__ import annotations

import argparse
import filecmp
import json
import re
import shutil
from pathlib import Path


ASSET_NAME = re.compile(r"^[0-9a-f]{64}(?:_(?:200|600))?\.webp$")


def add_asset_name(names: set[str], value: object) -> None:
    if not isinstance(value, str) or not value:
        return
    name = value.rsplit("/", 1)[-1]
    if not ASSET_NAME.fullmatch(name):
        raise RuntimeError(f"invalid public asset reference: {value}")
    names.add(name)


def referenced_assets(snapshot: dict) -> set[str]:
    names: set[str] = set()
    for card in [*(snapshot.get("top100") or []), *(snapshot.get("watchlist") or [])]:
        image = card.get("image") or {}
        add_asset_name(names, image.get("src"))
        for value in (image.get("variants") or {}).values():
            add_asset_name(names, value)
    return names


def referenced_box_assets(box_path: Path) -> set[str]:
    """037 BOX sidecar images. Daily prune must keep these or /box 圖會消失。"""
    if not box_path.is_file():
        return set()
    block = json.loads(box_path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for product in block.get("products") or []:
        image = (product or {}).get("image") or {}
        add_asset_name(names, image.get("src"))
        src = image.get("src")
        if isinstance(src, str) and src.endswith(".webp"):
            add_asset_name(names, src.replace(".webp", "_200.webp"))
            add_asset_name(names, src.replace(".webp", "_600.webp"))
    return names


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args()

    snapshot = json.loads(args.snapshot.read_text(encoding="utf-8"))
    wanted = referenced_assets(snapshot)
    wanted |= referenced_box_assets(args.snapshot.parent / "box-subset.json")
    source = args.source.resolve()
    destination = args.destination.resolve()
    destination.mkdir(parents=True, exist_ok=True)

    missing = sorted(name for name in wanted if not (source / name).is_file())
    if missing:
        raise RuntimeError(f"source tree is missing {len(missing)} referenced assets: {missing[:10]}")

    copied = 0
    for name in sorted(wanted):
        source_path = source / name
        destination_path = destination / name
        if not destination_path.is_file() or not filecmp.cmp(source_path, destination_path, shallow=False):
            # copyfile + 固定 0644，唔用 copy2：source 喺 /mnt/c（drvfs）上面永遠
            # 報 0777，copy2 會照抄，於是每個 webp 都由 100644 變 100755，
            # 每次 release 都出一個 3966 檔嘅純 mode diff，真正嘅內容改動被淹冇。
            shutil.copyfile(source_path, destination_path)
            destination_path.chmod(0o644)
            copied += 1

    removed = 0
    for path in destination.glob("*.webp"):
        if path.name not in wanted:
            path.unlink()
            removed += 1

    print(json.dumps({"referenced": len(wanted), "copied": copied, "removed": removed}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
