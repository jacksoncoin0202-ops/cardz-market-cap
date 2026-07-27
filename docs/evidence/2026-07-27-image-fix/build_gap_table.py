"""砌 gap-table.json：25 張卡逐張嘅結局 + 拒收理由。

輸入全部係同目錄嘅凍結副本（`missing22_work.json`、`clean_source_scan.json`…），
所以呢個檔今日重跑、下個月重跑，出嚟嘅表都一模一樣。上游三步
（`tcgplayer_lookup.py` → `fetch_candidates.py` → `scan_clean_sources.py`）
用 `temp/` 做草稿區，佢哋嘅產物就係呢度嘅凍結副本。

**已入庫嗰幾張唔喺呢個檔寫死 sha。** sha 由 `manifests/image-qc.json` 即場讀返，
所以表同真實庫狀態對唔上嘅時候，係表報錯，唔係表講大話。

`DECISIONS` 入面每一句拒收理由都係人手判斷嘅結果 —— 尺寸、水印、印次對唔對，
機器篩得出前兩樣，「係唔係同一個印次」要人眼對過美術圖先算。呢個 dict 就係
嗰批判斷嘅落地位，唔係喺 chat 講完就散。
"""
from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
QC_PATH = ROOT / "manifests" / "image-qc.json"

# 拒收理由代碼 —— 一句一個，唔好塞多過一個原因落同一個 code
REASONS = {
    "sample_watermark_all": "全部正確印次候選都有 Bandai 對角 SAMPLE 水印",
    "wrong_printing_only": "唯一無水印候選係另一個印次／另一套，唔係要嗰張",
    "language_mismatch": "圖係另一個語言版本，同 DB card_language 對唔上",
    "upscale_too_small": "候選細過 429x600 標準畫布，放大會糊",
}

# outcome: shipped_fill 新填 / shipped_swap 換走爛圖 / reverted 入完撤回 / blocked 冇合格圖
DECISIONS: dict[int, dict[str, object]] = {
    8: {
        "outcome": "shipped_swap",
        "opaqueId": "cmc_93789e346bbc157fac002457",
        "collector": "EB02-010",
        "name": "Monkey D. Luffy",
        "boards": ["op100", "tcg300"],
        "wasBefore": "卡封喺未開封卡包／膠套嘅照片，唔係卡面",
        "source": "https://tcgplayer-cdn.tcgplayer.com/product/641620_in_1000x1000.jpg",
        "sourceFacts": "625x873 / ratio 0.716 / 無水印 / 單卡卡面",
        "method": "human_confirmed_swap_tcgplayer_cdn",
    },
    111: {
        "outcome": "shipped_swap",
        "opaqueId": "cmc_f7587553086109d751e1a7c1",
        "collector": "ST10-006",
        "name": "Monkey D. Luffy",
        "boards": ["top100"],
        "wasBefore": "「NOW DESIGNING」官方佔位圖，根本冇卡面",
        "source": "https://tcgplayer-cdn.tcgplayer.com/product/557283_in_1000x1000.jpg",
        "sourceFacts": "500x701 / 無水印 / 單卡卡面",
        "method": "human_confirmed_swap_tcgplayer_cdn",
    },
    168: {
        "outcome": "shipped_fill",
        "collector": "024/020",
        "name": "Mew EX",
        "source": r"..\..\..\..\grade10-scraper\data\images\snkrdunk_91584.jpg",
        "sourceFacts": "711x1000 / ratio 0.711 / 無水印 / 日文版，同 DB card_language=ja 一致",
        "method": "human_confirmed_fill_local_snkrdunk",
    },
    35: {
        "outcome": "reverted",
        "collector": "153/SV-P",
        "name": "Pikachu",
        "source": r"..\..\..\..\grade10-scraper\data\images\snkrdunk_459741.webp",
        "reason": "language_mismatch",
        "detail": "圖上卡文係繁體中文（「電磁電光」「五週年」），DB card_language=ja。"
                  "入庫後喺覆核圖見到，即刻撤回 QC 記錄 —— 唔准為咗填數而報假 languageMatch。",
    },
    276: {
        "outcome": "blocked",
        "opaqueId": "cmc_a5cdc0db5df433edd719055f",
        "collector": "EB03-026",
        "name": "Boa Hancock",
        "note": "PM 追加項，唔喺原本 22 張缺圖名單。原圖有 SAMPLE 水印，屬「有圖但係爛圖」。",
        "reason": "sample_watermark_all",
        "detail": "三個 TCGplayer 候選全部帶 SAMPLE 水印；而且 DB card_language=ja，"
                  "英文版圖就算乾淨都會踩 language mismatch。兩重卡死，冇出。",
    },
}

