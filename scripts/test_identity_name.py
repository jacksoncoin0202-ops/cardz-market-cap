#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""identity_name 對住真數據驗：真 GemRate payload 同出街 snapshot。

規則本身（純字串、restate 兩個 mode、writer 經唯一組裝點）喺
test_identity_rules.py —— 呢個檔要 machine-private fixture，clean checkout
（pre-push release gate）成個 SKIP，所以唔使 data 嘅 check 唔可以放喺度。
名同編號由 data/public/seed-snapshot.json（出街嗰份）攞，分母由真 GemRate
payload 攞。邏輯再被人刪一次，呢個 test 就會紅。

Run: python -X utf8 scripts/test_identity_name.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

import identity_name as N
from psa_identity_repair import load_psa_raw

FAILED: list[str] = []
CHECKS = 0


def check(label: str, got, want) -> None:
    global CHECKS
    CHECKS += 1
    if got != want:
        FAILED.append(f"FAIL {label}\n  got  {got!r}\n  want {want!r}")


def truthy(label: str, value) -> None:
    check(label, bool(value), True)


# --- 1. 對住真 GemRate payload 行一次 --------------------------------------
# 固定同一份真實 v5 capture：PSA 行印裸 110，CGC 行印 110/080。
# 最新 receipt 只描述最新採集；DOM-only 採集合法地冇 sourcePointer，
# 唔可以用佢取代回歸測試已選定嘅原始 bytes。沿用 canonical pinned resolver，
# 原檔遺失、hash 不符或身份欄位錯誤仍然要紅。
GEMRATE_ID = "80a8b349acb400cc08fe55617c3ff152256d371d"
RAW_SHA = "328da8488056c401a59478d8717e3232fa947f1aef13ac711fd76957ef3abadb"
resolved = load_psa_raw(GEMRATE_ID, pinned_sha=RAW_SHA)
check("真 payload 固定原始 hash", resolved.get("rawPayloadSha256"), RAW_SHA)
if not resolved.get("error") and resolved.get("rawPayloadSha256") == RAW_SHA:
    raw = json.loads((ROOT / resolved["rawPath"]).read_text(encoding="utf-8-sig"))
    psa_rows = [r for r in raw.get("population_data") or [] if str(r.get("grader")).casefold() == "psa"]
    truthy("真 payload 得一行 PSA", len(psa_rows) == 1)
    check("真 payload 個 PSA 行本身就係裸號", psa_rows[0].get("card_number"), "110")
    check(
        "真 payload 補得返分母",
        N.complete_collector_number(psa_rows[0].get("card_number"), raw.get("population_data")),
        "110/080",
    )
    check(
        "真 payload 個 PSA description 補得返條尾",
        N.complete_collector_tail(psa_rows[0].get("description"), "110/80"),
        "2025 Pokemon Japanese M2-Inferno X Mega Charizard X EX Special Art Rare 110/80",
    )
else:
    FAILED.append(f"FAIL 真 GemRate payload 驗證失敗：{resolved}")
    CHECKS += 1

# --- 2. 出街嗰份 snapshot 全量掃：唔准出雙號 -------------------------------
# 舊版最大罪狀係靜靜出雙號。呢度攞出街全 universe 逐張行一次，任何一張個結果
# 唔係以完整編號收尾、或者同一個 core 出現兩次，都當紅。
SNAPSHOT = ROOT / "data/public/seed-snapshot.json"
if SNAPSHOT.is_file():
    snapshot = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    cards = [*snapshot["top100"], *snapshot["watchlist"]]
    dupes: list[str] = []
    unfinished: list[str] = []
    not_a_number_touched: list[str] = []
    for card in cards:
        name = str(card.get("officialName") or "")
        num = str((card.get("collectorNumber") or {}).get("display") or "")
        if not name or not num:
            continue
        out = N.complete_collector_tail(name, num)
        if not any(ch.isdigit() for ch in num):
            # Ancient Mew 個號係 `Old`：唔係號，個名原封不動，唔准出 `… Movie Old`。
            if out != N.normalise_text(name):
                not_a_number_touched.append(f"{card.get('marketRank')}: {out}")
            continue
        if not out.casefold().endswith(num.casefold()):
            unfinished.append(f"{card.get('marketRank')}: {out}")
        tokens = out.split(" ")
        cores = [N.collector_core(t) for t in tokens[-2:]]
        if len(cores) == 2 and cores[0] and cores[0] == cores[1]:
            dupes.append(f"{card.get('marketRank')}: {out}")
    check(f"出街 {len(cards)} 張冇一張出雙號", dupes, [])
    check("出街每一張都以完整編號收尾", unfinished, [])
    check("冇數字嘅號一張都唔准加落卡名", not_a_number_touched, [])
    target = next(card for card in cards if card.get("id") == "cmc_f698284d7bc333408782e4c6")
    check("Latias & Latios 完整卡號", target["collectorNumber"]["display"], "170/181")
    check("Latias & Latios PSA 全名", target["officialName"].endswith("170/181"), True)
else:
    FAILED.append(f"FAIL 揾唔到 snapshot：{SNAPSHOT}")
    CHECKS += 1

for line in FAILED:
    print(line)
print(f"{CHECKS - len(FAILED)}/{CHECKS} checks passed")
raise SystemExit(1 if FAILED else 0)
