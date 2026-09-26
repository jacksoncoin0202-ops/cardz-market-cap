#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""卡名／卡號組裝規則：唔使任何 data 都行得嗰一半，release gate 每次 push 都行。

點解要有呢個 test：呢段邏輯之前存在過，然後被 commit f24b2447 整段刪走
（`_canonical_full_name` 變成 byte-for-byte passthrough）。刪佢唔係冇道理 ——
舊版唔識折前導零，`079` 對唔上 `79/73`，出咗 `… Secret 079 79/73` 呢種雙號。
所以呢度每一條 check 都係一個真實出現過嘅形狀。

點解同 test_identity_name.py 分開：嗰個檔要 machine-private GemRate fixture，
clean checkout（pre-push release gate）成個 SKIP —— 2026-09-26 睇 gate log 先
發現，入面嘅純規則由第一日起就冇喺 gate 行過。唔使 data 嘅規則全部喺呢度；
對住真 payload 同出街 snapshot 嗰兩節留喺 test_identity_name.py。

Run: python -X utf8 scripts/test_identity_rules.py
"""
from __future__ import annotations

import inspect
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

import identity_name as N  # noqa: E402
import rebuild_036 as rebuild  # noqa: E402

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
check(
    "v2307 冇數字嘅號（`Old`）唔准 append 落卡名",
    N.complete_collector_tail(
        "2007 Pokemon Japanese 10th Movie Commemoration Promo Explosive Birth Lugia-Holo Base", "OLD"),
    "2007 Pokemon Japanese 10th Movie Commemoration Promo Explosive Birth Lugia-Holo Base",
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

# --- 5. 已出街編號只准補全，唔准改寫 ---------------------------------------
# 全部係 2026-09-26 實測嘅真卡。
check("v2255 裸號補前綴", N.restate_collector_number("091", "OP13-091"), ("OP13-091", "restated"))
check("v2289 裸號補分母", N.restate_collector_number("SV57", "SV57/SV94"), ("SV57/SV94", "restated"))
check(
    "v2247 SP 重印借原 set 前綴（CGC/Beckett 同產品一致）",
    N.restate_collector_number("030", "OP10-030"),
    ("OP10-030", "restated"),
)
check(
    "v2030 型：sibling grader 行印 EB02-061，出街 OP09-061 唔准被換走",
    N.restate_collector_number("OP09-061", "EB02-061"),
    ("OP09-061", "prefix_conflict"),
)
check(
    "有前綴唔准被冇前綴嘅分母式換走",
    N.restate_collector_number("OP09-061", "061/100"),
    ("OP09-061", "prefix_conflict"),
)
check("core 唔同 → 唔准換卡", N.restate_collector_number("061", "062/100"), ("061", "core_conflict"))
check("完整唔准降級做裸號", N.restate_collector_number("OP13-091", "091"), ("OP13-091", "keep_complete"))
check("placeholder 照補", N.restate_collector_number("unknown", "OP13-091"), ("OP13-091", "filled"))
check("細階照轉大階", N.restate_collector_number("op01-001", "OP01-001"), ("OP01-001", "restated"))
check("candidate 空 → 照舊", N.restate_collector_number("OP13-091", ""), ("OP13-091", "same"))
check("前綴", N.collector_prefix("op09-061"), "OP09")
check("分母式冇前綴", N.collector_prefix("110/080"), "")
check("完整：前綴", N.collector_is_complete("OP13-091"), True)
check("完整：分母", N.collector_is_complete("SV57/SV94"), True)
check("裸號唔完整", N.collector_is_complete("091"), False)

# --- 6. restate_display_identity：bind（全量）同每日 intake（淨係補全）------
# 2255 / 2289 / 2307 / 2030 係 2026-09-26 live DB 嗰幾行原文（read-only 攞）；
# 2030 個 psa_number_full 換咗做 mis-merge 形狀 `EB02-061`。9001 / 9002 係砌出嚟
# 嘅對照：operator 改過嘅名、已經完整但細階嘅號。
GEN = "036_test_generation"
MARS = "2025 One Piece OP13-Carrying on His Will St. Marcus Mars Alternate Art 091"
ELECTRODE = "2019 Pokemon Sun & Moon Hidden Fates Full Art/Electrode GX Base SV57"
LUGIA = "2007 Pokemon Japanese 10th Movie Commemoration Promo Explosive Birth Lugia-Holo Base"
LUFFY = "2025 One Piece Japanese English Version 2nd Anniversary Set Monkey D. Luffy Base"
ZORO = "2022 One Piece OP01-Romance Dawn Roronoa Zoro Leader OP01-001"
CURATED = "2025 One Piece St. Marcus Mars OP13 Alternate Art 091"


def bind_rows() -> list[dict]:
    return [
        {"variant_id": 2255, "canonical_name": MARS, "collector_number": "091",
         "psa_description": MARS, "psa_number_full": "OP13-091", "printed_collector_number": "091"},
        {"variant_id": 2289, "canonical_name": ELECTRODE, "collector_number": "SV57",
         "psa_description": ELECTRODE, "psa_number_full": "SV57/SV94", "printed_collector_number": "SV57"},
        {"variant_id": 2307, "canonical_name": LUGIA, "collector_number": "Old",
         "psa_description": LUGIA, "psa_number_full": "", "printed_collector_number": "Old"},
        {"variant_id": 2030, "canonical_name": f"{LUFFY} OP09-061", "collector_number": "OP09-061",
         "psa_description": f"{LUFFY} 061", "psa_number_full": "EB02-061", "printed_collector_number": "061"},
        {"variant_id": 9001, "canonical_name": CURATED, "collector_number": "091",
         "psa_description": MARS, "psa_number_full": "OP13-091", "printed_collector_number": "091"},
        {"variant_id": 9002, "canonical_name": ZORO, "collector_number": "op01-001",
         "psa_description": ZORO, "psa_number_full": "", "printed_collector_number": "op01-001"},
    ]


class BindCursor:
    """restate_display_identity 淨係得一個 SELECT 同兩種 UPDATE；其他 SQL 一律紅。"""

    def __init__(self, rows: list[dict]) -> None:
        self.rows = {row["variant_id"]: dict(row) for row in rows}
        self.generation = None
        self.selected: list[dict] = []

    def execute(self, sql: str, params=()) -> None:
        text = " ".join(sql.split())
        if text.startswith("SELECT") and "AS psa_number_full" in text:
            self.generation = params[0]
            self.selected = [dict(row) for row in self.rows.values()]
        elif text == "UPDATE catalog_variant SET collector_number=%s WHERE id=%s":
            self.rows[params[1]]["collector_number"] = params[0]
        elif text == "UPDATE catalog_variant SET canonical_name=%s WHERE id=%s":
            self.rows[params[1]]["canonical_name"] = params[0]
        else:
            raise AssertionError(f"unexpected SQL: {text[:160]}")

    def fetchall(self) -> list[dict]:
        return self.selected


def shown(cursor: BindCursor, variant_id: int) -> tuple[str, str]:
    row = cursor.rows[variant_id]
    return row["collector_number"], row["canonical_name"]


REFUSED_2030 = [{"variantId": 2030, "shown": "OP09-061", "candidate": "EB02-061",
                 "reason": "prefix_conflict"}]

daily = BindCursor(bind_rows())
out = rebuild.restate_display_identity(daily, GEN, incomplete_only=True)
check("intake：揀啱 generation", daily.generation, GEN)
check("intake：v2255 補晒號同名", shown(daily, 2255), ("OP13-091", MARS[:-3] + "OP13-091"))
check("intake：v2289 補晒號同名", shown(daily, 2289), ("SV57/SV94", ELECTRODE + "/SV94"))
check("intake：v2307 `Old` 一個 byte 都唔郁", shown(daily, 2307), ("Old", LUGIA))
check("intake：v2030 出街號同名唔郁", shown(daily, 2030), ("OP09-061", f"{LUFFY} OP09-061"))
check("intake：改過嘅名唔准冚走，號照補", shown(daily, 9001), ("OP13-091", CURATED))
check("intake：已完整嘅號唔郁（淨係補全）", shown(daily, 9002), ("op01-001", ZORO))
check("intake：restated 名單", [item["variantId"] for item in out["restated"]], [2255, 2289, 9001])
check("intake：refused", out["refused"], REFUSED_2030)
check(
    "intake：計數",
    (out["psaCollectorNumbersRestated"], out["psaNamesRestated"], out["psaCollectorNumbersRefused"]),
    (3, 2, 1),
)

bind = BindCursor(bind_rows())
out = rebuild.restate_display_identity(bind, GEN)
check("bind：v2255", shown(bind, 2255), ("OP13-091", MARS[:-3] + "OP13-091"))
check("bind：v2289", shown(bind, 2289), ("SV57/SV94", ELECTRODE + "/SV94"))
check("bind：v2307 個名都唔准加 `OLD`", shown(bind, 2307)[1], LUGIA)
check("bind：v2030 出街號同名唔郁", shown(bind, 2030), ("OP09-061", f"{LUFFY} OP09-061"))
check("bind：個名由 PSA 標籤重新組裝", shown(bind, 9001), ("OP13-091", MARS[:-3] + "OP13-091"))
check("bind：細階轉大階", shown(bind, 9002), ("OP01-001", ZORO))
check("bind：refused", out["refused"], REFUSED_2030)

# --- 7. canonical writer 全部要經唯一組裝點 -------------------------------
# 每日新卡 intake 係第六個 writer：佢 INSERT 裸號同斷尾 PSA 名，而 V2 日更唔行
# bind，2026-08-23..09-25 就咁出咗 91 張裸號卡。intake 經 restate_display_identity
# 補返，行為由 test_identity_intake.py 證明；bind 冇 DB 行唔到，睇 source。
for relative, needle in (
    ("pipelines/db_runtime.py", "complete_collector_tail"),
    ("pipelines/new_era_db_tidy.py", "complete_collector_tail"),
    ("pipelines/psa_identity_repair.py", "complete_collector_tail"),
    ("pipelines/resolve_active_psa_identity.py", "complete_collector_tail"),
    ("pipelines/rebuild_036.py", "complete_collector_tail"),
    ("pipelines/gemrate_identity_intake.py", "restate_display_identity"),
):
    source = (ROOT / relative).read_text(encoding="utf-8")
    truthy(f"{relative} 經 {needle}", needle in source)
truthy(
    "stage_bind 經 restate_display_identity（全量）",
    "restate_display_identity(cursor, generation)" in inspect.getsource(rebuild.stage_bind),
)
truthy(
    "restate_display_identity 經 restate_collector_number",
    "restate_collector_number(" in inspect.getsource(rebuild.restate_display_identity),
)

for line in FAILED:
    print(line)
print(f"{CHECKS - len(FAILED)}/{CHECKS} checks passed")
raise SystemExit(1 if FAILED else 0)
