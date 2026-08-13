#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Operator knowledge adjudication 2026-08-13.

DADDY：有把握就直接決定。PC 產品 SAMPLE 圖經常張冠李戴，EN 卡信 heading；
SNK 圖係真卡（v906 Celebrations 25th 印已目視）。形狀 21：GemRate 寫包裝套，
卡面編號係原套 —— TR/SP 照面編號綁。

紅名單 13 張以前唔識；而家識就放。放咗嘅寫入
data/editorial/red-sheet-036-release.json，validator 036 只閘仲未放嗰啲。

用法：
  python -X utf8 pipelines/adjudicate_operator_knowledge_20260813.py --dry-run
  python -X utf8 pipelines/adjudicate_operator_knowledge_20260813.py --write
  # freeze 期間 cardz 只剩 SELECT，要用 rebuild.env：
  python -X utf8 pipelines/adjudicate_operator_knowledge_20260813.py --write --credentials-env data/runtime/config/rebuild.env
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))
sys.path.insert(0, str(ROOT / "scripts"))

import snk_identity_discover as D  # noqa: E402
from adjudicate_backlog_corroboration_20260813 import (  # noqa: E402
    _load_cards,
    _load_local_payload,
    _promote_one as snk_promote,
)
from adjudicate_pc_pop_corroboration_20260813 import (  # noqa: E402
    _find_html,
    _promote_one as pc_promote,
    pop_close,
    psa10_pop,
    psa10_price_usd,
)
from rebuild_036 import (  # noqa: E402
    DAILY_CREDENTIALS_ENV,
    EVIDENCE_TYPE_PROVIDER_PAGE,
    REJECTION_RED_LIST_KEY,
    _pc_page_identity,
    canonical_json,
    connect,
    sha256_bytes,
    sha256_file,
)
from leftover5_go import (  # noqa: E402
    LEFTOVER5_PC_GO as LEFTOVER5_GO,
    LEFTOVER5_VIDS,
)
from stamp_red_sheet_quarantine import red_variant_ids  # noqa: E402
from tcgplayer_images import search_products  # noqa: E402

RECEIPT = ROOT / "data" / "runtime" / "operator" / "audit" / "operator_knowledge_20260813.json"
RELEASE = ROOT / "data" / "editorial" / "red-sheet-036-release.json"
TCG_DIR = ROOT / "data" / "runtime" / "operator" / "audit" / "tcgplayer_leftover5"

