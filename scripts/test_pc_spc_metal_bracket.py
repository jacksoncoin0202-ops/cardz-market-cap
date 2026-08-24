#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""spc 金屬括號規則（3rd Anniversary Silver / Gold）兩方向證明。

PriceCharting 賣呢隻卡係佢自己嘅 product，括號淨係寫金屬（[SP Silver]）；
GemRate 嘅 parallel label 就將 product 同金屬焊埋一齊（"3rd Anniversary-Silver"），
catalog printing_code 寫 'spc'。兩道閘因此一齊拒絕 v112 / v36：
product_agrees 要人哋重複 "3rd"/"anniversary"，_pc_print_signature_ok 認唔到
"sp silver" 係 "spc"。

規則同 dated-event 年份一樣係「佐證」，唔係「剝走」：金屬一定要係呢張卡自己
parallel_code 講嗰隻，所以 Gold 嗰版永遠拿唔到 Silver 嗰張卡，反之亦然。
coarse 'sp' code（[SP Foil] / [SP Gold]）嘅排除完全冇郁。

Run: python -X utf8 scripts/test_pc_spc_metal_bracket.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

from rebuild_036 import (  # noqa: E402
    _pc_print_signature_ok, _pc_spc_metal, _pc_spc_metal_parallel,
)
import pc_identity_discover as D  # noqa: E402

FAILURES: list[str] = []


def check(name: str, actual, expected) -> None:
    status = "ok" if actual == expected else "FAIL"
    if actual != expected:
        FAILURES.append(name)
    print(f"[{status}] {name}: got {actual!r}, want {expected!r}")


# v112: the Silver twin. v36: the Gold twin. Same product, same set page, and
# only the metal separates them.
V112 = {
    "tcg_code": "one-piece", "card_language": "ja", "collector_number": "OP01-016",
    "fp_name": "Nami",
    "canonical_name": "2024 One Piece Japanese Nami 3rd Anniversary-Silver 016",
    "set_name": "One Piece Japanese Promos",
    "fp_parallel": "3rd Anniversary-Silver",
    "parallel_code": "3rd anniversary-silver", "printing_code": "spc",
}
V36 = dict(
    V112,
    collector_number="OP01-003", fp_name="Monkey D. Luffy",
    canonical_name="2024 One Piece Japanese Monkey D. Luffy 3rd Anniversary-Gold 003",
    fp_parallel="3rd Anniversary-Gold", parallel_code="3rd anniversary-gold",
)
# The third and last spc row in the database: a rarity-suffix parallel with no
# metal word of its own, so no metal bracket may answer for it.
SR_SPC = dict(V112, fp_parallel="SR-SPC", parallel_code="sr-spc")
# The COARSE code. Same family of wording, different catalog code, and the
# 2026-08-09 exclusion on it must not move.
COARSE_SP = {"parallel_code": "sr-spc", "printing_code": "sp"}


# --- the print signature ---------------------------------------------------
check("v112 takes its own [SP Silver]",
      _pc_print_signature_ok("SP Silver", V112), True)
check("v112 refuses the Gold twin's page",
      _pc_print_signature_ok("SP Gold", V112), False)
check("v36 takes its own [SP Gold]",
      _pc_print_signature_ok("SP Gold", V36), True)
check("v36 refuses the Silver twin's page",
      _pc_print_signature_ok("SP Silver", V36), False)
check("a parallel with no metal word takes neither (silver)",
      _pc_print_signature_ok("SP Silver", SR_SPC), False)
check("a parallel with no metal word takes neither (gold)",
      _pc_print_signature_ok("SP Gold", SR_SPC), False)
check("the metal is read off the row, not the bracket",
      _pc_spc_metal(V112), "silver")
check("and only from an spc row",
      _pc_spc_metal(dict(V112, printing_code="sp")), "")

# The coarse-sp exclusion is a separate rule on a separate code: pinned here so
# the metal rule cannot be widened into it by accident.
check("coarse sp still refuses [SP Foil]",
      _pc_print_signature_ok("SP Foil", COARSE_SP), False)
check("coarse sp still refuses [SP Gold]",
      _pc_print_signature_ok("SP Gold", COARSE_SP), False)
check("coarse sp still takes its own spelled-out treatment",
      _pc_print_signature_ok("Special Alternate Art", COARSE_SP), True)
check("a bracket-less page is still not an spc card",
      _pc_print_signature_ok("", V112), False)
check("and an unrelated bracket is still not one either",
      _pc_print_signature_ok("Treasure Rare", V112), False)


# --- product agreement, the other gate that refused these two --------------
def listing(pid: str, name: str, bracket: str, number: str, slug: str) -> dict[str, str]:
    return {
        "pid": pid, "title": f"{name} [{bracket}] {number}", "slug": slug,
        "url": f"https://www.pricecharting.com/game/one-piece-japanese-promo/{slug}",
    }


SILVER = listing("5399901", "Nami", "SP Silver", "OP01-016", "nami-sp-silver-op01-016")
GOLD = listing("5399902", "Monkey.D.Luffy", "SP Gold", "OP01-003",
               "monkey-d-luffy-sp-gold-op01-003")
NAMI_GOLD = listing("5399903", "Nami", "SP Gold", "OP01-016", "nami-sp-gold-op01-016")
LUFFY_SILVER = listing("5399904", "Monkey.D.Luffy", "SP Silver", "OP01-003",
                       "monkey-d-luffy-sp-silver-op01-003")

# The seam itself: our own metal, corroborated by their listing, spelled the
# way PriceCharting spells it -- never a metal read off their page alone.
check("their [SP Silver] corroborates our silver row",
      _pc_spc_metal_parallel(V112, SILVER["title"], SILVER["url"]), "sp silver")
check("their [SP Gold] does not corroborate our silver row",
      _pc_spc_metal_parallel(V112, NAMI_GOLD["title"], NAMI_GOLD["url"]), "")
check("their [SP Silver] does not corroborate our gold row",
      _pc_spc_metal_parallel(V36, LUFFY_SILVER["title"], LUFFY_SILVER["url"]), "")
check("their [SP Gold] corroborates our gold row",
      _pc_spc_metal_parallel(V36, GOLD["title"], GOLD["url"]), "sp gold")
check("a metal-less spc row is corroborated by nothing",
      _pc_spc_metal_parallel(SR_SPC, SILVER["title"], SILVER["url"]), "")

ok, why = D.judge_listing(V112, SILVER)
check(f"the whole judgement accepts v112's own page ({why})", ok, True)
ok, why = D.judge_listing(V36, GOLD)
check(f"and v36's own page ({why})", ok, True)
ok, why = D.judge_listing(V112, NAMI_GOLD)
check("v112 is refused the Gold page of the same card", ok, False)
check("product agreement is what refuses it, still demanding our own metal",
      why.startswith("product_mismatch:") and "silver" in why, True)
ok, why = D.judge_listing(V36, LUFFY_SILVER)
check("v36 is refused the Silver page of the same card", ok, False)
check("and product agreement refuses that one too",
      why.startswith("product_mismatch:") and "gold" in why, True)
ok, why = D.judge_listing(SR_SPC, SILVER)
check("the metal-less spc row earns no page at all", ok, False)

if FAILURES:
    print(f"\n{len(FAILURES)} failure(s): {FAILURES}")
    raise SystemExit(1)
print("\nall spc metal-bracket checks passed")
