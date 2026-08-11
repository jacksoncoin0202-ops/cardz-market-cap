#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PC 兩條 browser lane 每轉一定要掃齊全部 exact stream。

點解要有呢個 test：`REFRESH_DUE_HOURS` 答嘅係「幾時**一定要**重收」（由
acceptance gate 倒推：SLA_HOURS - LANE_INTERVAL_HOURS = 12），但佢一路兼任
「幾時**先至准**重收」。lane 一日行一次、又喺 00:30 UTC 開跑，於是前一晚 12
個鐘內掂過嘅 stream 全部會俾當日嗰轉當成「夠新」跳過。

實測 2026-08-11：09:30 JST 嗰轉只打 208/993 —— 08-10 夜晚（12:13 UTC）另一轉
收咗其餘 785 條，到朝早佢哋只有 10–12 個鐘大，全部判 "ok"。個 lane 由頭到尾
冇報過錯，張數就係靜靜少咗七成八。

PriceCharting 個 sold 表硬上限 30 行，燒得最快嗰批卡 2 日就滿，跳一日就真係
少咗成交筆數，而歷史只有靠每日抄低嗰 30 行先儲得返。所以呢兩條 lane 冇
cooldown。呢個 test 守住嗰件事：cooldown 一旦被改返做預設值，下面第 2 條會紅。

Run: python -X utf8 scripts/test_pc_lane_full_sweep.py
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

from collect_control import (  # noqa: E402
    PC_REFRESH_DUE_HOURS,
    REFRESH_DUE_HOURS,
    _poll_mode,
)

FAILED: list[str] = []
CHECKS = 0


def check(label: str, got, want) -> None:
    global CHECKS
    CHECKS += 1
    if got != want:
        FAILED.append(f"FAIL {label}\n  got  {got!r}\n  want {want!r}")


def checkpoint(hours_ago: float) -> dict[str, object]:
    stamp = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=hours_ago)
    return {"last_effective_at": stamp}


# --- 1. 兩個門檻嘅意思唔同 ------------------------------------------------
check("SLA 倒推嗰個門檻仲係 12 個鐘", REFRESH_DUE_HOURS, 12)
check("PC lane 冇 cooldown", PC_REFRESH_DUE_HOURS, 0.0)

# --- 2. 前一晚收過嘅 stream，第二朝仲係要收 -------------------------------
# 10.5 個鐘 = 2026-08-11 嗰次實際被跳過嘅 785 條嘅年紀。
for hours in (0.5, 4.4, 10.5, 11.9):
    check(
        f"{hours}h 大喺預設門檻下被跳過",
        _poll_mode(has_stock=True, observed_at=None, checkpoint=checkpoint(hours)),
        "ok",
    )
    check(
        f"{hours}h 大喺 PC lane 照收",
        _poll_mode(
            has_stock=True,
            observed_at=None,
            checkpoint=checkpoint(hours),
            refresh_due_hours=PC_REFRESH_DUE_HOURS,
        ),
        "incr",
    )

# 冇成交紀錄嗰批（empty_poll_is_complete）行同一條路，唔可以淨係修一半。
check(
    "零成交卡喺 PC lane 一樣照收",
    _poll_mode(
        has_stock=False,
        observed_at=None,
        checkpoint=checkpoint(4.4),
        empty_poll_is_complete=True,
        refresh_due_hours=PC_REFRESH_DUE_HOURS,
    ),
    "incr",
)

# --- 3. 兩個 call site 唔准漏傳 -------------------------------------------
# 「有 constant 但零 call site」等於冇 constant。classify_needs 入面 PC 嗰兩個
# adapter 各要傳一次，漏一個就等於嗰條 lane 靜靜返舊制。
source = (ROOT / "pipelines" / "collect_control.py").read_text(encoding="utf-8")
check(
    "classify_needs 兩個 PC adapter 都傳咗 PC_REFRESH_DUE_HOURS",
    source.count("refresh_due_hours=PC_REFRESH_DUE_HOURS"),
    2,
)

for line in FAILED:
    print(line)
print(f"{CHECKS - len(FAILED)}/{CHECKS} checks passed")
raise SystemExit(1 if FAILED else 0)