# heading 必須同時含呢啲 token（casefold），唔對就唔綁。
PC = [
    {"vid": 2026, "pid": "10032135", "need": ["shanks", "magazine", "op09-001"],
     "why": "PSA Magazine Exclusive Shanks = PC Shanks [Magazine] OP09-001"},
    {"vid": 1917, "pid": "8877677", "need": ["o-nami", "illustration box", "op05-062"],
     "why": "Illustration Box Vol.1 O-Nami is OP05-062 ibox parallel"},
    {"vid": 2177, "pid": "8870955", "need": ["black maria", "illustration box", "op08-074"],
     "why": "Illustration Box Vol.2 Black Maria is OP08-074 ibox parallel"},
    {"vid": 2135, "pid": "12842340", "need": ["zoro", "alternate art", "op15-113"],
     "why": "Illustration Box Vol.3 Zoro is the OP15-113 AA"},
    {"vid": 132, "pid": "6235786", "need": ["boa hancock", "[sp]", "op01-078"],
     "why": "shape 21: Hancock SP face OP01-078 packed in OP04"},
    {"vid": 1441, "pid": "6905635", "need": ["o-nami", "[sp]", "op06-101"],
     "why": "shape 21: O-Nami SP face OP06-101 packed in OP07"},
    {"vid": 1734, "pid": "6235920", "need": ["uta", "[sp]", "op02-120"],
     "why": "shape 21: Uta SP face OP02-120 packed in OP05"},
    {"vid": 2133, "pid": "8828334", "need": ["edward", "[sp]", "st15-002"],
     "why": "shape 21: Newgate SP face ST15-002 packed in OP10"},
    {"vid": 2032, "pid": "8828333", "need": ["uso-hachi", "[sp]", "st18-001"],
     "why": "shape 21: Uso-Hachi SP face ST18-001 packed in OP10"},
    {"vid": 2134, "pid": "7419025", "need": ["tashigi", "sp", "st06-006"],
     "why": "shape 21: Tashigi SP face ST06-006 packed in OP08"},
    {"vid": 2197, "pid": "6142504", "need": ["venusaur", "#3", "classic"],
     "why": "CLV Classic Venusaur deck #3"},
    {"vid": 2077, "pid": "6235184", "need": ["nami", "gift collection", "op01-016"],
     "why": "Gift Collection 2023 Nami, not the base OP01-016"},
    {"vid": 2096, "pid": "8091548", "need": ["shanks", "wanted", "op09-004"],
     "why": "Shanks Wanted Poster OP09-004"},
    {"vid": 1921, "pid": "8096464", "need": ["ace", "winner", "op07-053"],
     "why": "3 Brothers Pack Winner Ace OP07-053"},
    {"vid": 1192, "pid": "4277242", "need": ["radiant jirachi", "120", "silver tempest"],
     "why": "Radiant Jirachi SIT 120"},
    {"vid": 163, "pid": "11329915", "need": ["luffy", "3rd anniversary", "st21-014"],
     "why": "3rd Anniversary campaign Luffy ST21-014"},
    {"vid": 1254, "pid": "9052272", "need": ["luffy", "errata", "op01-003"],
     "why": "OP01-003 AA errata"},
    {"vid": 1993, "pid": "10854657", "need": ["sanji", "manga", "op06-119"],
     "why": "PRB02 Sanji manga OP06-119"},
    {"vid": 197, "pid": "10492805", "need": ["nami", "championship", "op09-050"],
     "why": "Championship 25-26 Nami OP09-050"},
    {"vid": 2180, "pid": "8321540", "need": ["umbreon", "master ball", "59"],
     "why": "Prismatic Evolutions Umbreon Master Ball 059"},
    {"vid": 1199, "pid": "6236062", "need": ["yamato", "[sp]", "op01-121"],
     "why": "shape 21: Yamato SP face OP01-121 packed in OP05"},
    {"vid": 1738, "pid": "11012121", "need": ["roger", "wanted", "op09-118"],
     "why": "Gol D. Roger Wanted OP09-118 packed in OP13"},
    {"vid": 1205, "pid": "6235921", "need": ["lucci", "[sp]", "op03-092"],
     "why": "Rob Lucci SP OP03-092, not the unbracketed base page"},
    {"vid": 1304, "pid": "6578590", "need": ["buggy", "[sp]", "op03-008"],
     "why": "Buggy SP OP03-008, not the Judge page"},
    {"vid": 1281, "pid": "6235785", "need": ["law", "[sp]", "op01-047"],
     "why": "shape 21: Law SP face OP01-047 packed in OP04"},
    {"vid": 1235, "pid": "6905632", "need": ["doflamingo", "[sp]", "op01-073"],
     "why": "shape 21: Doflamingo SP face OP01-073 packed in OP07"},
    {"vid": 1745, "pid": "7418883", "need": ["pudding", "sp", "op03-112"],
     "why": "Charlotte Pudding SP Foil OP03-112, not the unbracketed page"},
    {"vid": 1467, "pid": "11012115", "need": ["ace", "alternate art", "op13-119"],
     "why": "regular Ace AA OP13-119; Wanted twin v1464 stays unbound"},
    # red 13 (shape 21 / exact AA)
    {"vid": 1741, "pid": "11012125", "need": ["luffy", "[tr]", "op11-058"],
     "why": "red13 release: Luffy TR face OP11-058 packed in OP13"},
    {"vid": 1226, "pid": "9362322", "need": ["red hawk", "alternate art", "op11-114"],
     "why": "red13 release: Gum-Gum Fire-Fist AA OP11-114"},
    {"vid": 1733, "pid": "6235922", "need": ["kaido", "[sp]", "op04-044"],
     "why": "red13 release: Kaido SP face OP04-044 packed in OP05"},
    {"vid": 1717, "pid": "6235341", "need": ["boa hancock", "alternate art", "op01-078"],
     "why": "red13 release: EN Hancock AA OP01-078 (not the JP SP page)"},
    {"vid": 1214, "pid": "8828340", "need": ["ace", "[tr]", "op08-052"],
     "why": "red13 release: Ace TR face OP08-052 packed in OP10"},
    {"vid": 1236, "pid": "11634254", "need": ["nami", "alternate art", "op14-031"],
     "why": "red13 release: Nami AA OP14-031"},
    {"vid": 1216, "pid": "10266507", "need": ["sanji", "[tr]", "op10-063"],
     "why": "red13 release: Sanji TR face OP10-063 packed in OP12"},
    {"vid": 1274, "pid": "11011963", "need": ["ace", "[sp]", "eb02-028"],
     "why": "red13 release: Ace SP face EB02-028 packed in OP13"},
    {"vid": 1731, "pid": "6235410", "need": ["nami", "alternate art", "op02-036"],
     "why": "red13 release: Nami AA OP02-036 Paramount War"},
    {"vid": 1722, "pid": "8828332", "need": ["sanji", "[sp]", "st14-003"],
     "why": "red13 release: Sanji SP face ST14-003 packed in OP10"},
    {"vid": 2168, "pid": "7419023", "need": ["bonney", "sp", "st02-007"],
     "why": "shape 21: Bonney SP face ST02-007 packed in OP08"},
    {"vid": 1244, "pid": "8091534", "need": ["zoro", "sp", "op05-067"],
     "why": "shape 21: Zoro-Juurou SP Foil OP05-067 packed in OP09"},
    {"vid": 1302, "pid": "6490255", "need": ["rebecca", "[sp]", "op05-091"],
     "why": "shape 21: Rebecca SP OP05-091 packed in OP06 (not the AA page)"},
    {"vid": 1303, "pid": "6480431", "need": ["all sunday", "[sp]", "op04-064"],
     "why": "shape 21: Ms. All Sunday SP OP04-064 packed in OP06 (not the AA page)"},
    {"vid": 98, "pid": "6235389", "need": ["shanks", "manga", "op01-120"],
     "why": "Shanks manga OP01-120; page was wrongly sitting on the AA twin v1718"},
    {"vid": 90, "pid": "6881305", "need": ["law", "treasure rare", "st10-010"],
     "why": "shape 21: Law TR face ST10-010 packed in OP07"},
    # leftover repoints: DB 連住錯頁，本地已有正確 heading
    {"vid": 1424, "pid": "6380267", "need": ["luffy", "1st anniversary", "st01-012"],
     "why": "EN unsigned 1st Anniversary Luffy; page was sitting on Wanted twin v1197"},
    {"vid": 1445, "pid": "8091685", "need": ["luffy", "op09-119"],
     "why": "Luffy OP09-119 base (no Manga/AA/SP bracket); manga page was the wrong candidate"},
    {"vid": 1757, "pid": "11012112", "need": ["luffy", "wanted", "op13-118"],
     "why": "Luffy Wanted OP13-118; DB 連住 Red Bull PRB02"},
    {"vid": 652, "pid": "5809386", "need": ["charizard", "#6", "151"],
     "why": "EN 151 Charizard ex #6; DB 連住日文 Battle Master，EN 頁之前坐喺 JP v1039"},
    {"vid": 1813, "pid": "7096087", "need": ["nami", "1st anniversary", "op01-016"],
     "why": "EN 1st Anniversary Nami OP01-016; SNK 候選係中文版"},
    {"vid": 1426, "pid": "7419005", "need": ["nami", "alternate art", "op08-106"],
     "why": "red13 release: Nami AA OP08-106; DB 連住 SP Foil"},
    {"vid": 1448, "pid": "8091539", "need": ["nami", "sp", "op08-106"],
     "why": "Nami SP Foil OP08-106 packed in OP09; page was wrongly on the AA twin v1426"},
    {"vid": 36, "pid": "9277912", "need": ["luffy", "gold", "japanese", "op05-119"],
     "why": "JP 3rd Anniversary Gold Luffy = PC [SP Gold] Japanese OP05-119"},
    {"vid": 1945, "pid": "630459", "need": ["charmander", "#46", "base set"],
     "why": "unlimited Base Set Charmander #46 (GemRate 寫 Base 唔寫 1st)"},
    {"vid": 2173, "pid": "630457", "need": ["bulbasaur", "#44", "base set"],
     "why": "unlimited Base Set Bulbasaur #44"},
    {"vid": 2181, "pid": "630476", "need": ["squirtle", "#63", "base set"],
     "why": "unlimited Base Set Squirtle #63"},
    {"vid": 1965, "pid": "630437", "need": ["charmeleon", "#24", "base set"],
     "why": "unlimited Base Set Charmeleon #24"},
    {"vid": 2211, "pid": "643519", "need": ["dragonite", "#5", "promo"],
     "why": "1999 movie promo Dragonite is Wizards Promo #5"},
    {"vid": 2229, "pid": "643534", "need": ["mewtwo", "#3", "promo"],
     "why": "1999 movie promo Mewtwo is Wizards Promo #3"},
    {"vid": 122, "pid": "7108820", "need": ["greninja", "132"],
     "why": "Shrouded Fable SIR Greninja is PC Promo Greninja Ex #132; DB 冇連呢頁"},
    {"vid": 19, "pid": "6403320", "need": ["luffy", "signature", "st01-012"],
     "why": "EN Anniversary Signature Luffy; DB 連住 Japanese 頁"},
    {"vid": 1447, "pid": "8091538", "need": ["hancock", "sp", "op07-051"],
     "why": "Hancock SP Foil OP07-051 packed in OP09; DB 連住 base SR / 中國周年"},
    {"vid": 1464, "pid": "11012116", "need": ["ace", "wanted", "op13-119"],
     "why": "red13 release: EN Ace Wanted OP13-119; DB 連住無 Wanted 嘅 base"},
    {"vid": 1898, "pid": "8091654", "need": ["teach", "wanted", "op09-093"],
     "why": "Teach Wanted Poster OP09-093; DB 連住 Manga 頁"},
    {"vid": 2223, "pid": "1656640", "need": ["pikachu", "#4", "promo"],
     "why": "1999 movie promo Pikachu is Wizards Promo #4; DB 連住 Topps sticker"},
    {"vid": 112, "pid": "8829097", "need": ["luffy", "silver", "japanese", "op05-119"],
     "why": "JP 3rd Anniversary Silver Luffy = PC [SP Silver] Japanese OP05-119"},
    {"vid": 1034, "pid": "5399884", "need": ["arcanine", "master", "59", "japanese"],
     "why": "JP 151 Arcanine Master Ball #59; DB 連住 EN base，SNK 係 Monster Ball"},
    {"vid": 1300, "pid": "6573101", "need": ["nami", "treasure", "st01-007"],
     "why": "red13 release: Nami Treasure Rare ST01-007; DB 連住 [Red] starter"},
    {"vid": 915, "pid": "2618192", "need": ["garchomp", "145", "celebration"],
     "why": "Celebrations Classic Garchomp C LV.X #145; DB 連住 Supreme Victors 原套"},
    {"vid": 998, "pid": "5399714", "need": ["pikachu", "25", "japanese", "reverse"],
     "why": "JP 151 Pikachu Reverse #25; DB 連住 EN base/reverse"},
    {"vid": 1201, "pid": "6235912", "need": ["kid", "manga", "op05-074"],
     "why": "EN Kid manga OP05-074; DB 連住 Japanese PRB01"},
    {"vid": 1446, "pid": "8091606", "need": ["buggy", "wanted", "op09-051"],
     "why": "Buggy Wanted Poster Foil OP09-051; DB 連住 Manga 頁"},
    # leftover-5 special cases (DADDY 2026-08-13, web pages 2026-08-13).
    # PC 英文 OP11 reprint 標題唔寫 [SP]/[TR]；產品身分睇 details 盒 TCGPlayer
    # ID + 成交標題，唔睇 SAMPLE 圖、唔睇 heading 有冇方括號。
    # v35: PC 7980813 係唯一 Chinese Promo #153/SV-P（HTML 有 5th/anniversary/
    # traditional，simplified=0）。_pc_page_identity 見唔到 console slug 有
    # japanese 就當 language=en；_language_from_set_name("Pokemon Chinese Promo")
    # 只回 "zh"。catalog 係 zhTW。唔改 parser（形狀 22：語言判斷有多份 copy）。
    # operator-knowledge 抄 catalog bound_*，strict view 只要求
    # providerClaims.cardLanguage IS NOT NULL，所以呢頁入得。
    {"vid": 35, "pid": "7980813", "need": ["pikachu", "153/sv-p", "chinese"],
     "why": "leftover-5: zhTW 5th Anniversary Pikachu 153/SV-P = PC Chinese Promo #153/SV-P; parser language=en vs catalog zhTW, do not change the shared language parser"},
    # v1225: EN Stussy SP. Live page is unbracketed Fist of Divine Speed
    # stussy-op07-085, PriceCharting ID 9362363, TCGPlayer ID 632506 = TCG
    # "Stussy (SP)". Sales titles are SP. Japanese [SP] 8843990 is the JP twin
    # console — v1875 keeps SNK 520534, do not steal it.
    {"vid": 1225, "pid": "9362363", "need": ["stussy", "op07-085"],
     "why": "leftover-5: EN Stussy SP OP07-085 = PC 9362363 (unbracketed heading; TCGPlayer 632506 Stussy (SP); sales are SP). JP [SP] 8843990 is the other console"},
    # v1438: DADDY 畀嘅頁。Heading 無 [SP]；TCGPlayer 632502 = Shirahoshi (SP)；
    # 成交標題 SP / (SP)。唔綁 Memorial AA / OP11-022 AA / JP 9362267。
    {"vid": 1438, "pid": "9362360", "need": ["shirahoshi", "eb01-057"],
     "why": "leftover-5: EN Shirahoshi SP EB01-057 = PC 9362360 (unbracketed heading; TCGPlayer 632502 Shirahoshi (SP); sales are SP). JP twin v2167 keeps SNK 520532"},
    # v1228: DADDY 畀嘅頁。TCGPlayer 632773 = Lucky.Roux (TR)。[Foil] 8091560
    # 係原套 OP09 foil，另一張。
    {"vid": 1228, "pid": "9967271", "need": ["lucky.roux", "op09-015"],
     "why": "leftover-5: EN Lucky Roux TR OP09-015 = PC 9967271 (unbracketed heading; TCGPlayer 632773 Lucky.Roux (TR); sales are Treasure Rare / TR). Do not bind [Foil] 8091560"},
    # v1900: PSA/eBay POP ~3079 ~= GemRate 3104 = unlimited Yellow Cheeks。
    # 1st Edition Yellow Cheeks 係 549、另一張卡。PC 630471 census=5 係欄位壞，
    # 成交標題係 UNLIMITED YELLOW CHEEKS。SNK 766125 名對、SKU 誤標 1st。
    {"vid": 1900, "pid": "630471", "need": ["pikachu", "#58"],
     "why": "leftover-5: unlimited Yellow Cheeks (GemRate 3104 ~= PSA ~3079). PC 630471 sales are that version; census 5 is not a different card. Not 1st (pop 549)"},
]

