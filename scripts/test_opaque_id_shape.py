#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""upsert_variant 唔准鑄一個唔喺 cmc_ 命名空間嘅公開 id。

點解要有呢個 test：opaque_id 就係公開 URL（`/card/<opaque_id>`）同 sitemap entry，
而佢一寫落 DB 就俾 D7 凍結，冇得改。upsert_variant 係全個 repo 唯一一個收 caller
自己俾 id 嘅 INSERT（另外兩個 writer 自己計 cmc_+sha256），呢道窿放咗四行帶供應商
前綴嘅 id 入 catalog_variant（2026-08-11 查 DB 實測），其中三行已經出咗街。

擋只擋 INSERT：舊嗰四行照樣 UPDATE 得，唔會即刻炸咗每日採集。所以呢度兩條路都要
行一次，唔係淨係證個 raise 會 raise。

Run: python -X utf8 scripts/test_opaque_id_shape.py
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

import db_runtime as R

FAILED: list[str] = []
CHECKS = 0


def check(label: str, got: Any, want: Any) -> None:
    global CHECKS
    CHECKS += 1
    if got != want:
        FAILED.append(f"FAIL {label}\n  got  {got!r}\n  want {want!r}")


class FakeCursor:
    """淨係夠 upsert_variant 行到第一段：SELECT ... FOR UPDATE 之後就分岔。

    `existing` = None → 行 INSERT 路（新卡，要擋）。
    `existing` = row  → 行 UPDATE 路（舊卡，唔准擋）。
    """

    def __init__(self, existing: dict[str, Any] | None) -> None:
        self.existing = existing
        self.statements: list[str] = []
        self.lastrowid = 4242

    def execute(self, sql: str, params: Any = None) -> None:
        self.statements.append(" ".join(sql.split())[:60])

    def fetchone(self) -> dict[str, Any] | None:
        return self.existing


CARD = {
    "tcg": "pokemon",
    "language": "ja",
    "setName": "M2: Inferno X",
    "collectorNumber": "110/80",
    "name": "2025 Pokemon Japanese M2-Inferno X Mega Charizard X EX Special Art Rare 110",
}


def mint(opaque_id: str, existing: dict[str, Any] | None = None) -> tuple[str, FakeCursor]:
    """行一次 upsert_variant，回傳 ("ok" | 個 error message, cursor)。

    過咗個 id 閘之後，upsert_variant 會繼續行落 source-identity 嗰段，嗰段要成份
    採集 payload 先行得到。呢個 test 唔關嗰段事，所以 `KeyError` 當「id 閘放咗行」
    —— 下面仲會 check 返真係行到 INSERT / UPDATE 先算數。
    """
    cursor = FakeCursor(existing)
    try:
        R.upsert_variant(cursor, {**CARD, "pokedexId": opaque_id})
    except ValueError as error:
        return str(error), cursor
    except KeyError:
        return "ok", cursor
    return "ok", cursor


def refuses(label: str, opaque_id: str) -> None:
    global CHECKS
    CHECKS += 1
    message, cursor = mint(opaque_id)
    if message == "ok":
        FAILED.append(f"FAIL {label}\n  個 assert 冇 fire —— {opaque_id!r} 入到 DB，之後凍結")
    elif "outside the cmc_ namespace" not in message:
        FAILED.append(f"FAIL {label}\n  炸咗但唔係嗰個原因：{message}")
    elif any(s.startswith("INSERT INTO catalog_variant") for s in cursor.statements):
        FAILED.append(f"FAIL {label}\n  炸之前已經行咗 INSERT")


def accepts(label: str, opaque_id: str, existing: dict[str, Any] | None = None) -> None:
    global CHECKS
    CHECKS += 1
    message, cursor = mint(opaque_id, existing)
    expected = "UPDATE catalog_variant" if existing else "INSERT INTO catalog_variant"
    if message != "ok":
        FAILED.append(f"FAIL {label}\n  唔應該擋：{message}")
    elif not any(s.startswith(expected) for s in cursor.statements):
        FAILED.append(f"FAIL {label}\n  冇擋，但都冇行到 {expected}：{cursor.statements}")


