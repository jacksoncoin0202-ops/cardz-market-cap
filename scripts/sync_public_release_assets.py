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


def referenced_assets(snapshot: dict) -> set[str]:
    names: set[str] = set()
    for card in [*(snapshot.get("top100") or []), *(snapshot.get("watchlist") or [])]:
        image = card.get("image") or {}
        for value in [image.get("src"), *((image.get("variants") or {}).values())]:
            if not isinstance(value, str) or not value:
                continue
            name = value.rsplit("/", 1)[-1]
            if not ASSET_NAME.fullmatch(name):
                raise RuntimeError(f"invalid public asset reference: {value}")
            names.add(name)
    return names


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args()

    snapshot = json.loads(args.snapshot.read_text(encoding="utf-8"))
    wanted = referenced_assets(snapshot)
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
            shutil.copy2(source_path, destination_path)
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