SNK = [
    {"vid": 906, "ext": "489509", "need": ["umbreon", "17/17", "celebration"],
     "why": "SNK image is Celebrations Classic Gold Star 17/17 with 25th stamp; PC 762776 is POP Series 5"},
    {"vid": 1597, "ext": "115267", "need": ["charizard", "cll 003"],
     "why": "unique CLL 003/032 Classic Charizard"},
    {"vid": 1598, "ext": "115274", "need": ["pikachu", "cll 008"],
     "why": "unique CLL 008/032 Classic Pikachu"},
    {"vid": 96, "ext": "91467", "need": ["charizard", "cp6 011", "1ed"],
     "why": "CP6 011/087 1ED Charizard; -Holo in PSA name is the holo rare"},
    {"vid": 382, "ext": "106322", "need": ["mewtwo", "cp6 049", "1ed"],
     "why": "CP6 049/087 1ED Mewtwo"},
    {"vid": 388, "ext": "386919", "need": ["pikachu", "cp6 033", "1ed"],
     "why": "CP6 033/087 1ED Pikachu"},
    {"vid": 149, "ext": "91462", "need": ["charizard", "cp6 090", "1ed"],
     "why": "CP6 090/087 1ED Charizard EX SR"},
    {"vid": 67, "ext": "100092", "need": ["rowlet", "290", "munch"],
     "why": "SM-P 290 Rowlet Munch exhibition promo"},
    {"vid": 44, "ext": "91489", "need": ["pikachu", "cp3 010", "1ed"],
     "why": "Pokekyun Pikachu CP3 010/032 1ED"},
    {"vid": 109, "ext": "91492", "need": ["flareon", "cp3 007", "1ed"],
     "why": "Pokekyun Flareon EX CP3 007/032 1ED"},
    {"vid": 349, "ext": "91493", "need": ["gardevoir", "cp3 019", "1ed"],
     "why": "Pokekyun Gardevoir EX CP3 019/032 1ED"},
    {"vid": 162, "ext": "91475", "need": ["mew", "cp5 017", "1ed"],
     "why": "Dream Shine Mew CP5 017/036 1ED"},
    {"vid": 154, "ext": "91526", "need": ["kyogre", "cp1 006", "1ed"],
     "why": "Double Crisis Team Aqua Kyogre EX CP1 006/034 1ED"},
    {"vid": 184, "ext": "91515", "need": ["rayquaza", "xy7 095", "1ed"],
     "why": "Bandit Ring M Rayquaza EX UR XY7 095/081 1ED"},
    {"vid": 1760, "ext": "349470", "need": ["roger", "op09-118"],
     "why": "Gol D. Roger SEC OP09-118 base (not the Wanted twin)"},
    {"vid": 1229, "ext": "520535", "need": ["rayleigh", "op09-005", "spc"],
     "why": "Rayleigh R-SPC OP09-005; PC page has no SP bracket"},
    {"vid": 1249, "ext": "349477", "need": ["dragon", "op07-015", "spc"],
     "why": "Monkey D. Dragon SR-SPC OP07-015"},
    {"vid": 354, "ext": "450656", "need": ["pikachu", "003/009"],
     "why": "11th movie commemoration Pikachu DP 003/009"},
    {"vid": 45, "ext": "91461", "need": ["pikachu", "cp6 094", "1ed"],
     "why": "CP6 094/087 1ED Pikachu EX SR full art"},
    {"vid": 159, "ext": "91525", "need": ["groudon", "cp1 015", "1ed"],
     "why": "CP1 015/034 1ED Team Magma Groudon EX"},
    {"vid": 1244, "ext": "349481", "need": ["zoro", "op05-067", "spc"],
     "why": "Zoro-Juurou R-SPC OP05-067 matches the SP parallel"},
    {"vid": 1302, "ext": "158400", "need": ["rebecca", "op05-091", "spc"],
     "why": "Rebecca SR-SPC OP05-091 matches the SP parallel"},
    {"vid": 1303, "ext": "160727", "need": ["sunday", "op04-064", "spc"],
     "why": "Ms. All-Sunday SR-SPC OP04-064 matches the SP parallel"},
    # leftover-5: Yellow Cheeks. PC 630471 live sales are UNLIMITED YELLOW
    # CHEEKS PSA 10 (~$530). SNK 766125 名有 Yellow Cheeks + 58/102 + [EN]；
    # SKU 寫「Base Set 1st Edition」但 PSA/catalog 係 unlimited。兩條都留：
    # EN 路由優先 PC。唔綁 shadowless/1st/E3/1999-2000 頁。
    {"vid": 1900, "ext": "766125", "need": ["pikachu", "yellow", "58"],
     "why": "leftover-5: SNK 766125 name is Yellow Cheeks; SKU 1st is storefront lie. Version is unlimited (GemRate 3104 ~= PSA ~3079). Fallback if PC 630471 has no current price"},
]