# --- 1. 呢個 assert 真係會 fire ---------------------------------------------
refuses("供應商前綴（正正係已經入咗 DB 嗰四行嘅形狀）", "g10_356a7d75453fba4d71586411")
refuses("candidate_ 前綴", "candidate_356a7d75453fba4d71")
refuses("裸 sha，冇命名空間", "356a7d75453fba4d71586411")
refuses("cmc_ 但長度唔啱（22 hex）", "cmc_356a7d75453fba4d715864")
refuses("cmc_ 但有非 hex 字", "cmc_356a7d75453fba4d7158zz")
refuses("前面有嘢黐住（fullmatch 唔係 search）", "x_cmc_356a7d75453fba4d71586411")
refuses("後面有嘢黐住", "cmc_356a7d75453fba4d71586411_v2")
refuses("空 id", "")

# --- 2. 唔准誤殺 ------------------------------------------------------------
accepts("現行 24 hex mint", "cmc_356a7d75453fba4d71586411")
accepts("早期 20 hex mint（七張舊卡）", "cmc_f698284d7bc333408782")

# --- 3. 舊嗰四行仲要 UPDATE 得到 --------------------------------------------
# 擋只加喺 INSERT 路。如果連 UPDATE 都擋，每日採集一掂到嗰四行就即刻紅，而佢哋
# 出唔出街係 scripts/bake-public-snapshot.mjs 個閘嘅決定，唔係呢度。
accepts(
    "舊供應商前綴嘅 row 照樣 UPDATE 得",
    "g10_356a7d75453fba4d71586411",
    existing={
        "id": 1814,
        "tcg_code": CARD["tcg"],
        "card_language": CARD["language"],
        "set_name": CARD["setName"],
        "collector_number": CARD["collectorNumber"],
        "set_code": "",
        "printing_code": "",
        "rarity_code": "",
    },
)

# --- 4. alias 一鑄，舊 URL 就一定要有 308 ------------------------------------
# public_card_alias.py 鑄完 alias 之後會對返 next.config.ts。呢個對數本身唔使 DB，
# 所以喺度直接證佢會 fire —— 冇呢個 check 嘅話，鑄 alias 就等於靜靜整死幾條已經
# 出咗街嘅 URL。
import public_card_alias as A  # noqa: E402

REAL = [
    {"opaqueId": "g10_356a7d75453fba4d71586411", "publicId": "cmc_909295e09fbe7e1f3c7b44f4"},
    {"opaqueId": "g10_c43dd6aa54b7671068938692", "publicId": "cmc_4078d2704951804758fe7ca3"},
]
check("四個真 alias 全部有 308", A.missing_redirects(REAL), [])
check(
    "新鑄一個但冇寫落 next.config → 報返出嚟",
    A.missing_redirects([{"opaqueId": "g10_deadbeef", "publicId": "cmc_0123456789abcdef01234567"}]),
    ["g10_deadbeef -> cmc_0123456789abcdef01234567"],
)
check(
    "舊 id 啱但指去第二個新 id → 一樣當冇",
    A.missing_redirects(
        [{"opaqueId": "g10_356a7d75453fba4d71586411", "publicId": "cmc_ffffffffffffffffffffffff"}]
    ),
    ["g10_356a7d75453fba4d71586411 -> cmc_ffffffffffffffffffffffff"],
)

# --- 5. 個 pattern 同 FE 果邊同源 -------------------------------------------
# packages/market-data/src/id.ts 個 isOpaquePublicId 收同一組形狀。兩邊唔同步嘅
# 話，producer 鑄得出嘅 id 消費端會當佢唔合法。
ID_TS = (ROOT / "packages" / "market-data" / "src" / "id.ts").read_text(encoding="utf-8")
check("id.ts 一樣收 20 同 24 hex", "{20}" in ID_TS and "{24}" in ID_TS, True)

for line in FAILED:
    print(line)
print(f"{CHECKS - len(FAILED)}/{CHECKS} checks passed")
raise SystemExit(1 if FAILED else 0)
