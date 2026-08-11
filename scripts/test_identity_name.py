#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""identity_name 可以同唔可以做嘅嘢，全部對住真數據驗。

點解要有呢個 test：呢段邏輯之前存在過，然後被 commit f24b2447 整段刪走
（`_canonical_full_name` 變成 byte-for-byte passthrough）。刪佢唔係冇道理 ——
舊版唔識折前導零，`079` 對唔上 `79/73`，出咗 `… Secret 079 79/73` 呢種雙號。
所以呢度每一條 check 都係一個真實出現過嘅形狀，唔係我砌嘅 fixture：名同編號
由 data/public/seed-snapshot.json（出街嗰份）攞，分母由真 GemRate payload 攞。
邏輯再被人刪一次，呢個 test 就會紅。

Run: python -X utf8 scripts/test_identity_name.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

import identity_name as N

FAILED: list[str] = []
CHECKS = 0


def check(label: str, got, want) -> None:
    global CHECKS
    CHECKS += 1
    if got != want:
        FAILED.append(f"FAIL {label}\n  got  {got!r}\n  want {want!r}")


def truthy(label: str, value) -> None:
    check(label, bool(value), True)


# --- 1. 四個實測形狀 -------------------------------------------------------
# 每個都係真卡。前兩個係舊版 append 出雙號嗰批（實測 79 行）。
check(
    "前導零：079 認得 79/73（舊版喺呢度出雙號）",
    N.complete_collector_tail(
        "2020 Pokemon Sword & Shield Champion's Path Full Art/Charizard V Secret 079", "79/73"),
    "2020 Pokemon Sword & Shield Champion's Path Full Art/Charizard V Secret 79/73",
)
check(
    "前導零：001 認得 1/SV-P",
    N.complete_collector_tail(
        "2022 Pokemon Japanese SV Promo Pikachu Scarlet & Violet Pre-Order 001", "1/SV-P"),
    "2022 Pokemon Japanese SV Promo Pikachu Scarlet & Violet Pre-Order 1/SV-P",
)
check(
    "分母：rank 8 梅加噴火龍 110 → 110/80",
    N.complete_collector_tail(
        "2025 Pokemon Japanese M2-Inferno X Mega Charizard X EX Special Art Rare 110", "110/80"),
    "2025 Pokemon Japanese M2-Inferno X Mega Charizard X EX Special Art Rare 110/80",
)
check(
    "前綴 + 雙空格：棒球路飛 v8",
    N.complete_collector_tail(
        "2025 One Piece Promos Monkey D. Luffy Dodgers X One Piece Night  010", "EB02-010"),
    "2025 One Piece Promos Monkey D. Luffy Dodgers X One Piece Night EB02-010",
)
check(
    "個名根本冇數字尾巴 → 先至 append",
    N.complete_collector_tail(
        "2025 One Piece OP13-Carrying on His Will Don!! Card Alternate Art-Gold", "OPCD-093"),
    "2025 One Piece OP13-Carrying on His Will Don!! Card Alternate Art-Gold OPCD-093",
)

# --- 2. 唔准做嘅嘢 ---------------------------------------------------------
check(
    "已經完整 → 一個 byte 都唔郁",
    N.complete_collector_tail("2016 Pokemon Japanese XY Promo … 207/XY-P", "207/XY-P"),
    "2016 Pokemon Japanese XY Promo … 207/XY-P",
)
check("冇編號 → 唔准砌", N.complete_collector_tail("2015 Pokemon Japanese XY Promo Pikachu Battle Festa 175", ""),
      "2015 Pokemon Japanese XY Promo Pikachu Battle Festa 175")
check("冇名 → 空", N.complete_collector_tail("", "110/80"), "")
check(
    "編號已經喺個名中間出現過 → 唔准再 append",
    N.complete_collector_tail("2023 Pokemon 110/80 Something Else", "110/80"),
    "2023 Pokemon 110/80 Something Else",
)
check(
    "條尾係第二個號 → 唔准換，只准 append",
    N.complete_collector_tail("2019 Pokemon Sun & Moon Base 170", "999/181"),
    "2019 Pokemon Sun & Moon Base 170 999/181",
)

# --- 3. collector_core 折零同掉分母 ----------------------------------------
check("core 掉分母", N.collector_core("110/080"), "110")
check("core 掉前綴", N.collector_core("OP05-119"), "119")
check("core 折前導零", N.collector_core("008"), "8")
check("core placeholder 唔算號", N.collector_core("unknown"), "")
check("core 非數字保留", N.collector_core("TG13/TG30"), "tg13")