# leftover-5: TCGPlayer 仍然係 SP/TR 產品編號嘅旁證。PSA10 價而家走 PC
# 9362360 / 9967271（unbracketed heading + TCGPlayer ID + 成交標題）。
TCG = [
    {"vid": 1438, "ext": "632502", "query": "Shirahoshi SP",
     "need": ["shirahoshi", "(sp)", "eb01-057"],
     "why": "leftover-5 corroboration: TCGPlayer 632502 Shirahoshi (SP) is the same product PC wired onto 9362360"},
    {"vid": 1228, "ext": "632773", "query": "Lucky Roux",
     "need": ["lucky.roux", "(tr)", "op09-015"],
     "why": "leftover-5 corroboration: TCGPlayer 632773 Lucky.Roux (TR) is the same product PC wired onto 9967271"},
]


def _has_tokens(blob: str, need: list[str]) -> bool:
    text = blob.casefold()
    return all(token.casefold() in text for token in need)


def _load_variant_row(cur, vid: int) -> dict[str, Any] | None:
    """Catalog row for a leftover-5 card that already left the identity backlog."""
    cur.execute(
        """
        SELECT v.id AS variant_id,
               COALESCE(psa.psa_description,
                 JSON_UNQUOTE(JSON_EXTRACT(rm.detail_json,'$.fingerprint.description'))) AS psa_name,
               rm.latest_psa10_population AS pop,
               v.tcg_code, v.card_language, v.set_name, v.collector_number,
               v.canonical_name, v.set_code AS v_set_code, v.printing_code AS v_printing_code,
               p.parallel_code, p.printing_code, p.canonical_printing_sha256,
               p.tcg_code AS p_tcg_code, p.card_language AS p_card_language,
               p.set_code AS p_set_code, p.collector_number AS p_collector_number,
               p.edition_code AS p_edition_code, p.finish_code AS p_finish_code,
               JSON_UNQUOTE(JSON_EXTRACT(rm.detail_json,'$.fingerprint.name')) AS fp_name,
               JSON_UNQUOTE(JSON_EXTRACT(rm.detail_json,'$.fingerprint.parallel')) AS fp_parallel,
               JSON_UNQUOTE(JSON_EXTRACT(rm.detail_json,'$.fingerprint.cardNumber')) AS fp_number
        FROM catalog_variant v
        LEFT JOIN catalog_printing_identity p ON p.variant_id=v.id
        LEFT JOIN catalog_rebuild_member rm ON rm.variant_id=v.id
          AND rm.generation_id=(SELECT generation_id FROM catalog_rebuild_member ORDER BY computed_at DESC LIMIT 1)
        LEFT JOIN catalog_psa_identity_acceptance psa ON psa.variant_id=v.id
          AND NOT EXISTS (SELECT 1 FROM catalog_psa_identity_acceptance n WHERE n.supersedes_acceptance_id=psa.id)
        WHERE v.id=%s
        """,
        (vid,),
    )
    row = cur.fetchone()
    return dict(row) if row else None


