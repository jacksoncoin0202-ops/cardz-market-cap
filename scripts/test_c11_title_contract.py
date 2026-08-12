#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""C11 exact-gate 嘅 title↔卡號矛盾契約（規矩 9：種毒證明會 fire）。

2026-07-14 v1326 Latias 事故：PC 產品頁 binding exact，但頁入面俾 PC 自己
fuzzy match 塞咗一條 Tag Bolt #060/095 嘅 $91 成交，接受後 30d +556%。
呢個測試用返「真毒 title 原文」種毒：佢一定要紅；合法 title 一定唔准誤中。
"""
import sys
from pathlib import Path
from collections import Counter

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

import c11_pc_sold_ingest as C  # noqa: E402

FAILED: list[str] = []


def check(label: str, got: object, want: object) -> None:
    if got == want:
        print(f"ok   {label}")
    else:
        FAILED.append(label)
        print(f"FAIL {label}: got {got!r}, want {want!r}")


# ── 1. 判別器本體 ────────────────────────────────────────────────
# 真毒原文（market_sale_observation v1326, 2026-07-14, $91）：
POISON_TITLE = "2018 Pokemon SM Tag Bolt Latios & Latias GX #060/095 PSA 10 GEM MINT"
check("real poison title fires (60 != 113)",
      C.title_collector_contradiction(POISON_TITLE, "113"), True)

check("matching claim passes",
      C.title_collector_contradiction(
          "2019 Pokemon Team Up Latias & Latios GX 113/181 PSA 10", "113"), False)

check("zero-padded claim still matches (int compare)",
      C.title_collector_contradiction(
          "Pokemon Latias GX #0113/181 PSA 10", "113"), False)

check("no collector claim in title passes (exact page vouches)",
      C.title_collector_contradiction(
          "Latias & Latios GX Team Up PSA 10 GEM MINT", "113"), False)

check("multiple claims with one match passes",
      C.title_collector_contradiction(
          "Mew 151 #151/165 alt #196/165 PSA 10", "196"), False)

check("catalog fraction form also parses (113/181)",
      C.title_collector_contradiction(POISON_TITLE, "113/181"), True)

check("non-numeric collector (op02-013) is out of scope",
      C.title_collector_contradiction(POISON_TITLE, "op02-013"), False)

check("empty title never fires",
      C.title_collector_contradiction("", "113"), False)

# 真 DB 回掃執出嘅誤中形狀（regression，全部要 pass）：
check("JP pair + bare EN number both printed passes (v634 shape)",
      C.title_collector_contradiction(
          "MEWTWO VSTAR 2022 POKEMON GO ULTRA RARE FULL ART #091/071 #086", "86"), False)

check("bare #NNN alone passes (v1761 shape)",
      C.title_collector_contradiction(
          "2025 Pokemon Phantasmal Flames EN #125 Mega Charizard X ex SIR PSA 10 186/190",
          "125/094"), False)

check("letter-suffix promo pair passes (v2194 shape)",
      C.title_collector_contradiction(
          "Pikachu Berkemeja Batik 101/SV-P Indonesia Journey Promo Pokemon Card PSA 10", "101"), False)

check("JP-numbered sale on EN product still fires (v1909 shape)",
      C.title_collector_contradiction(
          "PSA 10 Team Rocket's Mewtwo ex SAR 237/193 M2a MEGA Dream ex Pokemon Card", "281"), True)

check("zero-padded catalog side parses (edge)",
      C.title_collector_contradiction(POISON_TITLE, "0113"), True)

check("year tag #2024 is not a claim (v2228 shape)",
      C.title_collector_contradiction(
          "2024 Pokemon Pikachu World Championships 190 ENGLISH PSA 10 #2024", "190"), False)

check("separator slash is not a claim (v433 shape)",
      C.title_collector_contradiction(
          "PSA 10 / Lost Origins  / Giratina Vstar 201  - English Pokemon TCG", "201"), False)

check("spaced slash around grade is not a pair",
      C.title_collector_contradiction("Charizard PSA 10 / mint 2020", "4"), False)

check("PSA 10/POP 3 is not a claim (v852 shape)",
      C.title_collector_contradiction(
          "2021 Pokemon SWSH Alt Leafeon Vmax Evolving Skies-Secret PSA 10/POP 3", "205"), False)

check("promo denominator still counts (101/SV-P)",
      C.title_collector_contradiction("Pikachu Batik 101/SV-P Promo PSA 10", "150"), True)

check("bare wanted number absolves (v754 shape)",
      C.title_collector_contradiction(
          "Jasmine's Gaze 245 Surging Sparks Special Illustration Rare PSA 10 Pokemon 231/182",
          "245"), False)

check("pair denominator does not absolve",
      C.title_collector_contradiction("Charizard Card 231/182 PSA 10", "182"), True)

# ── 2. verify_sale 接線：gated row 遇到毒 listing 一定要拒 ──────────
GATED_ROW = {
    "variant_id": 1326,
    "pc_product_id": 999001,
    "collector_number": "113",
    "set_name": "Pokemon Team Up",
    "card_name": "Latias & Latios GX",
    "pc_url": "https://www.pricecharting.com/game/pokemon-team-up/latias-latios-gx-113",
    "_pc_exact_product_gate": True,
}


def sale(title: str) -> dict:
    return {"title": title, "ebay_itm": "123456789012",
            "ebay_url": "https://www.ebay.com/itm/123456789012",
            "price_usd": 91.0, "date": "2026-07-14"}


stats = Counter()
check("gated verify_sale rejects the poison listing",
      C.verify_sale(GATED_ROW, sale(POISON_TITLE), stats), None)
check("rejection lands in its own stat bucket",
      stats["reject_collector_contradiction"], 1)

ok_sale = C.verify_sale(GATED_ROW, sale("Latias & Latios GX Team Up 113/181 PSA 10 GEM MINT"), stats)
check("gated verify_sale keeps the legit listing", ok_sale is not None, True)
check("legit listing unit price survives", float(ok_sale["unit_price_usd"]) if ok_sale else None, 91.0)

no_claim = C.verify_sale(GATED_ROW, sale("Latias Latios GX PSA 10 GEM MINT"), stats)
check("gated verify_sale keeps no-claim listing (gate 原意)", no_claim is not None, True)

# ── 3. ungated 路徑唔准鬆：舊 presence 檢查照舊 ────────────────────
ungated = dict(GATED_ROW)
ungated.pop("_pc_exact_product_gate")
stats2 = Counter()
check("ungated verify_sale still rejects poison (presence check)",
      C.verify_sale(ungated, sale(POISON_TITLE), stats2), None)
check("ungated rejection uses the legacy bucket", stats2["reject_collector"], 1)

print("\n")
if FAILED:
    print(f"FAILED {len(FAILED)}: {FAILED}")
    raise SystemExit(1)
print("all c11 title contracts hold")
