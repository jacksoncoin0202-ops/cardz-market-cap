#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""BOX sidecar 圖唔准被 PSA10 prune/sync 當 stale 或者要 source 必有。"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import sync_public_release_assets as S  # noqa: E402

PSA = "a" * 64
BOX = "b" * 64
STALE = "c" * 64


def _card(sha: str) -> dict:
    return {
        "image": {
            "src": f"/market-assets/{sha}.webp",
            "variants": {
                "200": f"/market-assets/{sha}_200.webp",
                "600": f"/market-assets/{sha}_600.webp",
            },
        }
    }


def _write(folder: Path, name: str) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / name).write_bytes(b"webp-fixture")


def _set(sha: str) -> list[str]:
    return [f"{sha}.webp", f"{sha}_200.webp", f"{sha}_600.webp"]


release = (ROOT / "scripts" / "daily_public_release.sh").read_text(encoding="utf-8")
entry = (ROOT / "scripts" / "daily_public_release.ps1").read_text(encoding="utf-8-sig")
bake_at = release.index("bake-public-snapshot.mjs")
sync_at = release.index("sync_public_release_assets.py")
bake_block = release[bake_at:sync_at]
assert "--no-prune" in bake_block
assert '"$SOURCE_REPO/scripts/sync_public_release_assets.py"' in release
assert '"$RELEASE_REPO/scripts/sync_public_release_assets.py"' not in release
assert "publish_assets()" in release
assert "bake/sync/validate retry" in release
assert "push retry" in release
assert "$maxAttempts = 3" in entry
assert "daily_public_release attempt" in entry
assert "cardz-last-tested-head" in release
print("POSITIVE_OK daily release uses source sync, skips bake prune, and retries 3 times")


with tempfile.TemporaryDirectory() as folder:
    root = Path(folder)
    public = root / "public"
    public.mkdir()
    source = root / "source"
    dest = public / "market-assets"
    snapshot = {
        "top100": [_card(PSA)],
        "watchlist": [],
    }
    (public / "seed-snapshot.json").write_text(json.dumps(snapshot), encoding="utf-8")
    (public / "box-subset.json").write_text(
        json.dumps({"products": [_card(BOX)]}),
        encoding="utf-8",
    )
    for name in _set(PSA):
        _write(source, name)
    for name in _set(BOX):
        _write(dest, name)
    _write(dest, f"{STALE}.webp")

    report = S.sync_release_assets(snapshot, source, dest, public / "box-subset.json")
    assert report["referenced"] == 6
    assert report["copied"] == 3
    assert report["removed"] == 1
    assert report["keptFromDestination"] == 3
    assert {path.name for path in dest.glob("*.webp")} == set(_set(PSA) + _set(BOX))
    print("POSITIVE_OK dest-only BOX is kept and stale PSA10 is removed")

    dest.joinpath(f"{BOX}.webp").unlink()
    dest.joinpath(f"{BOX}_200.webp").unlink()
    dest.joinpath(f"{BOX}_600.webp").unlink()
    try:
        S.sync_release_assets(snapshot, source, dest, public / "box-subset.json")
    except RuntimeError as error:
        assert "missing 3 referenced assets" in str(error)
        print("NEGATIVE_OK BOX missing from source and dest fails closed")
    else:
        raise AssertionError("missing BOX assets did not fail closed")