def _card_row(cards: dict[int, dict[str, Any]], cur, vid: int) -> dict[str, Any] | None:
    row = cards.get(vid)
    if row is not None:
        return row
    if vid in LEFTOVER5_VIDS:
        return _load_variant_row(cur, vid)
    return None


def _demote_other_exact_pc(cur, vid: int, keep_pid: str, why: str) -> int:
    """One variant, one exact PC product. Stussy 8843990 was the JP console."""
    cur.execute(
        """UPDATE catalog_source_identity
              SET match_status='rejected',
                  bind_evidence_json=JSON_SET(
                    COALESCE(bind_evidence_json, JSON_OBJECT()),
                    '$.adjudication.demoteReason', %s,
                    '$.adjudication.demotedAt', %s,
                    '$.adjudication.demotedBy', 'adjudicate_operator_knowledge_20260813')
            WHERE variant_id=%s AND source_code='pricecharting'
              AND match_status='exact' AND external_entity_id<>%s""",
        (
            why,
            datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            vid,
            str(keep_pid),
        ),
    )
    return int(cur.rowcount)


def _tcg_hit(spec: dict[str, Any]) -> dict[str, Any] | None:
    want = int(spec["ext"])
    try:
        hits = search_products(spec["query"], size=16, product_line="One Piece Card Game")
    except Exception:
        return None
    for hit in hits:
        if int(hit.get("productId") or 0) == want:
            return hit
    return None