# 20 張 One Piece：逐張寫「無水印候選係咩」同「點解唔要」
BLOCKED_OP: dict[int, tuple[str, str]] = {
    19: ("wrong_printing_only",
         "4 個無水印命中全部係 Scorched Battlefield（Battle Spirits Saga 促銷卡），"
         "同 Luffy 完全無關；正確印次 527027 / 529850 / 657798 全部 600x83x 水印模版"),
    24: ("wrong_printing_only",
         "唯一無水印 635479 係 English Version 2nd Anniversary 促銷版，唔係 Manga Alternate Art"),
    43: ("wrong_printing_only",
         "同 v24 撞同一個 635479（2nd Anniversary 促銷），目標係 3rd Anniversary Gold Foil"),
    60: ("wrong_printing_only",
         "唯一無水印 617591 係日文版 2nd Anniversary 促銷，目標係英文 Manga Alternate Art"),
    63: ("sample_watermark_all", "5 個候選全部 SAMPLE 水印模版尺寸，零無水印"),
    69: ("sample_watermark_all", "8 個候選全部 SAMPLE 水印模版尺寸，零無水印"),
    72: ("wrong_printing_only", "同 v24 撞同一個 635479 促銷版，目標係 A Fist of Divine Speed 本體"),
    78: ("wrong_printing_only",
         "2 個無水印係 Gift Collection 2023（523775）同 1st Anniversary Set（557286），"
         "兩個都唔係 Premium Booster the Best Manga Alternate Art"),
    102: ("sample_watermark_all", "7 個候選全部 SAMPLE 水印模版尺寸，零無水印"),
    103: ("upscale_too_small",
          "唯一無水印命中 453506（Romance Dawn Parallel）得 300x419，細過 429x600；"
          "而且落眼睇實際都帶水印 —— 尺寸啟發式喺呢張失效"),
    128: ("sample_watermark_all", "4 個候選全部 SAMPLE 水印模版尺寸，零無水印"),
    132: ("sample_watermark_all", "5 個候選全部 SAMPLE 水印模版尺寸，零無水印"),
    134: ("sample_watermark_all", "8 個候選全部 SAMPLE 水印模版尺寸，零無水印"),
    146: ("wrong_printing_only", "同 v24 撞同一個 635479 促銷版，目標係 English Manga Alt. Art"),
    163: ("sample_watermark_all", "4 個候選全部 SAMPLE 水印模版尺寸，零無水印"),
    171: ("sample_watermark_all", "7 個候選全部 SAMPLE 水印模版尺寸，零無水印"),
    175: ("sample_watermark_all", "8 個候選全部 SAMPLE 水印模版尺寸，零無水印"),
    188: ("upscale_too_small",
          "485235（Paramount War）300x419 太細；617587 係日文版 2nd Anniversary 促銷唔係目標印次"),
    192: ("sample_watermark_all", "5 個候選全部 SAMPLE 水印模版尺寸，零無水印"),
    197: ("sample_watermark_all", "3 個候選全部 SAMPLE 水印模版尺寸，零無水印"),
}


def qc_index() -> dict[str, dict]:
    """publicId -> 最後生效嗰條 QC 記錄（同 load_public_images 一樣後蓋前）。"""
    document = json.loads(QC_PATH.read_text(encoding="utf-8"))
    index: dict[str, dict] = {}
    for record in document["records"]:
        index[record["publicId"]] = record
    return index


def main() -> int:
    work = {card["variantId"]: card for card in
            json.loads((HERE / "missing22_work.json").read_text(encoding="utf-8"))}
    scan = {row["variantId"]: row for row in
            json.loads((HERE / "clean_source_scan.json").read_text(encoding="utf-8"))}
    qc = qc_index()

    rows: list[dict[str, object]] = []
    for vid in sorted(set(work) | set(DECISIONS)):
        card = work.get(vid, {})
        decided = DECISIONS.get(vid, {})
        opaque = card.get("opaqueId") or decided.get("opaqueId")
        row: dict[str, object] = {
            "variantId": vid,
            "opaqueId": opaque,
            "collector": card.get("collector") or decided.get("collector"),
            "name": card.get("name") or decided.get("name"),
            "set": card.get("set"),
            "boards": (card.get("boards", "").split("|") if card.get("boards")
                       else decided.get("boards")),
            "inOriginalMissing22": vid in work,
        }

        if vid in DECISIONS:
            row["outcome"] = decided["outcome"]
            for key in ("wasBefore", "source", "sourceFacts", "method", "note", "detail"):
                if key in decided:
                    row[key] = decided[key]
            if "reason" in decided:
                row["reasonCode"] = decided["reason"]
                row["reason"] = REASONS[decided["reason"]]
        else:
            code, detail = BLOCKED_OP[vid]
            row["outcome"] = "blocked"
            row["reasonCode"] = code
            row["reason"] = REASONS[code]
            row["detail"] = detail

        if vid in work:
            row["localBytes"] = card["localFile"]
            row["localKind"] = ("評級殼拍賣相（結構上出唔到卡面）"
                               if card["localFile"].startswith("altxyz_")
                               else "商城卡面圖（可用）")
        if vid in scan:
            row["tcgCandidatesMeasured"] = scan[vid]["totalHits"]
            row["tcgNoWatermarkHits"] = scan[vid]["likelyRealScanCount"]
            row["tcgIdentifiedProductIds"] = [f["productId"] for f in scan[vid]["all"]]

        # sha 唔喺呢個檔寫死，即場對返 manifests/image-qc.json
        if opaque:
            record = qc.get(opaque)
            row["liveSha256"] = record["contentSha256"] if record else None
            row["liveSemanticMatchStatus"] = record["semanticMatchStatus"] if record else None
            row["inProducerManifest"] = bool(record)
        rows.append(row)

    tally: dict[str, int] = {}
    for row in rows:
        tally[str(row["outcome"])] = tally.get(str(row["outcome"]), 0) + 1

    payload = {
        "measuredAt": "2026-07-27",
        "measuredBy": "opus-image-fix",
        "cardCount": len(rows),
        "outcomeTally": tally,
        "reasonCodes": REASONS,
        "cards": rows,
    }
    target = HERE / "gap-table.json"
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(json.dumps(tally, ensure_ascii=False))
    for row in rows:
        if row["outcome"] in ("shipped_fill", "shipped_swap"):
            print(f"  v{row['variantId']:<4} {row['collector']:<11} {row['outcome']:<13} "
                  f"live={str(row.get('liveSha256'))[:16]} inManifest={row.get('inProducerManifest')}")
    print(f"寫入 {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
