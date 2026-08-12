#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""_pc_print_signature_ok 年份佐證規則（dated event promo）兩方向證明。

2026-08-13 Battle Festa 事故：PC 產品頁括號 [Battle Festa 2015]，PSA/GemRate
嘅 parallel label 冇年份（"Battle Festa"），催化 print_signature_mismatch，
v1874 條 binding 升唔到 exact。2014/2015 兩張孖生 Pikachu 只有卡號同年份可分，
所以規則係「bracket == parallel + 卡自己 canonical 年份，一字不差」——
佐證先過，孖生卡個年份對唔上照拒。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

from rebuild_036 import _pc_print_signature_ok  # noqa: E402

FAILURES: list[str] = []


def check(name: str, actual: bool, expected: bool) -> None:
    status = "ok" if actual == expected else "FAIL"
    if actual != expected:
        FAILURES.append(name)
    print(f"[{status}] {name}: got {actual}, want {expected}")


ROW_2015 = {
    "parallel_code": "battle festa",
    "printing_code": "",
    "canonical_name": "2015 Pokemon Japanese XY Promo Pikachu Battle Festa 175/XY-P",
}
ROW_2014 = {
    "parallel_code": "battle festa",
    "printing_code": "",
    "canonical_name": "2014 Pokemon Japanese XY Promo Pikachu Battle Festa 090/XY-P",
}
ROW_NO_YEAR = {
    "parallel_code": "battle festa",
    "printing_code": "",
    "canonical_name": "Pokemon Japanese XY Promo Pikachu Battle Festa",
}
ROW_TREATED = {
    "parallel_code": "alternate art",
    "printing_code": "aa",
    "canonical_name": "2023 One Piece OP04 Nami Alternate Art",
}

check("2015 twin: bracket year corroborates -> promote",
      _pc_print_signature_ok("Battle Festa 2015", ROW_2015), True)

check("2014 twin: same bracket, other year -> refuse",
      _pc_print_signature_ok("Battle Festa 2015", ROW_2014), False)

check("2014 twin with its own year -> promote",
      _pc_print_signature_ok("Battle Festa 2014", ROW_2014), True)

check("canonical without year cannot corroborate -> refuse",
      _pc_print_signature_ok("Battle Festa 2015", ROW_NO_YEAR), False)

check("year alone does not excuse a different parallel -> refuse",
      _pc_print_signature_ok("Poncho Wearing 2015", ROW_2015), False)

check("exact parallel equality still passes (no year involved)",
      _pc_print_signature_ok("Battle Festa", ROW_2015), True)

check("bracket-less page vs treated printing still refuses (2026-08-09 rule)",
      _pc_print_signature_ok("", ROW_TREATED), False)

if FAILURES:
    print(f"\n{len(FAILURES)} failure(s): {FAILURES}")
    raise SystemExit(1)
print("\nall dated-event signature checks passed")
