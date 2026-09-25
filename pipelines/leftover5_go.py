#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Leftover-5 GO pins — one map, every refresh lane reads it.

DADDY 2026-08-13：identity backlog leftover 5 張已經用 GemRate/PSA/eBay POP
對到正確產品。後續 `pc-replay` / `snk-refresh` 唔准因為 heading 冇 [SP]/[TR]、
PC census 欄位壞、或者 SNK SKU 誤標 1st 而將 exact 打落 `manual_review`。

唔好複製呢個 set。新 call site 只准 call `hold_exact_against_refresh`
（形狀 22 / AGENTS.md 13）。個案同方法論：
docs/LEFTOVER5_IDENTITY_20260813.md

呢啲 pin **唔係**放鬆 `_pc_print_signature_ok`。unbracketed heading 對
treated printing 仍然拒絕（Yamato 2026-08-09）。例外只係呢五對 (vid, pid)。

S12 另外要 live quote revision（`pc_psa10_local_history_v1` 頭點）同
case-insensitive `PC_PRICE_LANGUAGES`（`zhtw` ≠ `zhTW` 曾經令 v35 route=none）。
見 runbook 形狀 34。
"""
from __future__ import annotations

# (variant_id, pricecharting product id) → why this page is the card.
LEFTOVER5_PC_GO: dict[tuple[int, str], str] = {
    (35, "7980813"): (
        "unique zhTW 5th Anniversary 153/SV-P; GemRate 5316; eBay PSA10 ~4851"
    ),
    (1225, "9362363"): (
        "EN Stussy SP pop 1576 vs JP twin v1875 pop 1247; TCG 632506 (SP); "
        "JP keeps SNK 520534; unbracketed heading is still the SP product"
    ),
    (1228, "9967271"): (
        "unique EN Lucky Roux TR; no JP twin; TCG 632773 (TR); "
        "not [Foil] 8091560"
    ),
    (1438, "9362360"): (
        "EN Shirahoshi SP pop 1670 vs JP twin v2167 pop 1530; TCG 632502 (SP); "
        "JP keeps SNK 520532; unbracketed heading is still the SP product"
    ),
    (1900, "630471"): (
        "unlimited Yellow Cheeks; eBay/PSA PSA10 ~3079 ~= GemRate 3104; "
        "1st is 549; PC census 5 is a broken field, not another card"
    ),
}

# (variant_id, snkrdunk item id) → why this listing is the card.
LEFTOVER5_SNK_GO: dict[tuple[int, str], str] = {
    (1900, "766125"): (
        "name is Yellow Cheeks; SKU '1st' is a storefront lie; "
        "version is unlimited (GemRate 3104 ~= PSA ~3079)"
    ),
}

LEFTOVER5_VIDS = frozenset(
    {vid for vid, _ in LEFTOVER5_PC_GO} | {vid for vid, _ in LEFTOVER5_SNK_GO}
)

# JP twin of leftover-5 EN Shirahoshi (v1438 / 9362360). Same PriceCharting
# filing: OP11 SP sold unbracketed on Fist of Divine Speed. Keep this pair
# OUT of LEFTOVER5_PC_GO so the 2026-08-13 five-card adjudicate script
# cannot re-run it. Refresh still has to hold the exact once the judge
# promotes it; unbracketed vs SP would otherwise downgrade (Yamato).
_PC_REFRESH_HOLD_EXTRA: dict[tuple[int, str], str] = {
    (2167, "9362267"): (
        "JP Shirahoshi SP EB01-057 packed in OP11; unbracketed Fist of "
        "Divine Speed; sales SP ALTERNATE ART; ungraded $64.48; EN twin "
        "v1438 exact 9362360"
    ),
    (1904, "11302596"): (
        "Mega Gengar EX Incorrect Texture 230/193; PC unbracketed #230 "
        "census 963 ≈ GemRate 1410 / PSA Incorrect Texture ~1040-1443; "
        "correct MA twin v339 pop 39082 is 40× the census, not this page"
    ),
}

# Pages that look like leftover-5 candidates but are a different card.
LEFTOVER5_PC_REJECT: frozenset[tuple[int, str]] = frozenset({
    (1225, "8843990"),  # JP Stussy [SP] console; belongs to v1875
    (1228, "8091560"),  # original OP09 [Foil], not the OP11 TR reprint
})


def pc_go(variant_id: int, pid: str) -> bool:
    return (int(variant_id), str(pid)) in LEFTOVER5_PC_GO


def snk_go(variant_id: int, item_id: str) -> bool:
    return (int(variant_id), str(item_id)) in LEFTOVER5_SNK_GO


def hold_exact_against_refresh(
    source: str, variant_id: int, external_id: str,
) -> bool:
    """True = pc-replay / snk-refresh must leave this exact bind alone.

    One function, every refresh lane. Do not inline the frozenset at the
    downgrade site.
    """
    if source == "pricecharting":
        key = (int(variant_id), str(external_id))
        return key in LEFTOVER5_PC_GO or key in _PC_REFRESH_HOLD_EXTRA
    if source == "snkrdunk":
        return snk_go(variant_id, external_id)
    return False
