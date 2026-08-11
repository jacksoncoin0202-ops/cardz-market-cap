#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""將 baked snapshot 引用嘅 market-asset 由私有 bytes 抄入 data/public/。

點解要有呢個檔：採集側 accept 一張新 canonical 圖嘅時候，bytes 只會落私有路徑
（`market_image_asset.private_object_key`，例如
`data/runtime/operator/snk-en-assets/<sha>.webp`）。由私有搬去
`data/public/market-assets/` 呢一步，成條 release 鏈**冇人做過**：
`scripts/bake-public-snapshot.mjs` 只 prune（搬走多餘），
`scripts/daily_public_release.sh` 只由 source repo 抄去 release repo，
`scripts/materialize_snapshot_assets.py` 係另一個紀元嘅嘢（要
`generation.productionEligible`，036 snapshot 冇呢個欄，即係零 call site）。

所以新收嘅圖永遠上唔到街。舊版 `live-db-snapshot.ts` 仲喺 bake 嗰陣摸檔案，摸唔到
就靜靜寫 `sha256: ""` —— 即係「啱啱收到嘅新圖」變成「冇圖」，出 placeholder。
實測 2026-08-11：`collect_control:snk_en_image` accept 咗 677 張新 canonical 圖，
其中 28 張喺出街榜（rank 14 / 55 / 58 …），全部變 placeholder，跟住
`sync_public_release_assets.py` 見到 `/card-placeholder.svg` 就炸咗成個發佈。

呢個檔就係嗰一步。master 一個 byte 都唔改（snapshot 嘅 sha256 係內容定址，改
master 即刻撞爛 hash 驗證）—— 只係 hash 對得上先抄，抄完再對一次。derivative
行返 `build_asset_derivatives.build`（429x600 畫布 + 圓角 + _200/_600），同已經
出街嗰批完全一致。

揾唔到 hash 對得上嘅本機 bytes 就硬死。呢度靜唔得：靜咗就等於個榜靜靜少咗卡。

    python -X utf8 scripts/materialize_public_assets.py [--snapshot <path>]
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))
sys.path.insert(0, str(ROOT / "scripts"))

import build_asset_derivatives as derivatives  # noqa: E402
from materialize_snapshot_assets import (  # noqa: E402
    load_asset_rows,
    sha256_file,
    source_candidates,
)

SUFFIXES = derivatives.SUFFIXES


def snapshot_shas(path: Path) -> set[str]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    cards = [*(payload.get("top100") or []), *(payload.get("watchlist") or [])]
    shas = {str((card.get("image") or {}).get("sha256") or "") for card in cards}
    invalid = sorted(value for value in shas if len(value) != 64)
    if invalid:
        raise SystemExit(
            f"snapshot 有 {len(invalid)} 個唔係 64 位嘅 image sha —— "
            "bake 唔應該出得到呢啲，去查 live-db-snapshot.ts 而唔係喺呢度補"
        )
    return shas


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot", type=Path, default=ROOT / "data" / "public" / "seed-snapshot.json")
    parser.add_argument("--assets", type=Path, default=ROOT / "data" / "public" / "market-assets")
    args = parser.parse_args()

    assets = args.assets.resolve()
    assets.mkdir(parents=True, exist_ok=True)
    wanted = snapshot_shas(args.snapshot.resolve())

    missing = sorted(sha for sha in wanted if not (assets / f"{sha}.webp").is_file())
    rows = load_asset_rows(set(missing)) if missing else {}
    resolved: dict[str, Path] = {}
    for sha in missing:
        for row in rows.get(sha, []):
            for candidate in source_candidates(str(row.get("private_object_key") or ""), assets):
                if candidate.is_file() and sha256_file(candidate) == sha:
                    resolved[sha] = candidate
                    break
            if sha in resolved:
                break
    unresolved = sorted(set(missing) - set(resolved))
    if unresolved:
        raise SystemExit(
            f"揾唔到 hash 對得上嘅本機 bytes：{len(unresolved)} 個 master —— "
            f"{', '.join(sha[:12] for sha in unresolved[:5])}"
        )

    for sha, source in resolved.items():
        target = assets / f"{sha}.webp"
        shutil.copyfile(source, target)
        if sha256_file(target) != sha:
            raise SystemExit(f"抄完 master hash 對唔返：{sha}")

    built = [
        sha for sha in sorted(wanted)
        if not all((assets / f"{sha}_{suffix}.webp").is_file() for suffix in SUFFIXES)
    ]
    for sha in built:
        derivatives.build(assets, sha, True)

    incomplete = sorted(
        sha for sha in wanted
        if not all(
            (assets / f"{sha}{suffix}.webp").is_file()
            for suffix in ("", *(f"_{value}" for value in SUFFIXES))
        )
    )
    if incomplete:
        raise SystemExit(f"仲有 {len(incomplete)} 個 sha 唔齊三件套")

    print(json.dumps({
        "snapshotImages": len(wanted),
        "mastersMaterialized": len(resolved),
        "derivativeSetsBuilt": len(built),
        "materializedFrom": sorted({str(path.parent) for path in resolved.values()}),
    }, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
