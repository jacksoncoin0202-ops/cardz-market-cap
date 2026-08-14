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

import re
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

# --- 4. 節奏參數只准住喺 refresher，唔准喺 CLI 寫死 ------------------------
# 舊版 refresher 寫住 politeness sleep，但 collect_control 個 CLI 硬塞
# `--pc-sleep` default=4.0，所以真正決定 993 張跑幾耐嘅係 CLI 嗰一行 —— refresher
# 改幾多次都冇用。實測後果：5.2 s/頁 × 993 = 95 分鐘，入面 66 分鐘淨係喺度瞓。
# 兩個 flag 一旦有 default，calibration 就會再次被繞過。
for flag in ("--pc-sleep", "--pc-workers"):
    line = next(
        (row for row in source.splitlines() if f'"{flag}"' in row),
        "",
    )
    check(f"{flag} 存在", bool(line), True)
    check(f"{flag} 冇寫死 default（節奏由 refresher 話事）", "default=" in line, False)

check(
    "refresh_pc_pages 有將 tab 數傳落去",
    source.count('cmd.extend(["--workers"'),
    1,
)

# --- 5. 唔准 route interception 擋走 subresource -------------------------
# 呢個係反直覺、而且量過先知嘅嘢，所以一定要有閘 —— 淨係寫一段 comment，下一個人
# （或者下一個 agent）睇見「一版打 31 個 request，我只要 1 個 document」一定會覺得
# 擋走圖同 css 可以快三倍，然後親手做返一次。
#
# 實測 A/B（2026-08-12，同一批 40 張、同一設定 2 分頁 / 3.0s、隔 3 分鐘背對背）：
#     唔擋：40/40 全清，2.05 s/頁
#     擋咗：38/40，兩次 429，3.69 s/頁
# Cloudflare 見到「瀏覽器」淨係攞 HTML 唔攞 css／圖，直接當你係 bot。要扮足全套。
refresher = (ROOT / "pipelines" / "pc_cdp_sold_refresh_win.py").read_text(encoding="utf-8")
for banned in ("page.route(", "context.route(", "route.abort("):
    check(f"refresher 冇用 {banned} 擋 subresource", banned in refresher, False)

# 分頁唔係越多越好：4 分頁 2.78 s/頁，慢過 2 分頁嘅 2.03 s/頁 —— 每食一次 429 就要
# 全部分頁一齊停 30/60/120 秒。乾淨上限約 0.5 goto/s。改大過呢度就係冇量過就改。
# 用 regex 唔用 import：refresher module-level 就 import playwright，唔應該因為跑
# 一條 test 就要成套 browser stack 裝齊。
def constant(name: str) -> float:
    match = re.search(rf"^{name} = ([0-9.]+)$", refresher, re.M)
    return float(match.group(1)) if match else float("nan")


check("PC_TABS 冇超出量過嘅範圍", constant("PC_TABS") <= 4, True)
check("PC_SLEEP_SECONDS 冇低過 2026-08-15 fetch 乾淨值", constant("PC_SLEEP_SECONDS") >= 1.5, True)
check("PC 預設用 in-page fetch", 'PC_TRANSPORT = "fetch"' in refresher, True)
check("incr 唔再因為 mode 就強制 CDP", "incremental_refresh_due" in source, False)

for line in FAILED:
    print(line)
print(f"{CHECKS - len(FAILED)}/{CHECKS} checks passed")
raise SystemExit(1 if FAILED else 0)