def _leftover5_pc_pop_verdict(vid: int, pid: str, gemrate_pop: int) -> tuple[str, str]:
    """POP-first: mismatch = rejected; missing census = not proven.

    Unique catalog cards may exact-bind without PC VGPC.pop_data when PSA/eBay
    pop identifies the version (DADDY 2026-08-13).
    """
    if (vid, str(pid)) in LEFTOVER5_GO:
        return "exact", LEFTOVER5_GO[(vid, str(pid))]
    path = _find_html(vid, pid)
    if path is None:
        return "manual_review", "pc_pop_missing:no_html"
    html = path.read_text(encoding="utf-8", errors="replace")
    pc_pop = psa10_pop(html)
    if pc_pop is None:
        return "manual_review", f"pc_pop_missing:gemrate={gemrate_pop}"
    if not pop_close(pc_pop, gemrate_pop):
        return "rejected", f"pop_mismatch:pc={pc_pop},gemrate={gemrate_pop}"
    return "exact", f"pop_close:pc={pc_pop},gemrate={gemrate_pop}"


def _reconcile_leftover5_pc_pop(cur) -> list[dict[str, Any]]:
    demoted: list[dict[str, Any]] = []
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    for vid in sorted(LEFTOVER5_VIDS):
        row = _load_variant_row(cur, vid)
        gemrate_pop = int((row or {}).get("pop") or 0)
        cur.execute(
            """SELECT external_entity_id, match_status FROM catalog_source_identity
                WHERE variant_id=%s AND source_code='pricecharting'""",
            (vid,),
        )
        for bind in list(cur.fetchall()):
            pid = str(bind["external_entity_id"])
            verdict, why = _leftover5_pc_pop_verdict(vid, pid, gemrate_pop)
            current = str(bind["match_status"] or "")
            if verdict == "exact" or current == verdict:
                continue
            if current != "exact":
                continue
            cur.execute(
                """UPDATE catalog_source_identity
                      SET match_status=%s,
                          bind_evidence_json=JSON_SET(
                            COALESCE(bind_evidence_json, JSON_OBJECT()),
                            '$.adjudication.demoteReason', %s,
                            '$.adjudication.demotedAt', %s,
                            '$.adjudication.demotedBy', 'leftover5_pop_first_20260813')
                    WHERE variant_id=%s AND source_code='pricecharting'
                      AND external_entity_id=%s AND match_status='exact'""",
                (verdict, why, now, vid, pid),
            )
            demoted.append({"vid": vid, "pid": pid, "from": current, "to": verdict, "why": why})
    return demoted


