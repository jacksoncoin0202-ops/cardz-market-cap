#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""一筆 PriceCharting PSA10 成交只可以有一條 fingerprint。

點解要有呢個 test：呢個 repo 有兩個 writer 各自砌 preimage —— 日更嘅
c11_pc_sold_ingest 同 replay 嘅 rebuild_036 —— 兩邊自出世以來就對「價格點寫」
唔同意（六位 vs 兩位小數）。同一筆 eBay 成交因此喺 market_sale_observation
入面有兩行，而 UNIQUE KEY (source_code, external_entity_id,
transaction_fingerprint) 睇唔到，因為兩條 sha 真係唔同。實測 2026-08-11：
65,845 行 pricecharting 對源頭 36,101 筆 distinct 成交 = 1.82 倍，個榜嘅
trackedSales.count 就係咁樣多咗八成出街。

下面每一組數都係真嘢：repro 嗰筆係 DB id 1624373 / 1718910（同一個 eBay item
id 800321739580，同一日同一個價，兩個 run 各插一行），30 行嗰版係 owner 圈住
嗰張 Pikachu with Grey Felt Hat 嘅本機 HTML。

Run: python -X utf8 scripts/test_pc_sale_identity.py
"""
from __future__ import annotations

import hashlib
import sys
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

from pc_sale_identity import pc_sale_fingerprint, pc_sale_price_text  # noqa: E402
from pricecharting_page_parse import parse_product_html  # noqa: E402

FAILED: list[str] = []
CHECKS = 0


def check(label: str, got, want) -> None:
    global CHECKS
    CHECKS += 1
    if got != want:
        FAILED.append(f"FAIL {label}\n  got  {got!r}\n  want {want!r}")


# --- 0. 兩個舊 builder，逐字抄返 ------------------------------------------
# 呢兩個 function 就係被刪走嗰兩段 code。留喺呢度唔係為咗用，係為咗證明佢哋
# 對同一筆成交出唔同 sha —— 邊個 writer 將來再自己砌一次，呢個形狀就要紅。


def legacy_c11(product_id, date_text, price, itm) -> str:
    unit = Decimal(str(price)).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)
    return hashlib.sha256(
        f"pc|{product_id}|psa|10|{date_text}|{unit}|{itm}".encode("utf-8")
    ).hexdigest()


def legacy_rebuild036(product_id, date_text, price, itm) -> str:
    unit = Decimal(str(price)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return hashlib.sha256(
        f"pc|{product_id}|psa|10|{date_text}|{unit}|{itm}".encode("utf-8")
    ).hexdigest()


# --- 1. repro：真係有兩行嗰筆成交 -----------------------------------------
# variant 473, product 10026801, 2026-07-12, $65.00, eBay item 800321739580.
# DB id 1624373 (run 7868) 同 1718910 (run 8146) 就係佢，一筆成交兩行。
SALE = ("10026801", "2026-07-12", 65.0, "800321739580")

check(
    "兩個舊 builder 對同一筆成交出唔同 sha（呢個就係 1.82 倍嘅機制）",
    legacy_c11(*SALE) != legacy_rebuild036(*SALE),
    True,
)
check(
    "DB id 1718910 嗰條 fingerprint",
    pc_sale_fingerprint(*SALE),
    "0334af5d51aadd6217d72574d37f3ecb252349cc6a1694cc8cc0527dded3d5af",
)
check(
    "DB id 1624373 嗰條（兩位小數）唔再會被砌出嚟",
    pc_sale_fingerprint(*SALE)
    != "dc0c17bab987c7f2185187248ba89e6e02530b1fda269dcffde29b06793de477",
    True,
)
check("canonical = 舊 c11 六位嗰個 → 已入庫同 manifest 嘅 sha 全部仍然啱",
      pc_sale_fingerprint(*SALE), legacy_c11(*SALE))

# --- 2. 價格文字：跟 decimal(18,6)，唔跟 caller ---------------------------
check("六位小數", pc_sale_price_text(65), "65.000000")
check("float 入嚟一樣", pc_sale_price_text(2801.06), "2801.060000")
check("Decimal 入嚟一樣", pc_sale_price_text(Decimal("2801.06")), "2801.060000")
check("字串入嚟一樣", pc_sale_price_text("2801.06"), "2801.060000")

# --- 3. input 型別唔准改變身份 --------------------------------------------
# 兩個 call site 一個攞 int（map 行）一個攞 str（parsed page key），價格一個係
# Decimal 一個係 float。四種組合必須出同一條 sha。
BASE = pc_sale_fingerprint(*SALE)
check("product id 用 int", pc_sale_fingerprint(10026801, "2026-07-12", 65.0, "800321739580"), BASE)
check("價用 Decimal", pc_sale_fingerprint("10026801", "2026-07-12", Decimal("65"), "800321739580"), BASE)
check("價用字串", pc_sale_fingerprint("10026801", "2026-07-12", "65.00", "800321739580"), BASE)
check("item id 有空格", pc_sale_fingerprint("10026801", "2026-07-12", 65.0, " 800321739580 "), BASE)

# --- 4. 對住 owner 圈住嗰張卡嘅真 HTML 行一次 -----------------------------
PAGE = ROOT / "data/private/pricecharting_session/html/full900/1_pikachu-with-grey-felt-hat-85_r.html"
if PAGE.is_file():
    parsed = parse_product_html(PAGE.read_text(encoding="utf-8", errors="replace"))
    rows = ((parsed.get("psa10") or {}).get("completed_sales") or {}).get("rows") or []
    product_id = (parsed.get("product") or {}).get("id")
    check("PC 一版 PSA 10 只出 30 行（呢個係佢個上限，唔係總成交）", len(rows), 30)
    check("product id", product_id, 5834844)
    fps = [
        pc_sale_fingerprint(product_id, row["date"], row["price_usd"], row["ebay_itm"])
        for row in rows
    ]
    check("30 行 30 條 fingerprint，冇撞", len(set(fps)), 30)
    reparsed = parse_product_html(PAGE.read_text(encoding="utf-8", errors="replace"))
    check("同一版 parse 兩次出同一批 sha", fps, [
        pc_sale_fingerprint(product_id, row["date"], row["price_usd"], row["ebay_itm"])
        for row in ((reparsed.get("psa10") or {}).get("completed_sales") or {}).get("rows") or []
    ])
    check(
        "最新嗰筆（2026-08-09 $2751.00 item 287495389764）",
        fps[0],
        "130be8a2cc0c8ad78a76cbc9d564bd53b126166b100bc15bb0e9dc885f4f49ba",
    )
    check(
        "逐筆 eBay 證據齊：日期 + link + 價",
        all(
            row["date"] and row["ebay_url"].startswith("https://www.ebay.com/itm/")
            and row["price_usd"] > 0
            for row in rows
        ),
        True,
    )
    check(
        "舊 rebuild_036 format 對呢 30 行全部唔同 sha（即係全部會變雙行）",
        sum(
            1
            for row in rows
            if legacy_rebuild036(product_id, row["date"], row["price_usd"], row["ebay_itm"])
            == pc_sale_fingerprint(product_id, row["date"], row["price_usd"], row["ebay_itm"])
        ),
        0,
    )
else:
    FAILED.append(f"FAIL 揾唔到本機 PC 頁：{PAGE}")
    CHECKS += 1

# --- 5. 兩個 writer 唔准再自己砌 ------------------------------------------
# 「有 function 但零 call site」等於冇 function，所以呢度直接掃兩個 writer 嘅
# source：要見到 pc_sale_fingerprint，而且唔准再出現自己嗰條 pc| preimage。
for relative in ("pipelines/c11_pc_sold_ingest.py", "pipelines/rebuild_036.py"):
    source = (ROOT / relative).read_text(encoding="utf-8")
    check(f"{relative} 經 pc_sale_fingerprint", "pc_sale_fingerprint(" in source, True)
    # 條 preimage 只可以喺 pc_sale_identity.py 出現。連 comment 都唔准抄，
    # 因為抄得到就 copy-paste 得返落 code。
    check(f"{relative} 冇自己砌 pc| preimage", "pc|" in source, False)

for line in FAILED:
    print(line)
print(f"{CHECKS - len(FAILED)}/{CHECKS} checks passed")
raise SystemExit(1 if FAILED else 0)
