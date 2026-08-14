#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""incr 喺 36h SLA 內可以本地 replay，唔使再開 Chrome。

2026-08-15 DADDY 放開「每轉 CDP 掃齊」。classify 仍然 PC_REFRESH_DUE_HOURS=0
（全部 due），skip 只准發生喺 partition。呢個 test 守住：10.5h 大嘅 incr HTML
要 replay；40h 大嘅要落網路。

Run: python -X utf8 scripts/test_pc_incr_local_replay.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

import collect_control as cc  # noqa: E402
import pc_psa10_price_derivation as deriv  # noqa: E402

FAILED: list[str] = []
CHECKS = 0


def check(label: str, got, want) -> None:
    global CHECKS
    CHECKS += 1
    if got != want:
        FAILED.append(f"FAIL {label}\n  got  {got!r}\n  want {want!r}")


def main() -> int:
    test_tmp_root = ROOT / "data" / "runtime" / "test-tmp"
    test_tmp_root.mkdir(parents=True, exist_ok=True)
    tmpdir = Path(tempfile.mkdtemp(prefix="pc_incr_replay_", dir=str(test_tmp_root)))
    html_path = tmpdir / "1.html"
    html_path.write_text("x" * 6000, encoding="utf-8")
    mid = time.time() - (10.5 * 3600)
    os.utime(html_path, (mid, mid))
    row = {
        "variant_id": 1,
        "pc_product_id": "7108819",
        "pc_url": "https://www.pricecharting.com/game/pokemon-promo/test-card-85",
        "html_path": str(html_path.relative_to(ROOT)).replace("\\", "/"),
    }
    item = {"variantId": 1, "externalId": "7108819", "modeNeeded": "incr"}

    def fake_map(items, *, mode, label):
        return tmpdir / "map.jsonl", [row]

    original_map = cc._pc_subset_map
    original_validate = deriv.validate_pc_psa10
    cc._pc_subset_map = fake_map  # type: ignore[assignment]
    deriv.validate_pc_psa10 = lambda _row: (  # type: ignore[assignment]
        {"field": "VGPC.chart_data.manualonly.last", "observed_date": "2026-08-14"},
        "accepted",
    )
    try:
        replayed, network, report = cc.partition_local_pc_stock_pages(
            [item], mode="incr", dry_run=False
        )
        check("10.5h 內 incr 會 replay", [i["variantId"] for i in replayed], [1])
        check("10.5h 內 incr 唔落網路", [i["variantId"] for i in network], [])
        check("replay 有 payload hash", bool(report["payloadShaByVariant"].get("1")), True)

        old = time.time() - (40 * 3600)
        os.utime(html_path, (old, old))
        replayed_old, network_old, report_old = cc.partition_local_pc_stock_pages(
            [item], mode="incr", dry_run=False
        )
        check("40h incr 唔 replay", [i["variantId"] for i in replayed_old], [])
        check("40h incr 要 CDP", [i["variantId"] for i in network_old], [1])
        check(
            "40h 原因係 SLA",
            report_old["networkReasons"].get("1"),
            "local_exact_html_exceeds_36h_sla",
        )
    finally:
        cc._pc_subset_map = original_map
        deriv.validate_pc_psa10 = original_validate
        html_path.unlink(missing_ok=True)
        tmpdir.rmdir()

    for line in FAILED:
        print(line)
    print(f"{CHECKS - len(FAILED)}/{CHECKS} checks passed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