# --- 4. 分母由同一份 payload 其他 grader 行借 -------------------------------
PSA_ROWS = [
    {"grader": "psa", "card_number": "110"},
    {"grader": "beckett", "card_number": "110"},
    {"grader": "cgc", "card_number": "110/080"},
]
check("PSA 裸號 → 借 CGC 嗰個分母", N.complete_collector_number("110", PSA_ROWS), "110/080")
check(
    "已經有分母 → 唔准被 grader 行冚走",
    N.complete_collector_number("110", PSA_ROWS, printed_collector_number="110/80"),
    "110/80",
)
check(
    "numerator 唔同 → 拒絕，唔准撞去第二張卡",
    N.complete_collector_number("110", [{"grader": "cgc", "card_number": "111/080"}]),
    "110",
)
check(
    "邊個 grader 都冇分母 → 照出裸號",
    N.complete_collector_number("175", [{"grader": "cgc", "card_number": "175"}]),
    "175",
)
check("細階前綴一律轉大階", N.complete_collector_number("", [], "op01-001"), "OP01-001")

# --- 5. 對住真 GemRate payload 行一次 --------------------------------------
# 對 fixture 冇信心，所以行返張真卡：v5 個 payload 喺 disk，PSA 行印裸 110，
# CGC 行印 110/080。呢個 payload 一旦變格式，呢條 check 就要紅。
GEMRATE = ROOT / "data/private/gemrate/cards/80a8b349acb400cc08fe55617c3ff152256d371d"
receipt_path = GEMRATE / "card_details.raw.receipt.json"
if receipt_path.is_file():
    receipt = json.loads(receipt_path.read_text(encoding="utf-8-sig"))
    raw = json.loads((GEMRATE / receipt["sourcePointer"]).read_text(encoding="utf-8-sig"))
    psa_rows = [r for r in raw.get("population_data") or [] if str(r.get("grader")).casefold() == "psa"]
    truthy("真 payload 得一行 PSA", len(psa_rows) == 1)
    check("真 payload 個 PSA 行本身就係裸號", psa_rows[0].get("card_number"), "110")
    check(
        "真 payload 補得返分母",
        N.complete_collector_number(psa_rows[0].get("card_number"), raw.get("population_data")),
        "110/080",
    )
    check(
        "真 payload 個 PSA description 補得返條尾",
        N.complete_collector_tail(psa_rows[0].get("description"), "110/80"),
        "2025 Pokemon Japanese M2-Inferno X Mega Charizard X EX Special Art Rare 110/80",
    )
else:
    FAILED.append(f"FAIL 揾唔到真 GemRate payload：{receipt_path}")
    CHECKS += 1

# --- 6. 出街嗰份 snapshot 全量掃：唔准出雙號 -------------------------------
# 舊版最大罪狀係靜靜出雙號。呢度攞出街全 universe 逐張行一次，任何一張個結果
# 唔係以完整編號收尾、或者同一個 core 出現兩次，都當紅。
SNAPSHOT = ROOT / "data/public/seed-snapshot.json"
if SNAPSHOT.is_file():
    snapshot = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    cards = [*snapshot["top100"], *snapshot["watchlist"]]
    dupes: list[str] = []
    unfinished: list[str] = []
    for card in cards:
        name = str(card.get("officialName") or "")
        num = str((card.get("collectorNumber") or {}).get("display") or "")
        if not name or not num:
            continue
        out = N.complete_collector_tail(name, num)
        if not out.casefold().endswith(num.casefold()):
            unfinished.append(f"{card.get('marketRank')}: {out}")
        tokens = out.split(" ")
        cores = [N.collector_core(t) for t in tokens[-2:]]
        if len(cores) == 2 and cores[0] and cores[0] == cores[1]:
            dupes.append(f"{card.get('marketRank')}: {out}")
    check(f"出街 {len(cards)} 張冇一張出雙號", dupes, [])
    check("出街每一張都以完整編號收尾", unfinished, [])
    target = next(card for card in cards if card.get("id") == "cmc_f698284d7bc333408782e4c6")
    check("Latias & Latios 完整卡號", target["collectorNumber"]["display"], "170/181")
    check("Latias & Latios PSA 全名", target["officialName"].endswith("170/181"), True)
else:
    FAILED.append(f"FAIL 揾唔到 snapshot：{SNAPSHOT}")
    CHECKS += 1

# --- 7. 四個 canonical writer 全部要經唯一組裝點 ---------------------------
for relative in (
    "pipelines/db_runtime.py",
    "pipelines/new_era_db_tidy.py",
    "pipelines/psa_identity_repair.py",
    "pipelines/resolve_active_psa_identity.py",
    "pipelines/rebuild_036.py",
):
    source = (ROOT / relative).read_text(encoding="utf-8")
    truthy(f"{relative} 經 complete_collector_tail", "complete_collector_tail" in source)

for line in FAILED:
    print(line)
print(f"{CHECKS - len(FAILED)}/{CHECKS} checks passed")
raise SystemExit(1 if FAILED else 0)
