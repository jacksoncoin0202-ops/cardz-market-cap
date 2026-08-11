#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""將 baked snapshot 冇引用嘅 market-asset 搬出 data/public/。

點解要有呢個檔：搬移器 `g10_public_snapshot.quarantine_unreferenced_assets()`
由第一日就寫好咗，但佢唯一嘅 call site 喺 `g10_public_snapshot.main()` 個
`--clean-assets` flag 後面，而 036 release 鏈**從來冇 call 過** g10 個 main
（實測：全 repo 只 import 佢啲 helper，冇一處當 CLI 行）。結果就係「有搬移器
但零 call site」＝ 冇搬移器：repo 攰到 4,535 個 sha / 1.82 GB，snapshot 真正
引用得 1,286 個 / 0.52 GB。

所以呢個檔唔係第二套實作，係畀真正嗰個 bake（`scripts/bake-public-snapshot.mjs`）
一個**預設會行**嘅入口，行返同一個搬移器。搬唔係刪，仲有 manifest，搬去
`data/runtime/`（已 gitignore）。

    python scripts/prune_public_assets.py [--snapshot <path>] [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

from g10_public_snapshot import (  # noqa: E402
    DEFAULT_ASSETS,
    DEFAULT_OUTPUT,
    quarantine_unreferenced_assets,
    referenced_asset_names,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--assets", type=Path, default=DEFAULT_ASSETS)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    snapshot_path = args.snapshot.resolve()
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    assets = args.assets.resolve()

    expected = referenced_asset_names(snapshot)
    present = [path for path in assets.iterdir() if path.is_file()] if assets.is_dir() else []
    stale = [path for path in present if path.name not in expected]
    kept = [path for path in present if path.name in expected]

    report = {
        "snapshot": str(snapshot_path),
        "generation": snapshot.get("generation", {}).get("id"),
        "assetsDirectory": str(assets),
        "referenced": len(expected),
        "presentFiles": len(present),
        "keptFiles": len(kept),
        "keptBytes": sum(path.stat().st_size for path in kept),
        "staleFiles": len(stale),
        "staleBytes": sum(path.stat().st_size for path in stale),
        "dryRun": bool(args.dry_run),
    }

    if args.dry_run:
        report["movedFiles"] = 0
        report["movedBytes"] = 0
        report["manifest"] = None
    else:
        generation = str(snapshot.get("generation", {}).get("id") or "unknown")
        quarantine = ROOT / "data" / "runtime" / "private-quarantine" / "legacy-public-assets" / generation
        moved = quarantine_unreferenced_assets(assets, snapshot, quarantine)
        report["movedFiles"] = moved["count"]
        report["movedBytes"] = moved["bytes"]
        report["manifest"] = moved["manifest"]

    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
