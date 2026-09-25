#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Master Ball 同 rh 兩個 printing code 嘅括號合約，兩方向釘死。

2026-08-24 讀 DB 讀返嚟嘅事實：18 隻 mb variant（v83, 123, 985, 1003, 1006,
1012, 1018, 1029, 1042, 1049, 1059, 1061, 1070, 1073, 1087, 1456, 1582, 1583）
根本冇 exact PC binding——佢哋剩低嘅 row 係 manual_review / rejected / conflict
candidate，全部指住 BASE product page（5326xxx / 79800xx，h1 冇括號）。真正嘅
[Master Ball] product 係 PriceCharting 喺同一個 console 另外開嘅 pid（prod 已經
有 4 個綁咗 exact：5399884, 7980451, 5408343, 5399883）。

所以呢度冇新機制，只有合約：現有 mb synonym 已經識讀 [Master Ball]，而 base
page 嘅無括號形狀永遠升唔到 exact，operator 補齊兩個 console listing 之後行返
現有 discover -> pc-identity-reverify 就得。

同一份檔亦都釘住新加嘅 rh synonym：括號 [Reverse] 認得 printing rh，但
[Master Ball] 一世都唔可以——v1074 canonical 講嘅係淨 Reverse Holo，master-ball
嗰版係 v235 嘅。

Run: python -X utf8 scripts/test_pc_master_ball_rebind.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

from rebuild_036 import (  # noqa: E402
    _PC_BRACKET_SYNONYMS, _pc_bracket_printing_code, _pc_print_signature_ok,
)

FAILURES: list[str] = []


def check(name: str, actual, expected) -> None:
    status = "ok" if actual == expected else "FAIL"
    if actual != expected:
        FAILURES.append(name)
    print(f"[{status}] {name}: got {actual!r}, want {expected!r}")


# A held mb variant: the catalog spells the whole finish, PriceCharting's
# product page brackets just the seal.
MB = {
    "tcg_code": "pokemon", "parallel_code": "master ball reverse holo",
    "printing_code": "mb",
    "canonical_name": "2023 Pokemon Japanese Scarlet & Violet 151 Mew ex"
                      " Master Ball Reverse Holo 205/165",
}
# v1074's shape: printing rh, and the canonical name claims the plain reverse
# holo finish. The master-ball page belongs to another card.
RH = {
    "tcg_code": "pokemon", "parallel_code": "reverse holo", "printing_code": "rh",
    "canonical_name": "2023 Pokemon Japanese Scarlet & Violet 151 Mew ex"
                      " Reverse Holo 151/165",
}


# --- (i) the existing synonym already reads the product page ---------------
check("[Master Ball] is this card's own print",
      _pc_print_signature_ok("Master Ball", MB), True)
check("and the synonym table carries the three observed Master Ball wordings",
      _PC_BRACKET_SYNONYMS["mb"],
      frozenset({"master ball", "master ball reverse", "master ball reverse holo"}))

# --- (ii) the stale base-page candidates can never promote -----------------
# Every held mb row points at a base product page whose heading carries no
# bracket. That page is the base card's, and an mb variant may not take it --
# which is why this needs no rebind path: the wrong candidate is refused where
# it always was, and the right pid is simply a different product.
check("a bracket-less base page is not a Master Ball card",
      _pc_print_signature_ok("", MB), False)
check("nor is the base card's own [Reverse] page",
      _pc_print_signature_ok("Reverse", MB), False)
check("a page that says nothing about the seal cannot be read as it",
      _pc_bracket_printing_code(""), "")

# --- (iii) the rh synonym must not reach the master-ball page --------------
check("printing rh may not take the Master Ball page",
      _pc_print_signature_ok("Master Ball", RH), False)
check("not even spelled the way the catalog spells the finish",
      _pc_print_signature_ok("Master Ball Reverse Holo", RH), False)

# --- (iv) what the rh synonym IS for --------------------------------------
check("[Reverse] is printing rh's own bracket",
      _pc_print_signature_ok("Reverse", RH), True)
check("the bracket vocabulary names it too", _pc_bracket_printing_code("Reverse"), "rh")
check("but [Reverse] is not an Alternate Art card",
      _pc_print_signature_ok("Reverse", {"parallel_code": "sr", "printing_code": "aa"}),
      False)
check("nor a Treasure Rare one",
      _pc_print_signature_ok("Reverse", {"parallel_code": "sr", "printing_code": "tr"}),
      False)
check("nor a Master Ball one (iii the other way round)",
      _pc_print_signature_ok("Reverse", {"parallel_code": "master ball reverse holo",
                                         "printing_code": "mb"}), False)
# The bracket-less path is untouched by the new synonym: an rh row still names
# a treatment, so a page with no bracket at all is still not this card.
check("a bracket-less page is still not a reverse-holo card",
      _pc_print_signature_ok("", RH), False)
# The synonym carries the one word that was read off a page and no more. RH
# above passes "[Reverse Holo]" on plain parallel equality, which is a
# different rule, so the vocabulary is pinned on a row that spells no parallel.
check("the synonym itself is only the one word", _PC_BRACKET_SYNONYMS["rh"],
      frozenset({"reverse"}))
check("a longer wording is not carried by the synonym",
      _pc_print_signature_ok("Reverse Holo", {"parallel_code": "", "printing_code": "rh"}),
      False)
check("while the one word is",
      _pc_print_signature_ok("Reverse", {"parallel_code": "", "printing_code": "rh"}),
      True)

# --- (v) Poke Ball is the monster-ball reverse, never Master Ball / plain rh
PB = {
    "tcg_code": "pokemon",
    "parallel_code": "monster ball mirror",
    "printing_code": "rh",
    "canonical_name": "2024 Pokemon Japanese Terastal Festival ex Umbreon"
                      " Poke Ball Reverse Holo 092/187",
}
check("[Poke Ball] is the monster-ball reverse card",
      _pc_print_signature_ok("Poke Ball", PB), True)
check("that card still may not take Master Ball",
      _pc_print_signature_ok("Master Ball", PB), False)
check("a plain reverse-holo card may not take Poke Ball",
      _pc_print_signature_ok("Poke Ball", RH), False)
check("a Master Ball card may not take Poke Ball",
      _pc_print_signature_ok("Poke Ball", MB), False)

if FAILURES:
    print(f"\n{len(FAILURES)} failure(s): {FAILURES}")
    raise SystemExit(1)
print("\nall master-ball / rh bracket checks passed")