def tcg_promote(cur, vid: int, row: dict[str, Any], decision: dict[str, Any]) -> dict[str, Any]:
    """Promote an already-exact TCGPlayer product into the strict identity view.

    TCGPlayer is not a PSA10 price lane. This only records the correct EN
    product page so leftover identity is not stuck on gemrate-only.
    """
    ext = str(decision["ext"])
    path: Path = decision["path"]
    digest = sha256_file(path)
    # data/runtime is a junction into the sibling checkout; resolve() leaves
    # this repo, so do not Path.relative_to(ROOT). The capture path is the
    # logical path inside this project.
    rel = f"data/runtime/operator/audit/tcgplayer_leftover5/{path.name}"
    captured_at = datetime.fromtimestamp(
        path.stat().st_mtime, tz=timezone.utc
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    ident = D.bound_ident(row)
    hit = decision["hit"]
    evidence = {
        "providerClaims": {
            "tcgCode": ident["tcg"],
            "cardLanguage": ident["lang"] or "en",
            "collectorNumber": ident["collector"],
            "setCode": ident["set_code"],
            "printingCode": ident["printing"],
            "parallelCode": ident["parallel"],
        },
        "evidence": {
            "type": EVIDENCE_TYPE_PROVIDER_PAGE,
            "sha256": digest,
            "path": rel,
            "canonicalUrl": f"https://www.tcgplayer.com/product/{ext}",
            "pageHeading": f"{hit.get('productName')} {hit.get('setName')} {hit.get('number')}",
            "capturedAt": captured_at,
            "generation": "operator_knowledge_leftover5_20260813",
        },
        "adjudication": {
            "action": "promote-operator-knowledge-tcgplayer-identity",
            "adjudicatedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "adjudicatedBy": "adjudicate_operator_knowledge_20260813",
            "reason": decision["why"],
            "gemratePsaName": row.get("psa_name"),
            "notAPsa10PriceLane": True,
        },
    }
    evidence_sha = sha256_bytes(canonical_json(evidence))
    cur.execute(
        """INSERT INTO catalog_provider_capture_receipt
             (source_code, external_entity_id, capture_sha256, capture_path,
              captured_at, generation_id, parser_version)
           VALUES ('tcgplayer', %s, %s, %s, %s, %s, %s)
           ON DUPLICATE KEY UPDATE capture_sha256=VALUES(capture_sha256),
             capture_path=VALUES(capture_path), captured_at=VALUES(captured_at),
             generation_id=VALUES(generation_id), parser_version=VALUES(parser_version)""",
        (
            ext, digest, rel[:500],
            datetime.strptime(captured_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc),
            "operator_knowledge_leftover5_20260813", "tcgplayer_leftover5_v1",
        ),
    )
    cur.execute(
        """INSERT INTO catalog_source_identity
             (source_code, external_entity_id, variant_id, match_status, evidence_sha256,
              source_product_number, bind_evidence_json, bound_tcg_code, bound_card_language,
              bound_collector_number, bound_set_code, bound_printing_code, bound_parallel_code,
              bound_edition_code, bound_finish_code)
           VALUES ('tcgplayer', %s, %s, 'exact', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
           ON DUPLICATE KEY UPDATE variant_id=VALUES(variant_id), match_status='exact',
             evidence_sha256=VALUES(evidence_sha256),
             source_product_number=VALUES(source_product_number),
             bind_evidence_json=VALUES(bind_evidence_json),
             bound_tcg_code=VALUES(bound_tcg_code),
             bound_card_language=VALUES(bound_card_language),
             bound_collector_number=VALUES(bound_collector_number),
             bound_set_code=VALUES(bound_set_code),
             bound_printing_code=VALUES(bound_printing_code),
             bound_parallel_code=VALUES(bound_parallel_code),
             bound_edition_code=VALUES(bound_edition_code),
             bound_finish_code=VALUES(bound_finish_code)""",
        (
            ext, vid, evidence_sha, str(ident["collector"])[:96],
            json.dumps(evidence, ensure_ascii=False),
            ident["tcg"], ident["lang"], ident["collector"], ident["set_code"],
            ident["printing"], ident["parallel"], ident["edition"], ident["finish"],
        ),
    )
    cur.execute(
        """SELECT COUNT(*) n FROM operator_strict_source_identity
           WHERE source_code='tcgplayer' AND external_entity_id=%s AND variant_id=%s""",
        (ext, vid),
    )
    strict_n = int(cur.fetchone()["n"])
    if strict_n != 1:
        raise RuntimeError(f"v{vid} tcg:{ext} strict view rows={strict_n}")
    return {
        "variant_id": vid,
        "ext": ext,
        "why": decision["why"],
        "heading": evidence["evidence"]["pageHeading"],
        "strictRows": strict_n,
    }


def _write_release(released: list[int]) -> None:
    payload = {
        "contract": "red-sheet-036-release-v1",
        "releasedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "reason": (
            "034 red sheet was a human 'I don't know this card' hold. 2026-08-13 "
            "operator identified shape-21 TR/SP/AA (GemRate names the pack set; "
            "the face keeps the original number) and exact AA pages. Released "
            "ids are the red_variant_ids() members that this run bound exact."
        ),
        "releasedVariantIds": sorted(released),
        "stillRedVariantIds": sorted(set(red_variant_ids()) - set(released)),
    }
    RELEASE.parent.mkdir(parents=True, exist_ok=True)
    RELEASE.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--write", action="store_true")
    parser.add_argument(
        "--credentials-env", type=Path, default=DAILY_CREDENTIALS_ENV,
        help="default backend.env; pass rebuild.env while writer freeze is on",
    )
    args = parser.parse_args()

    red = set(red_variant_ids())
    conn = connect(args.credentials_env)
    cur = conn.cursor()
    cards = _load_cards(cur)

    pc_ready: list[tuple[int, dict[str, Any], dict[str, Any]]] = []
    snk_ready: list[tuple[int, dict[str, Any], dict[str, Any]]] = []
    tcg_ready: list[tuple[int, dict[str, Any], dict[str, Any]]] = []
    skipped: list[dict[str, Any]] = []

    for spec in PC:
        vid = int(spec["vid"])
        row = _card_row(cards, cur, vid)
        if row is None:
            skipped.append({"vid": vid, "pid": spec["pid"], "why": "not_in_backlog"})
            continue
        html_path = _find_html(vid, spec["pid"])
        if html_path is None:
            skipped.append({"vid": vid, "pid": spec["pid"], "why": "no_html"})
            continue
        html = html_path.read_text(encoding="utf-8", errors="replace")
        identity, why = _pc_page_identity(html)
        if identity is None:
            skipped.append({"vid": vid, "pid": spec["pid"], "why": f"identity:{why}"})
            continue
        heading = identity["heading"]
        if not _has_tokens(heading, spec["need"]):
            skipped.append({"vid": vid, "pid": spec["pid"], "why": f"heading_tokens:{heading}"})
            continue
        if vid in LEFTOVER5_VIDS and (vid, str(spec["pid"])) not in LEFTOVER5_GO:
            pc_pop = psa10_pop(html)
            gemrate_pop = int(row.get("pop") or 0)
            if pc_pop is None or not pop_close(pc_pop, gemrate_pop):
                skipped.append({
                    "vid": vid, "pid": spec["pid"],
                    "why": f"pop:{pc_pop} vs gemrate:{gemrate_pop}",
                })
                continue
        pc_ready.append((vid, row, {
            "ext": spec["pid"],
            "why": spec["why"],
            "identity": identity,
            "html_path": html_path,
            "pc_pop": psa10_pop(html),
            "gemrate_pop": row.get("pop"),
            "price_usd": psa10_price_usd(html, identity["canonicalUrl"]) or 0.0,
        }))

    for spec in SNK:
        vid = int(spec["vid"])
        row = _card_row(cards, cur, vid)
        if row is None:
            skipped.append({"vid": vid, "snk": spec["ext"], "why": "not_in_backlog"})
            continue
        payload = _load_local_payload(spec["ext"])
        if payload is None:
            skipped.append({"vid": vid, "snk": spec["ext"], "why": "no_snk_payload"})
            continue
        _ok, _why, facts = D.rule_candidate(row, payload)
        blob = " ".join((
            str(facts.get("masterName") or ""),
            str(facts.get("localized") or ""),
            str(facts.get("productNumber") or ""),
            str(facts.get("claim") or ""),
        ))
        if not _has_tokens(blob, spec["need"]):
            skipped.append({"vid": vid, "snk": spec["ext"], "why": f"name_tokens:{blob[:120]}"})
            continue
        if not facts:
            skipped.append({"vid": vid, "snk": spec["ext"], "why": "no_facts"})
            continue
        snk_ready.append((vid, row, {
            "ext": spec["ext"],
            "why": spec["why"],
            "facts": facts,
            "payload": payload,
        }))

    TCG_DIR.mkdir(parents=True, exist_ok=True)
    for spec in TCG:
        vid = int(spec["vid"])
        row = _card_row(cards, cur, vid)
        if row is None:
            skipped.append({"vid": vid, "tcg": spec["ext"], "why": "not_in_backlog"})
            continue
        hit = _tcg_hit(spec)
        if hit is None:
            skipped.append({"vid": vid, "tcg": spec["ext"], "why": "tcg_search_miss"})
            continue
        blob = " ".join((
            str(hit.get("productName") or ""),
            str(hit.get("setName") or ""),
            str(hit.get("number") or ""),
            str(hit.get("rarity") or ""),
        ))
        if not _has_tokens(blob, spec["need"]):
            skipped.append({"vid": vid, "tcg": spec["ext"], "why": f"name_tokens:{blob[:160]}"})
            continue
        path = TCG_DIR / f"{spec['ext']}.json"
        path.write_text(json.dumps(hit, ensure_ascii=False, indent=1), encoding="utf-8")
        tcg_ready.append((vid, row, {
            "ext": spec["ext"],
            "why": spec["why"],
            "hit": hit,
            "path": path,
        }))

    leftover5_pop: list[dict[str, Any]] = []
    for vid in sorted(LEFTOVER5_VIDS):
        row = _card_row(cards, cur, vid) or _load_variant_row(cur, vid)
        gemrate_pop = int((row or {}).get("pop") or 0)
        cur.execute(
            """SELECT external_entity_id, match_status FROM catalog_source_identity
                WHERE variant_id=%s AND source_code='pricecharting'""",
            (vid,),
        )
        binds = list(cur.fetchall())
        if not binds:
            leftover5_pop.append({"vid": vid, "gemrate_pop": gemrate_pop, "pc": None})
            continue
        for bind in binds:
            pid = str(bind["external_entity_id"])
            verdict, why = _leftover5_pc_pop_verdict(vid, pid, gemrate_pop)
            leftover5_pop.append({
                "vid": vid, "pid": pid, "gemrate_pop": gemrate_pop,
                "current": bind["match_status"], "verdict": verdict, "why": why,
            })

    plan = {
        "pc": [{"vid": v, "pid": d["ext"], "heading": d["identity"]["heading"], "why": d["why"],
                "red": v in red} for v, _, d in pc_ready],
        "snk": [{"vid": v, "item": d["ext"], "why": d["why"], "red": v in red}
                for v, _, d in snk_ready],
        "tcg": [{"vid": v, "ext": d["ext"], "heading": f"{d['hit'].get('productName')} {d['hit'].get('setName')} {d['hit'].get('number')}",
                 "why": d["why"], "red": v in red} for v, _, d in tcg_ready],
        "leftover5Pop": leftover5_pop,
        "skipped": skipped,
        "counts": {"pc": len(pc_ready), "snk": len(snk_ready), "tcg": len(tcg_ready),
                   "skipped": len(skipped)},
    }
    print(json.dumps({"counts": plan["counts"], "pc": plan["pc"], "snk": plan["snk"],
                      "tcg": plan["tcg"], "leftover5Pop": leftover5_pop,
                      "skippedLeftover5": [s for s in skipped if s.get("vid") in LEFTOVER5_VIDS]},
                     ensure_ascii=False, indent=1))
    if args.dry_run:
        RECEIPT.parent.mkdir(parents=True, exist_ok=True)
        RECEIPT.write_text(json.dumps(plan, ensure_ascii=False, indent=1), encoding="utf-8")
        print("dry-run receipt:", RECEIPT)
        conn.close()
        return 0

    written: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    released: list[int] = []

    for vid, row, decision in pc_ready:
        try:
            if vid in LEFTOVER5_VIDS:
                _demote_other_exact_pc(cur, vid, decision["ext"], decision["why"])
            cur.execute(
                "UPDATE catalog_source_identity SET variant_id=%s"
                " WHERE source_code='pricecharting' AND external_entity_id=%s",
                (vid, decision["ext"]),
            )
            written.append({"source": "pricecharting", **pc_promote(cur, vid, row, decision)})
            if vid in red:
                released.append(vid)
            conn.commit()
        except Exception as exc:
            conn.rollback()
            failed.append({"vid": vid, "pid": decision["ext"], "error": f"{type(exc).__name__}: {exc}"})

    for vid, row, decision in snk_ready:
        try:
            written.append({"source": "snkrdunk", **snk_promote(cur, vid, row, decision)})
            if vid in red:
                released.append(vid)
            conn.commit()
        except Exception as exc:
            conn.rollback()
            failed.append({"vid": vid, "snk": decision["ext"], "error": f"{type(exc).__name__}: {exc}"})

    for vid, row, decision in tcg_ready:
        try:
            written.append({"source": "tcgplayer", **tcg_promote(cur, vid, row, decision)})
            if vid in red:
                released.append(vid)
            conn.commit()
        except Exception as exc:
            conn.rollback()
            failed.append({"vid": vid, "tcg": decision["ext"], "error": f"{type(exc).__name__}: {exc}"})

    released = sorted(set(released))
    if released:
        _write_release(released)
        marks = ",".join(["%s"] * len(released))
        # Drop the redListed stamp on every non-gemrate row of a released card
        # so a later reverify does not treat the sibling as a human verdict.
        cur.execute(
            f"""UPDATE catalog_source_identity
                   SET bind_evidence_json=JSON_REMOVE(bind_evidence_json, '$.{REJECTION_RED_LIST_KEY}')
                 WHERE variant_id IN ({marks}) AND source_code<>'gemrate'
                   AND JSON_EXTRACT(bind_evidence_json, '$.{REJECTION_RED_LIST_KEY}') IS NOT NULL""",
            tuple(released),
        )
        conn.commit()

    leftover5_demoted = _reconcile_leftover5_pc_pop(cur)
    conn.commit()

    out = {**plan, "written": written, "failed": failed, "releasedRed": released,
           "leftover5Demoted": leftover5_demoted}
    RECEIPT.parent.mkdir(parents=True, exist_ok=True)
    RECEIPT.write_text(json.dumps(out, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print(json.dumps({"written": len(written), "failed": failed, "releasedRed": released,
                      "leftover5Demoted": leftover5_demoted},
                     ensure_ascii=False, indent=1))
    print("receipt:", RECEIPT)
    conn.close()
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
