#!/usr/bin/env python3
"""Bake one promoted snapshot and only its referenced image triplets into a release worktree."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

from release_frontend_bundle import sync_release_frontend  # noqa: E402


RELEASE_RUNTIME_FILES = (
    Path("pipelines/operator_control.py"),
    Path("pipelines/release_frontend_bundle.py"),
    Path("scripts/bake_tonight_release.py"),
    Path("scripts/validate_tonight_release.py"),
    Path("docs/ONE_TIME_0_TO_1_RUNBOOK.md"),
    Path("docs/FAST_E2E_RELEASE_RUNTIME.md"),
    Path("docs/OPERATOR_DUAL_MODE.md"),
    Path("docs/MODEL.md"),
    Path("PROJECT_STATE.md"),
    Path("AGENTS.md"),
)

def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sync_runtime_files(release_root: Path) -> dict[str, int]:
    copied = 0
    for relative in RELEASE_RUNTIME_FILES:
        source = (ROOT / relative).resolve()
        if not source.is_file():
            raise RuntimeError(f"required release runtime file is missing: {relative}")
        target = (release_root / relative).resolve()
        target.relative_to(release_root)
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.is_file() or target.read_bytes() != source.read_bytes():
            shutil.copy2(source, target)
            copied += 1
    return {"managed": len(RELEASE_RUNTIME_FILES), "copied": copied}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--source-assets", type=Path, required=True)
    parser.add_argument("--release-root", type=Path, required=True)
    parser.add_argument(
        "--frontend-receipt",
        type=Path,
        default=ROOT / "data/runtime/operator/frontend-bundle-receipt.json",
    )
    parser.add_argument("--expected-cards", type=int, default=762)
    args = parser.parse_args()

    snapshot_path = args.snapshot.resolve()
    source_assets = args.source_assets.resolve()
    release_root = args.release_root.resolve()
    if not (release_root / ".git").exists():
        raise RuntimeError(f"release root is not a git worktree: {release_root}")
    if release_root == snapshot_path.parent or release_root == source_assets:
        raise RuntimeError("source paths must not be the release worktree root")

    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8-sig"))
    cards = list(snapshot.get("top100") or []) + list(snapshot.get("watchlist") or [])
    if len(cards) != args.expected_cards:
        raise RuntimeError(f"snapshot has {len(cards)} cards, expected {args.expected_cards}")
    image_hashes = {
        str((card.get("image") or {}).get("sha256") or "")
        for card in cards
    }
    if any(
        len(value) != 64 or any(char not in "0123456789abcdef" for char in value)
        for value in image_hashes
    ):
        raise RuntimeError("snapshot contains an invalid image hash")

    expected_files = {
        filename
        for content_hash in image_hashes
        for filename in (
            f"{content_hash}.webp",
            f"{content_hash}_200.webp",
            f"{content_hash}_600.webp",
        )
    }
    missing = sorted(filename for filename in expected_files if not (source_assets / filename).is_file())
    if missing:
        raise RuntimeError(f"source assets are missing {len(missing)} referenced files")
    bad_base = sorted(
        content_hash
        for content_hash in image_hashes
        if sha256_file(source_assets / f"{content_hash}.webp") != content_hash
    )
    if bad_base:
        raise RuntimeError(f"source assets contain {len(bad_base)} content-hash mismatches")

    frontend_sync = sync_release_frontend(
        ROOT,
        release_root,
        args.frontend_receipt,
    )
    runtime_sync = sync_runtime_files(release_root)

    target_assets = (release_root / "data" / "public" / "market-assets").resolve()
    target_snapshot = (release_root / "data" / "public" / "seed-snapshot.json").resolve()
    target_assets.relative_to(release_root)
    target_snapshot.relative_to(release_root)
    target_assets.mkdir(parents=True, exist_ok=True)
    removed = 0
    for path in target_assets.iterdir():
        if not path.is_file():
            raise RuntimeError(f"unexpected directory in release assets: {path}")
        if path.name not in expected_files:
            path.unlink()
            removed += 1
    copied = 0
    for filename in sorted(expected_files):
        source = source_assets / filename
        target = target_assets / filename
        if not target.is_file() or sha256_file(target) != sha256_file(source):
            shutil.copy2(source, target)
            copied += 1
    shutil.copyfile(snapshot_path, target_snapshot)

    result = {
        "action": "bake-tonight-release",
        "snapshot": str(target_snapshot),
        "cards": len(cards),
        "uniqueImages": len(image_hashes),
        "assetFiles": len(expected_files),
        "copied": copied,
        "removedUnreferenced": removed,
        "frontendBundle": frontend_sync["frontendBundle"],
        "frontendCopied": frontend_sync["copied"],
        "frontendRemovedStale": frontend_sync["removedStale"],
        "frontendGeneratedFilesCleared": frontend_sync["generatedFilesCleared"],
        "runtimeFiles": runtime_sync,
        "frontendReceipt": str(args.frontend_receipt.resolve()),
    }
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
