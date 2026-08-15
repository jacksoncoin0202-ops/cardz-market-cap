#!/usr/bin/env python3
"""Mirror snapshot + BOX sidecar assets into a release tree.

PSA10 images come from source (fe-db materialize). BOX images live only in
the release git tree. Missing from source but present in destination: keep
dest. Missing from both: fail closed.
"""

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
    """037 BOX sidecar images. Daily prune/sync must keep these or /box images vanish."""
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


def wanted_release_assets(snapshot: dict, box_path: Path) -> set[str]:
    return referenced_assets(snapshot) | referenced_box_assets(box_path)


def sync_release_assets(snapshot: dict, source: Path, destination: Path, box_path: Path) -> dict:
    wanted = wanted_release_assets(snapshot, box_path)
    source = source.resolve()
    destination = destination.resolve()
    destination.mkdir(parents=True, exist_ok=True)

    missing_both = sorted(
        name
        for name in wanted
        if not (source / name).is_file() and not (destination / name).is_file()
    )
    if missing_both:
        raise RuntimeError(
            f"source and destination are missing {len(missing_both)} referenced assets: {missing_both[:10]}"
        )

    copied = 0
    kept_from_destination = 0
    for name in sorted(wanted):
        source_path = source / name
        destination_path = destination / name
        if source_path.is_file():
            if not destination_path.is_file() or not filecmp.cmp(source_path, destination_path, shallow=False):
                # copyfile + mode 0644, not copy2: source on /mnt/c (drvfs) always
                # reports 0777. copy2 would turn every webp 100644 -> 100755 and
                # drown the real content diff in a 3966-file mode-only commit.
                shutil.copyfile(source_path, destination_path)
                destination_path.chmod(0o644)
                copied += 1
        else:
            kept_from_destination += 1

    removed = 0
    for path in destination.glob("*.webp"):
        if path.name not in wanted:
            path.unlink()
            removed += 1

    return {
        "referenced": len(wanted),
        "copied": copied,
        "removed": removed,
        "keptFromDestination": kept_from_destination,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args()

    snapshot = json.loads(args.snapshot.read_text(encoding="utf-8"))
    report = sync_release_assets(
        snapshot,
        args.source,
        args.destination,
        args.snapshot.parent / "box-subset.json",
    )
    print(json.dumps(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
