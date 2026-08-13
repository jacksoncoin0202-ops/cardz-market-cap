#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Leftover-5 GO pin 要擋 refresh downgrade，又唔准放寬印記尺。

pc-replay 會將 unbracketed heading 當 base print（Yamato 2026-08-09）。
Stussy/Shirahoshi/Lucky Roux 嘅 GO 頁正正係 unbracketed SP/TR。保護只可以係
`hold_exact_against_refresh`，唔可以改 `_pc_print_signature_ok`。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

import leftover5_go  # noqa: E402
from rebuild_036 import _pc_print_signature_ok  # noqa: E402

FAILURES: list[str] = []


def check(name: str, actual: object, expected: object) -> None:
    status = "ok" if actual == expected else "FAIL"
    if actual != expected:
        FAILURES.append(name)
    print(f"[{status}] {name}: got {actual!r}, want {expected!r}")


ROW_SP = {
    "parallel_code": "Special Alternate Art",
    "printing_code": "saa",
    "canonical_name": "2024 One Piece OP11 Stussy Special Alternate Art OP07-085",
}

check(
    "unbracketed heading vs SP printing still refuses (Yamato hole stays shut)",
    _pc_print_signature_ok("", ROW_SP),
    False,
)

check(
    "Stussy EN GO page is pinned",
    leftover5_go.hold_exact_against_refresh("pricecharting", 1225, "9362363"),
    True,
)
check(
    "Stussy JP [SP] console is not the EN pin",
    leftover5_go.hold_exact_against_refresh("pricecharting", 1225, "8843990"),
    False,
)
check(
    "8843990 is on the explicit reject list",
    (1225, "8843990") in leftover5_go.LEFTOVER5_PC_REJECT,
    True,
)
check(
    "Shirahoshi EN GO page is pinned",
    leftover5_go.hold_exact_against_refresh("pricecharting", 1438, "9362360"),
    True,
)
check(
    "Lucky Roux EN GO page is pinned",
    leftover5_go.hold_exact_against_refresh("pricecharting", 1228, "9967271"),
    True,
)
check(
    "Lucky Roux original OP09 foil is not pinned",
    leftover5_go.hold_exact_against_refresh("pricecharting", 1228, "8091560"),
    False,
)
check(
    "zhTW Pikachu GO page is pinned",
    leftover5_go.hold_exact_against_refresh("pricecharting", 35, "7980813"),
    True,
)
check(
    "Yellow Cheeks unlimited PC is pinned",
    leftover5_go.hold_exact_against_refresh("pricecharting", 1900, "630471"),
    True,
)
check(
    "Yellow Cheeks SNK is pinned (SKU 1st is a lie)",
    leftover5_go.hold_exact_against_refresh("snkrdunk", 1900, "766125"),
    True,
)
check(
    "wrong source does not hold",
    leftover5_go.hold_exact_against_refresh("tcgplayer", 1225, "9362363"),
    False,
)
check(
    "derived vid set is exactly the five leftover cards",
    leftover5_go.LEFTOVER5_VIDS,
    frozenset({35, 1225, 1228, 1438, 1900}),
)

# Refresh downgrade rule, in one place: exact + print-sig fail + not pinned
# would drop to manual_review. Pinned exact is held even when the heading
# would fail the Yamato rule.
would_downgrade_unpinned = (
    True  # status == exact
    and not _pc_print_signature_ok("", ROW_SP)
    and not leftover5_go.hold_exact_against_refresh("pricecharting", 1199, "6236062")
)
would_downgrade_stussy_go = (
    True
    and not _pc_print_signature_ok("", ROW_SP)
    and not leftover5_go.hold_exact_against_refresh("pricecharting", 1225, "9362363")
)
check("unpinned SP exact still eligible for replay downgrade", would_downgrade_unpinned, True)
check("Stussy GO exact is not eligible for replay downgrade", would_downgrade_stussy_go, False)

if FAILURES:
    print(f"\n{len(FAILURES)} failure(s): {FAILURES}")
    raise SystemExit(1)
print("\nall leftover-5 GO protect checks passed")
